"""Google Drive OAuth and controlled media import."""
import os, io, re
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

def flow():
    return Flow.from_client_config({"web":{"client_id":os.environ["GOOGLE_CLIENT_ID"],"client_secret":os.environ["GOOGLE_CLIENT_SECRET"],"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[os.environ["GOOGLE_REDIRECT_URI"]]}},scopes=SCOPES,redirect_uri=os.environ["GOOGLE_REDIRECT_URI"])

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
    if not check(request,csrf): return JSONResponse({"error":"Forbidden"},403)
    try:
        f=flow()
        url,state=f.authorization_url(access_type="offline",prompt="consent",include_granted_scopes="true")
        request.session["google_oauth_state"]=state
        return RedirectResponse(url,303)
    except KeyError:
        return go("Configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI na VPS")

@router.get("/drive/callback")
def callback(request:Request,code:str="",state:str=""):
    from app.main import admin,db_connection
    if not admin(request) or not state or state!=request.session.pop("google_oauth_state",None):return JSONResponse({"error":"OAuth state inválido"},403)
    try:
        f=flow()
        f.fetch_token(code=code)
        with db_connection() as db: init(db);store(db,f.credentials)
        return go("Google Drive conectado")
    except Exception:
        return go("Não foi possível conectar Google Drive. Confira as credenciais OAuth.")

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
