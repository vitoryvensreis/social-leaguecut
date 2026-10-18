"""Google Drive OAuth and controlled media import."""
import os, io, re, secrets, logging
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from cryptography.fernet import Fernet

router=APIRouter()
ROOT=Path(__file__).resolve().parent.parent
MEDIA=ROOT/"media"
SCOPES=["https://www.googleapis.com/auth/drive.readonly"]
logger=logging.getLogger("social.drive")

def flow(state=None, code_verifier=None):
    """Build the same OAuth flow before and after Google's redirect.

    Explicitly persist the PKCE verifier in the signed login session.
    """
    config={"web":{
        "client_id":os.environ["GOOGLE_CLIENT_ID"],
        "client_secret":os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri":"https://accounts.google.com/o/oauth2/auth",
        "token_uri":"https://oauth2.googleapis.com/token",
        "redirect_uris":[os.environ["GOOGLE_REDIRECT_URI"]]
    }}
    kwargs={"scopes":SCOPES,
            "redirect_uri":os.environ["GOOGLE_REDIRECT_URI"],
            "autogenerate_code_verifier":False}
    if state is not None:
        kwargs["state"]=state
    if code_verifier is not None:
        kwargs["code_verifier"]=code_verifier
    return Flow.from_client_config(config,**kwargs)

def crypt():
    return Fernet(os.environ["PROXY_ENCRYPTION_KEY"].encode())

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_google_tokens(id TINYINT PRIMARY KEY, token_enc MEDIUMTEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_media(id INT AUTO_INCREMENT PRIMARY KEY, drive_id VARCHAR(160) NOT NULL UNIQUE, filename VARCHAR(255) NOT NULL, path VARCHAR(400) NOT NULL, size_bytes BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    db.commit()

def creds(db):
    init(db)
    with db.cursor() as c:
        c.execute("SELECT token_enc FROM social_google_tokens WHERE id=1")
        row=c.fetchone()
    return Credentials.from_authorized_user_info(__import__("json").loads(crypt().decrypt(row["token_enc"].encode()))) if row else None

def store(db, credentials):
    encoded=crypt().encrypt(credentials.to_json().encode()).decode()
    with db.cursor() as c:
        c.execute("INSERT INTO social_google_tokens(id,token_enc) VALUES(1,%s) ON DUPLICATE KEY UPDATE token_enc=VALUES(token_enc)",(encoded,))
    db.commit()

def check(request,csrf):
    from app.main import admin,form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+quote(message),303)

@router.post("/drive/connect")
def connect(request:Request,csrf:str=Form(...)):
    if not check(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    try:
        verifier=secrets.token_urlsafe(64)
        f=flow(code_verifier=verifier)
        url,state=f.authorization_url(access_type="offline",prompt="consent")
        request.session["google_oauth_state"]=state
        request.session["google_oauth_pkce"]=verifier
        return RedirectResponse(url,status_code=303)
    except KeyError:
        return go("Google Drive: faltam configurações OAuth no servidor")
    except Exception as exc:
        logger.warning("Drive authorization setup failed: %s",type(exc).__name__)
        return go("Google Drive: erro ao iniciar autorização. Confira Client ID e URI cadastrada.")

@router.get("/drive/callback")
def callback(request:Request,code:str="",state:str="",error:str=""):
    from app.main import admin,db_connection
    expected_state=request.session.pop("google_oauth_state",None)
    verifier=request.session.pop("google_oauth_pkce",None)
    if not admin(request) or not state or state!=expected_state:
        return JSONResponse({"error":"Estado OAuth inválido. Faça login e reconecte o Drive."},status_code=403)
    if error:
        logger.warning("Drive authorization refused: %s", "access_denied" if error=="access_denied" else "other")
        return go("O Google não autorizou o acesso ao Drive.")
    if not code or not verifier:
        return go("Google Drive: código ou verificador PKCE ausente. Reconecte o Drive.")
    stage="token_exchange"
    try:
        f=flow(state=state,code_verifier=verifier)
        f.fetch_token(code=code)
        credentials=f.credentials
        stage="database_storage"
        with db_connection() as db:
            init(db)
            store(db,credentials)
        return go("Google Drive conectado")
    except Exception as exc:
        category=getattr(exc,"error",None)
        category=category if category in {"invalid_grant","invalid_client","access_denied","redirect_uri_mismatch","invalid_request"} else type(exc).__name__
        logger.warning("Drive connection failed stage=%s error=%s",stage,category)
        if stage=="token_exchange":
            return go("Google Drive: falha ao trocar autorização por token ("+category+"). Refazer conexão.")
        return go("Google Drive: autorização recebida, mas erro ao salvar no MySQL ("+category+").")

@router.get("/drive/files")
def files(request:Request):
    from app.main import admin,db_connection
    if not admin(request):return JSONResponse({"error":"Forbidden"},403)
    try:
        with db_connection() as db:
            c=creds(db)
            if not c:return JSONResponse({"files":[],"connected":False})
            api=build("drive","v3",credentials=c,cache_discovery=False)
            data=api.files().list(q="trashed = false and (mimeType = 'video/mp4' or mimeType = 'video/quicktime')",fields="nextPageToken,files(id,name,mimeType,size)",page_size=100).execute()
            store(db,c)
        return JSONResponse({"files":data.get("files",[]),"connected":True})
    except Exception:
        return JSONResponse({"error":"Drive indisponível ou sessão expirada"},503)

@router.post("/drive/import")
def import_file(request:Request,csrf:str=Form(...),file_id:str=Form(...)):
    from app.main import db_connection
    if not check(request,csrf):return JSONResponse({"error":"Forbidden"},403)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{10,160}",file_id):return go("ID inválido")
    try:
        with db_connection() as db:
            c=creds(db)
            if not c:return go("Conecte o Drive primeiro")
            api=build("drive","v3",credentials=c,cache_discovery=False)
            meta=api.files().get(fileId=file_id,fields="id,name,mimeType,size").execute()
            if meta.get("mimeType") not in ("video/mp4","video/quicktime"):return go("Formato de vídeo não permitido")
            size=int(meta.get("size",0))
            if size<=0 or size>500*1024*1024:return go("Arquivo deve ter no máximo 500 MB")
            MEDIA.mkdir(mode=0o700,exist_ok=True)
            destination=MEDIA/(file_id+".mp4")
            temp=MEDIA/(file_id+".part")
            try:
                with open(temp,"wb") as out:
                    downloader=MediaIoBaseDownload(out,api.files().get_media(fileId=file_id),chunksize=2*1024*1024)
                    done=False
                    while not done:
                        _,done=downloader.next_chunk()
                        if out.tell()>500*1024*1024:raise ValueError("Tamanho excedido")
                temp.replace(destination)
            finally:
                temp.unlink(missing_ok=True)
            init(db)
            with db.cursor() as cur:
                cur.execute("INSERT INTO social_media(drive_id,filename,path,size_bytes) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE filename=VALUES(filename),path=VALUES(path),size_bytes=VALUES(size_bytes)",(file_id,meta["name"],str(destination),destination.stat().st_size))
            db.commit()
            store(db,c)
        return go("Vídeo importado: "+meta["name"][:80])
    except Exception:
        return go("Falha ao importar arquivo. Verifique armazenamento, permissões e acesso Drive.")
