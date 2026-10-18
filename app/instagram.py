"""Authenticated Instagram connection, with encrypted credentials and sessions.

This module only connects accounts. It does NOT publish media, bypass login
challenges, rotate IPs, or fall back to the VPS address.
"""
import json
import logging
import os
import re
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
        c.execute("""CREATE TABLE IF NOT EXISTS social_ig_diagnostics (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            account_id INT NOT NULL,
            stage VARCHAR(24) NOT NULL,
            category VARCHAR(32) NOT NULL,
            exception_type VARCHAR(80) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_account_created(account_id,created_at)
        )""")
    db.commit()

def record_failure(db, account_id:int, exc:Exception, stage:str, status:str):
    """Persist safe error *types* only; never raw exceptions, passwords or tokens."""
    error_name=re.sub(r"[^A-Za-z0-9_]","",type(exc).__name__)[:80] or "Unknown"
    with db.cursor() as c:
        c.execute("""INSERT INTO social_ig_diagnostics
                (account_id,stage,category,exception_type) VALUES(%s,%s,%s,%s)""",
                (account_id,stage,status,error_name))

def diagnostics(db,limit=25):
    init(db)
    with db.cursor() as c:
        c.execute("""SELECT e.stage,e.category,e.exception_type,e.created_at,a.username
             FROM social_ig_diagnostics e JOIN social_accounts a ON a.id=e.account_id
             ORDER BY e.id DESC LIMIT %s""",(limit,))
        return c.fetchall()

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
    """Classify without exposing exception text or triggering automatic retries."""
    name=type(exc).__name__
    if name == "UnknownError":
        code=str(getattr(exc,"error_type","") or "").lower()
        category=str(getattr(exc,"message_category","") or "").lower()
        message=str(getattr(exc,"message","") or "").lower()
        if code=="needs_upgrade" or category=="app_out_of_date" or "out of date" in message:
            return "paused","Instagram sinalizou versão de aplicativo desatualizada. Verifique a versão do Instagrapi; não repita logins."
        return "paused","Resposta não classificada da API privada. Verifique a versão do Instagrapi e aguarde antes de tentar novamente."
    if name in ("TwoFactorRequired","ChallengeRequired","ChallengeError",
                "ChallengeUnknownStep","CheckpointRequired","ConsentRequired"):
        return "needs_verification","Instagram exige verificação. Confira o aplicativo antes de tentar novamente."
    if name in ("FeedbackRequired","PleaseWaitFewMinutes","RateLimitError",
                "ClientThrottledError","ClientForbiddenError","ClientUnauthorizedError",
                "ClientUnpredictableError"):
        return "paused","Instagram limitou ou recusou o acesso. Não tente novamente em massa."
    if name in ("BadPassword","IncorrectPassword","InvalidCredentials",
                "UserNotFound","UserNotFoundError"):
        return "failed","Credenciais ou usuário recusados. Confira o login no aplicativo Instagram."
    if name in ("ProxyError","InvalidProxy","ProxyConnectionError","ConnectTimeout",
                "ReadTimeout","Timeout","SSLError","ClientConnectionError",
                "ClientRequestTimeout","ConnectionError") or isinstance(exc, (ConnectionError,TimeoutError,OSError)):
        return "paused","Falha de conexão com Instagram pelo proxy fixo. Verifique rede e proxy sem forçar login."
    if name in ("ClientJSONDecodeError","JSONDecodeError","ClientLoginError",
                "ClientError","ClientBadRequestError","ClientUnknownError",
                "ValueError","KeyError"):
        return "paused","Resposta inesperada do Instagram ou incompatibilidade da API privada. Verifique diagnóstico técnico."
    return "paused","Falha de origem não identificada. Tentativas pausadas; consulte o diagnóstico técnico."

def allowed(request,csrf):
    from app.main import admin, form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+urllib.parse.quote(message),status_code=303)

@router.post("/instagram/accounts/{account_id}/connect")
def connect(request:Request, account_id:int, csrf:str=Form(...),
            password:str=Form(""), verification_code:str=Form("")):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if not os.environ.get("INSTAGRAM_ENCRYPTION_KEY"):
        return go("Configure INSTAGRAM_ENCRYPTION_KEY na VPS antes de conectar perfis.")
    if len(password)>256 or len(verification_code)>10 or (
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
                c.execute("SELECT session_blob,proxy_id,encrypted_password FROM social_instagram_auth WHERE account_id=%s",(account_id,))
                saved=c.fetchone()
            if not account:
                return go("Perfil não encontrado no painel.")
            if not password and saved and saved["encrypted_password"]:
                try:
                    password=cipher().decrypt(saved["encrypted_password"].encode()).decode()
                except (InvalidToken,ValueError):
                    return go("Senha criptografada inválida. Reimporte pelo painel.")
            if not password:
                return go("Importe uma senha para o perfil antes do teste.")

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
                record_failure(db,account_id,exc,"connect",status)
                db.commit()
                return go(message)
    except Exception as exc:
        logger.warning("Instagram connection setup failed error_class=%s",type(exc).__name__)
        return go("Erro interno de conexão. Consulte os logs sem compartilhar credenciais.")

@router.post("/instagram/accounts/{account_id}/check")
def check_account(request:Request,account_id:int,csrf:str=Form(...)):
    """One authenticated check at a time; cooldown prevents repeated logins."""
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from datetime import datetime,timedelta
    from app.main import db_connection
    from instagrapi import Client
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SHOW COLUMNS FROM social_instagram_auth LIKE 'last_healthcheck'")
            if not c.fetchone():
                c.execute("ALTER TABLE social_instagram_auth ADD COLUMN last_healthcheck DATETIME NULL")
            c.execute("""SELECT a.username,a.status,auth.encrypted_password,auth.session_blob,auth.last_healthcheck
                FROM social_accounts a LEFT JOIN social_instagram_auth auth ON a.id=auth.account_id
                WHERE a.id=%s""",(account_id,))
            row=c.fetchone()
        db.commit()
    if not row:
        return JSONResponse({"status":"missing","message":"Conta inexistente"},status_code=404)
    if row["status"] in ("needs_verification","paused"):
        return JSONResponse({"status":row["status"],"message":"Conta pausada: resolva no aplicativo Instagram."})
    if row["last_healthcheck"] and datetime.utcnow()-row["last_healthcheck"]<timedelta(minutes=10):
        return JSONResponse({"status":"recent","message":"Já testada recentemente; nova tentativa evitada."})
    if not row["encrypted_password"]:
        return JSONResponse({"status":"not_connected","message":"Cadastre a senha antes do teste."})
    try:
        with db_connection() as db:
            proxy_id,proxy=proxy_for_account(db,account_id)
            with db.cursor() as c:
                c.execute("UPDATE social_instagram_auth SET last_healthcheck=UTC_TIMESTAMP() WHERE account_id=%s",(account_id,))
            db.commit()
        client=Client()
        if row["session_blob"]:
            try:
                client.set_settings(json.loads(cipher().decrypt(row["session_blob"].encode()).decode()))
            except (ValueError,InvalidToken,TypeError):
                return JSONResponse({"status":"paused","message":"Sessão inválida; reconecte manualmente."})
        client.set_proxy(proxy)
        client.login(row["username"],cipher().decrypt(row["encrypted_password"].encode()).decode())
        if not client.user_id:
            raise ValueError("Incomplete login")
        token=cipher().encrypt(json.dumps(client.get_settings()).encode()).decode()
        with db_connection() as db:
            with db.cursor() as c:
                c.execute("""UPDATE social_instagram_auth
                    SET session_blob=%s,proxy_id=%s,last_verified=UTC_TIMESTAMP(),
                    last_error=NULL WHERE account_id=%s""",(token,proxy_id,account_id))
                c.execute("UPDATE social_accounts SET status='connected' WHERE id=%s",(account_id,))
            db.commit()
        return JSONResponse({"status":"connected","message":"Sessão conectada e validada."})
    except Exception as exc:
        status,message=failure_reason(exc)
        logger.warning("Instagram check account_id=%s outcome=%s error_class=%s",account_id,status,type(exc).__name__)
        with db_connection() as db:
            with db.cursor() as c:
                c.execute("UPDATE social_accounts SET status=%s WHERE id=%s",(status,account_id))
            record_failure(db,account_id,exc,"check",status)
            db.commit()
        return JSONResponse({"status":status,"message":message})

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
