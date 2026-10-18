"""Authenticated Instagram connection, with encrypted credentials and sessions.

This module only connects accounts. It does NOT publish media, bypass login
challenges, rotate IPs, or fall back to the VPS address.
"""
import json
import logging
import os
import urllib.parse
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from app.proxies import public_host

logger=logging.getLogger("social.instagram")
router=APIRouter()

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_instagram_auth (
            account_id INT PRIMARY KEY,
            encrypted_password TEXT NOT NULL,
            session_blob LONGTEXT NOT NULL,
            proxy_id INT NOT NULL,
            last_verified DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_error VARCHAR(80) DEFAULT NULL
        )""")
    db.commit()

def cipher():
    key=os.environ.get("INSTAGRAM_ENCRYPTION_KEY","")
    if not key:
        raise ValueError("Instagram encryption not configured")
    return Fernet(key.encode())

def proxy_for_account(db,account_id:int):
    with db.cursor() as c:
        c.execute("""SELECT r.proxy_id,p.host,p.port,p.user_secret,p.pass_secret,p.last_result
          FROM social_account_routes r JOIN social_proxies p ON p.id=r.proxy_id
          WHERE r.account_id=%s""",(account_id,))
        row=c.fetchone()
    if not row or row["last_result"]!="ok" or not public_host(row["host"]):
        raise ValueError("Proxy fixo indisponível ou não testado")
    from app.proxies import crypt
    f=crypt()
    user=urllib.parse.quote(f.decrypt(row["user_secret"].encode()).decode(),safe="")
    pw=urllib.parse.quote(f.decrypt(row["pass_secret"].encode()).decode(),safe="")
    authority=f"{user}:{pw}@" if user else ""
    return row["proxy_id"],f"http://{authority}{row['host']}:{row['port']}"

def failure_reason(exc):
    name=type(exc).__name__
    if name in ("TwoFactorRequired","ChallengeRequired","ChallengeError","ChallengeUnknownStep"):
        return "needs_verification","Instagram exige verificação. Resolva pelo aplicativo e tente novamente."
    if name in ("FeedbackRequired","PleaseWaitFewMinutes","RateLimitError","ClientThrottledError"):
        return "paused","Instagram limitou o acesso. Aguarde e evite novas tentativas."
    if name in ("BadPassword","LoginRequired","ClientLoginRequired","ClientError","ClientLoginError"):
        return "failed","Instagram recusou o login. Confira credenciais e aprovações no aplicativo."
    if isinstance(exc,(ConnectionError,TimeoutError,OSError)):
        return "paused","Falha de conexão. Confira a disponibilidade do proxy fixo."
    return "failed","Falha na autenticação. Consulte o aplicativo Instagram antes de tentar novamente."

def allowed(request,csrf):
    from app.main import admin, form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+urllib.parse.quote(message),status_code=303)

@router.post("/instagram/accounts/{account_id}/connect")
def connect(request:Request, account_id:int, csrf:str=Form(...),
            password:str=Form(...), verification_code:str=Form("")):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if not os.environ.get("INSTAGRAM_ENCRYPTION_KEY"):
        return go("Configure INSTAGRAM_ENCRYPTION_KEY na VPS antes de conectar perfis.")
    if not password or len(password)>256 or len(verification_code)>10 or (
        verification_code and (not verification_code.isascii() or not verification_code.isdigit())
    ):
        return go("Senha ou código de verificação inválido.")
    from app.main import db_connection
    from instagrapi import Client
    try:
        with db_connection() as db:
            init(db)
            with db.cursor() as c:
                c.execute("SELECT username FROM social_accounts WHERE id=%s",(account_id,))
                account=c.fetchone()
                c.execute("SELECT session_blob,proxy_id FROM social_instagram_auth WHERE account_id=%s",(account_id,))
                saved=c.fetchone()
            if not account:
                return go("Perfil não encontrado no painel.")
            try:
                proxy_id,proxy=proxy_for_account(db,account_id)
            except (ValueError,InvalidToken):
                return go("Esse perfil precisa de um proxy aprovado e disponível.")
            client=Client()
            if saved and saved["proxy_id"]==proxy_id and saved["session_blob"]:
                try:
                    client.set_settings(json.loads(cipher().decrypt(saved["session_blob"].encode())))
                except (InvalidToken,ValueError,TypeError):
                    return go("Sessão criptografada inválida. Revise a chave do Instagram na VPS.")
            client.set_proxy(proxy)
            try:
                if verification_code:
                    client.login(account["username"],password,verification_code=verification_code)
                else:
                    client.login(account["username"],password)
                if not client.user_id:
                    raise ValueError("Login without user ID")
                session=client.get_settings()
                encrypted_settings=cipher().encrypt(json.dumps(session,ensure_ascii=False).encode()).decode()
                encrypted_password=cipher().encrypt(password.encode()).decode()
                with db.cursor() as c:
                    c.execute("""INSERT INTO social_instagram_auth(account_id,encrypted_password,
                              session_blob,proxy_id,last_verified,last_error)
                            VALUES(%s,%s,%s,%s,UTC_TIMESTAMP(),NULL)
                            ON DUPLICATE KEY UPDATE encrypted_password=VALUES(encrypted_password),
                              session_blob=VALUES(session_blob),proxy_id=VALUES(proxy_id),
                              last_verified=VALUES(last_verified),last_error=NULL""",
                              (account_id,encrypted_password,encrypted_settings,proxy_id))
                    c.execute("UPDATE social_accounts SET status='connected' WHERE id=%s",(account_id,))
                db.commit()
                return go("Conta @"+account["username"]+" conectada e sessão criptografada salva.")
            except Exception as exc:
                status,message=failure_reason(exc)
                logger.warning("Instagram login account_id=%s outcome=%s error_class=%s",
                               account_id,status,type(exc).__name__)
                with db.cursor() as c:
                    c.execute("UPDATE social_accounts SET status=%s WHERE id=%s",(status,account_id))
                db.commit()
                return go(message)
    except Exception as exc:
        logger.warning("Instagram connection setup failed error_class=%s",type(exc).__name__)
        return go("Erro interno de conexão. Consulte os logs sem compartilhar credenciais.")

@router.post("/instagram/accounts/{account_id}/disconnect")
def disconnect(request:Request,account_id:int,csrf:str=Form(...)):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("DELETE FROM social_instagram_auth WHERE account_id=%s",(account_id,))
            c.execute("UPDATE social_accounts SET status='disconnected' WHERE id=%s",(account_id,))
        db.commit()
    return go("Credenciais e sessão removidas deste perfil no Social LeagueCut.")
