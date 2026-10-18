"""Secure bulk entry. No credentials are embedded in source or git.

Paste directly into the authenticated HTTPS dashboard; encrypt Instagram
passwords before storing in MySQL. Never log credential payloads.
"""
import csv
import io
import re
from urllib.parse import quote
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from cryptography.fernet import Fernet
from app.proxies import init as proxy_init, public_host, crypt as proxy_crypt
from app.routing import auto_assign_proxy
from app.instagram import init as instagram_init, cipher as instagram_cipher

router=APIRouter()

def go(message):
    return RedirectResponse("/?error="+quote(message),status_code=303)

def allowed(request,csrf):
    from app.main import admin,form_allowed
    return admin(request) and form_allowed(request,csrf)

def csv_rows(value,maximum=100):
    if len(value)>60000:
        raise ValueError("Arquivo muito grande")
    lines=[line for line in value.splitlines() if line.strip()]
    if len(lines)>maximum+1:
        raise ValueError("Muitas linhas")
    if not lines:
        raise ValueError("Sem linhas")
    return lines

def parse_proxy_rows(raw):
    rows=[]
    for line in csv_rows(raw,100):
        if line.lower().startswith(("proxy address","address,","host,","host\t")):
            continue
        # Webshare supports CSV and IP:PORT:USER:PASS text exports.
        if "\t" in line:
            cols=line.split("\t")
        elif line.count(":")>=3 and "," not in line:
            cols=line.split(":",3)
        else:
            cols=next(csv.reader([line],skipinitialspace=True))
        if len(cols)<4:
            raise ValueError("Proxy precisa de host,porta,usuario,senha")
        host,port,user,password=[v.strip() for v in cols[:4]]
        if host.lower() in ("host","proxy address") or not port.isdigit():
            continue
        port=int(port)
        if not (1<=port<=65535 and public_host(host)
                and len(user)<=200 and len(password)<=200):
            raise ValueError("Host ou porta inválidos")
        rows.append((host,port,user,password))
    return list({(host,port):(host,port,u,p) for host,port,u,p in rows}.values())

def parse_account_rows(raw):
    parsed=[]
    for line in csv_rows(raw,150):
        cols=next(csv.reader([line],delimiter="\t")) if "\t" in line else next(csv.reader([line]))
        if not cols:
            continue
        name=cols[0].strip().lstrip("@").lower()
        if name in ("username","usuario","usuário","perfil"):
            continue
        if not re.fullmatch(r"[a-z0-9._]{1,30}",name):
            raise ValueError("Usuário Instagram inválido")
        email=cols[1].strip() if len(cols)>1 else ""
        password=cols[2].strip() if len(cols)>2 else ""
        if len(email)>255 or len(password)>256:
            raise ValueError("Campo muito longo")
        parsed.append((name,email,password))
    return list({name:(name,email,password) for name,email,password in parsed}.values())

@router.post("/bulk/proxies")
def import_proxies(request:Request,csrf:str=Form(...),lines:str=Form(...)):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    if not __import__("os").environ.get("PROXY_ENCRYPTION_KEY"):
        return go("Chave de criptografia Webshare ausente")
    try:
        rows=parse_proxy_rows(lines)
    except ValueError:
        return go("Formato inválido. Use IP:PORTA:USUARIO:SENHA ou CSV/TSV.")
    added=0
    with db_connection() as db:
        proxy_init(db)
        with db.cursor() as c:
            for host,port,user,password in rows:
                c.execute("SELECT id FROM social_proxies WHERE host=%s AND port=%s",(host,port))
                if c.fetchone():
                    continue
                secret=proxy_crypt()
                c.execute("""INSERT INTO social_proxies(label,host,port,user_secret,pass_secret)
                    VALUES(%s,%s,%s,%s,%s)""",
                    (f"Webshare {host[-8:]}",host,port,
                     secret.encrypt(user.encode()).decode(),
                     secret.encrypt(password.encode()).decode()))
                added+=1
        db.commit()
    return go(f"Importação de proxies concluída: {added} novos. Use Testar todos.")

@router.post("/bulk/accounts")
def import_accounts(request:Request,csrf:str=Form(...),lines:str=Form(...)):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    try:
        rows=parse_account_rows(lines)
    except ValueError:
        return go("Formato inválido. Cole colunas usuário, email e senha (separadas por tabulação ou vírgula).")
    if any(pw for _,_,pw in rows) and not __import__("os").environ.get("INSTAGRAM_ENCRYPTION_KEY"):
        return go("Configure INSTAGRAM_ENCRYPTION_KEY antes de importar senhas.")
    added=0
    queued=[]
    with db_connection() as db:
        instagram_init(db)
        with db.cursor() as c:
            c.execute("SHOW COLUMNS FROM social_accounts LIKE 'email'")
            if not c.fetchone():
                c.execute("ALTER TABLE social_accounts ADD COLUMN email VARCHAR(255) NULL")
            for username,email,password in rows:
                c.execute("SELECT id,status FROM social_accounts WHERE username=%s",(username,))
                current=c.fetchone()
                if current:
                    account_id=current["id"]
                else:
                    c.execute("INSERT INTO social_accounts(username,email) VALUES(%s,%s)",(username,email or None))
                    account_id=c.lastrowid
                    added+=1
                    queued.append(account_id)
                if email and current:
                    c.execute("UPDATE social_accounts SET email=%s WHERE id=%s",(email,account_id))
                if password:
                    c.execute("SELECT session_blob FROM social_instagram_auth WHERE account_id=%s",(account_id,))
                    existing=c.fetchone()
                    if existing and existing["session_blob"]:
                        continue  # Preserve existing authenticated session
                    encrypted=instagram_cipher().encrypt(password.encode()).decode()
                    c.execute("""INSERT INTO social_instagram_auth
                         (account_id,encrypted_password,session_blob,proxy_id)
                         VALUES(%s,%s,'',0)
                         ON DUPLICATE KEY UPDATE encrypted_password=VALUES(encrypted_password)""",
                         (account_id,encrypted))
        db.commit()
        for account_id in queued:
            try:
                auto_assign_proxy(db,account_id)
            except Exception:
                # Proxy can be assigned later, without losing imported accounts.
                pass
    return go(f"Importação de perfis concluída: {added} novos. Contas não foram logadas automaticamente.")

@router.post("/bulk/proxies/remove-failed")
def remove_failed(request:Request,csrf:str=Form(...),confirmed:str=Form("")):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if confirmed!="yes":
        return go("Confirmação obrigatória para remover proxies falhos.")
    from app.main import db_connection
    with db_connection() as db:
        proxy_init(db)
        with db.cursor() as c:
            c.execute("""DELETE p FROM social_proxies p
               LEFT JOIN social_account_routes r ON r.proxy_id=p.id
               LEFT JOIN social_instagram_auth a ON a.proxy_id=p.id
               WHERE p.last_result='failed' AND r.proxy_id IS NULL
                 AND a.account_id IS NULL""")
            removed=c.rowcount
        db.commit()
    return go(f"{removed} proxies falhos e sem vínculos removidos. Contas não foram alteradas.")

@router.post("/bulk/accounts/remove-failed")
def remove_failed_accounts(request:Request,csrf:str=Form(...),confirmed:str=Form("")):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if confirmed!="yes":
        return go("Confirmação obrigatória antes de remover contas.")
    from app.main import db_connection
    from app.campaign import init as campaign_init
    from app.routing import init as routing_init
    with db_connection() as db:
        instagram_init(db)
        routing_init(db)
        campaign_init(db)
        with db.cursor() as c:
            c.execute("""SELECT a.id FROM social_accounts a
                WHERE a.status='failed'
                  AND NOT EXISTS(SELECT 1 FROM social_daily_jobs j
                    WHERE j.account_id=a.id AND j.status IN ('published','publishing'))""")
            identifiers=[row["id"] for row in c.fetchall()]
            for account_id in identifiers:
                c.execute("DELETE FROM social_daily_jobs WHERE account_id=%s",(account_id,))
                c.execute("DELETE FROM social_post_accounts WHERE account_id=%s",(account_id,))
                c.execute("DELETE FROM social_instagram_auth WHERE account_id=%s",(account_id,))
                c.execute("DELETE FROM social_account_routes WHERE account_id=%s",(account_id,))
                c.execute("DELETE FROM social_accounts WHERE id=%s",(account_id,))
        db.commit()
    return go(f"{len(identifiers)} contas falhas removidas após confirmação.")
