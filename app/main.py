import os
import re
import secrets
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
import pymysql
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
app = FastAPI(title="Social LeagueCut", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET") or "SET_SESSION_SECRET_IN_ENV_BEFORE_LOGIN", https_only=True, same_site="lax")

def db_connection():
    return pymysql.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT","3306")), user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False, connect_timeout=3)

def initialized():
    if not all(os.getenv(k) for k in ("DB_NAME","DB_USER","DB_PASSWORD")):
        return False
    with db_connection() as db:
        with db.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS social_accounts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(40) NOT NULL UNIQUE,
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS social_posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(160) NOT NULL,
                caption TEXT NOT NULL,
                scheduled_at DATETIME NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS social_post_accounts (
                post_id INT NOT NULL,
                account_id INT NOT NULL,
                PRIMARY KEY(post_id,account_id))""")
        db.commit()
    return True

def admin(request):
    return request.session.get("authenticated") is True

def form_allowed(request, token):
    return bool(token and secrets.compare_digest(str(token),str(request.session.get("csrf",""))))

def redirect(error=""):
    return RedirectResponse("/" + ("?error="+quote(error) if error else ""), status_code=303)

@app.get("/health")
def health():
    return {"status":"ok","service":"social-leaguecut"}

@app.get("/")
def home(request:Request):
    setup=all(os.getenv(k) for k in ("ADMIN_PASSWORD","SESSION_SECRET","DB_USER","DB_PASSWORD","DB_NAME"))
    if "csrf" not in request.session:
        request.session["csrf"]=secrets.token_urlsafe(32)
    data={"request":request,"logged":admin(request),"configured":setup,"csrf":request.session["csrf"],"error":request.query_params.get("error","")}
    if admin(request):
        try:
            initialized()
            with db_connection() as db:
                with db.cursor() as cur:
                    cur.execute("SELECT * FROM social_accounts ORDER BY id DESC")
                    data["accounts"]=cur.fetchall()
                    cur.execute("SELECT * FROM social_posts ORDER BY scheduled_at DESC LIMIT 100")
                    data["posts"]=cur.fetchall()
                    cur.execute("SELECT COUNT(*) AS n FROM social_post_accounts")
                    data["queue_count"]=cur.fetchone()["n"]
        except Exception:
            data["error"]="Banco de dados indisponível. Verifique as configurações na VPS."
            data.update(accounts=[],posts=[],queue_count=0)
    return templates.TemplateResponse(request,"index.html",data)

@app.post("/login")
def login(request:Request,password:str=Form(...),csrf:str=Form(...)):
    expected=os.getenv("ADMIN_PASSWORD","")
    if not form_allowed(request,csrf) or not expected or not os.getenv("SESSION_SECRET"):
        return redirect("Configuração ou sessão inválida")
    if secrets.compare_digest(expected.encode(),password.encode()):
        request.session.clear()
        request.session.update(authenticated=True,csrf=secrets.token_urlsafe(32))
        return redirect()
    return redirect("Senha incorreta")

@app.post("/logout")
def logout(request:Request,csrf:str=Form(...)):
    if form_allowed(request,csrf):
        request.session.clear()
    return redirect()

@app.post("/accounts")
def add_account(request:Request,username:str=Form(...),csrf:str=Form(...)):
    if not admin(request) or not form_allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    name=username.strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}",name):
        return redirect("Usuário inválido")
    try:
        with db_connection() as db:
            with db.cursor() as cur:
                cur.execute("INSERT INTO social_accounts(username) VALUES(%s)",(name,))
            db.commit()
    except pymysql.err.IntegrityError:
        return redirect("Conta já cadastrada")
    return redirect()

@app.post("/posts")
def add_post(request:Request,title:str=Form(...),caption:str=Form(""),scheduled_at:str=Form(...),account_ids:list[int]=Form([]),csrf:str=Form(...)):
    if not admin(request) or not form_allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if not title.strip() or len(title)>160 or len(caption)>2200 or not account_ids:
        return redirect("Preencha os campos e selecione contas")
    try:
        dt=datetime.fromisoformat(scheduled_at)
    except ValueError:
        return redirect("Data inválida")
    with db_connection() as db:
        with db.cursor() as cur:
            cur.execute("INSERT INTO social_posts(title,caption,scheduled_at) VALUES(%s,%s,%s)",(title.strip(),caption,dt))
            post_id=cur.lastrowid
            for account_id in set(account_ids):
                cur.execute("INSERT INTO social_post_accounts(post_id,account_id) SELECT %s,id FROM social_accounts WHERE id=%s",(post_id,account_id))
        db.commit()
    return redirect()
