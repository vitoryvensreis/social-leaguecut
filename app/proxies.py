import os, json, ipaddress, socket, urllib.request, urllib.parse
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter()

def crypt():
    return Fernet(os.environ["PROXY_ENCRYPTION_KEY"].encode())

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_proxies(
            id INT AUTO_INCREMENT PRIMARY KEY, label VARCHAR(80) NOT NULL,
            host VARCHAR(255) NOT NULL, port INT NOT NULL,
            user_secret TEXT, pass_secret TEXT, active BOOLEAN DEFAULT 0,
            last_result VARCHAR(20) DEFAULT 'untested',
            last_ip VARCHAR(64), checked_at DATETIME
        )""")
    db.commit()

def available(db):
    init(db)
    with db.cursor() as c:
        c.execute("SELECT id,label,host,port,active,last_result,last_ip,checked_at FROM social_proxies ORDER BY id DESC")
        return c.fetchall()

def allowed(request,csrf):
    from app.main import admin, form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+urllib.parse.quote(message),status_code=303)

def public_host(host):
    if not host or len(host)>255 or any(c in host for c in '/:@?# \\'):
        return False
    try:
        records=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)
        return bool(records) and all(ipaddress.ip_address(r[4][0]).is_global for r in records)
    except (OSError,ValueError):
        return False

@router.post("/proxies/add")
def add(request:Request,csrf:str=Form(...),label:str=Form(...),host:str=Form(...),port:int=Form(...),username:str=Form(""),password:str=Form("")):
    if not allowed(request,csrf):return JSONResponse({"error":"Forbidden"},status_code=403)
    host=host.strip()
    if not 1<=port<=65535 or not label.strip() or len(label)>80 or not public_host(host) or len(username)>200 or len(password)>200:
        return go("Dados do proxy inválidos. Utilize host público.")
    from app.main import db_connection
    try:
        with db_connection() as db:
            init(db)
            with db.cursor() as c:
                c.execute("INSERT INTO social_proxies(label,host,port,user_secret,pass_secret) VALUES(%s,%s,%s,%s,%s)",(label.strip(),host,port,crypt().encrypt(username.encode()).decode(),crypt().encrypt(password.encode()).decode()))
            db.commit()
    except Exception:
        return go("Não foi possível salvar. Configure PROXY_ENCRYPTION_KEY no servidor.")
    return go("Proxy cadastrado. Teste antes de ativar.")

@router.post("/proxies/{proxy_id}/test")
def test(request:Request,proxy_id:int,csrf:str=Form(...)):
    if not allowed(request,csrf):return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT * FROM social_proxies WHERE id=%s",(proxy_id,))
            p=c.fetchone()
        if not p or not public_host(p["host"]):return go("Proxy não encontrado ou host inválido")
        try:
            u=urllib.parse.quote(crypt().decrypt(p["user_secret"].encode()).decode(),safe="")
            pw=urllib.parse.quote(crypt().decrypt(p["pass_secret"].encode()).decode(),safe="")
            proxy=f'http://{u}:{pw}@{p["host"]}:{p["port"]}' if u else f'http://{p["host"]}:{p["port"]}'
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({"http":proxy,"https":proxy}))
            with opener.open("https://api.ipify.org?format=json",timeout=10) as r:
                ip=str(ipaddress.ip_address(json.load(r)["ip"]))
            status="ok"
            msg="Proxy conectado! IP de saída: "+ip
        except Exception:
            ip=None
            status="failed"
            msg="Falha no teste. Verifique porta, autenticação, limite e protocolo."
        with db.cursor() as c:
            c.execute("UPDATE social_proxies SET last_result=%s,last_ip=%s,checked_at=NOW(),active=IF(%s='ok',active,0) WHERE id=%s",(status,ip,status,proxy_id))
        db.commit()
    return go(msg)

@router.post("/proxies/{proxy_id}/activate")
def activate(request:Request,proxy_id:int,csrf:str=Form(...)):
    if not allowed(request,csrf):return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT last_result FROM social_proxies WHERE id=%s",(proxy_id,))
            result=c.fetchone()
            if not result or result["last_result"]!="ok":return go("Teste este proxy antes de ativar")
            c.execute("UPDATE social_proxies SET active=0")
            c.execute("UPDATE social_proxies SET active=1 WHERE id=%s",(proxy_id,))
        db.commit()
    return go("Proxy selecionado. Ainda não há rotação automática nas sessões Instagram.")

@router.post("/proxies/{proxy_id}/delete")
def delete(request:Request,proxy_id:int,csrf:str=Form(...)):
    if not allowed(request,csrf):return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        with db.cursor() as c:
            c.execute("DELETE FROM social_proxies WHERE id=%s",(proxy_id,))
        db.commit()
    return go("Proxy excluído.")
