"""Social LeagueCut VPS: monitoring, database, Drive OAuth, Windows API.

This service deliberately does not log into Instagram or control Windows
browser sessions. Scheduled posts remain in MySQL for the local motor.
"""
import os
import secrets
from pathlib import Path
from urllib.parse import quote

import pymysql
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.drive import router as drive_router
from app.motor_api import router as motor_router
from app.motor_campaign import router as motor_campaign_router
from app.local_library import router as local_library_router
from app.monitor import router as reports_router, report as monitor_report

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/".env")
templates=Jinja2Templates(directory=str(ROOT/"app"/"templates"))
app=FastAPI(title="Social LeagueCut · Central de resultados",docs_url=None,redoc_url=None)
app.include_router(drive_router)
app.include_router(motor_router)
app.include_router(motor_campaign_router)
app.include_router(local_library_router)
app.include_router(reports_router)
app.add_middleware(SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or "SET_SESSION_SECRET_IN_ENV_BEFORE_LOGIN",
    https_only=True,same_site="lax")

def db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST","localhost"),port=int(os.getenv("DB_PORT","3306")),
        user=os.environ["DB_USER"],password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,autocommit=False,connect_timeout=3,
    )

def initialized():
    if not all(os.getenv(k) for k in ("DB_NAME","DB_USER","DB_PASSWORD")):
        return False
    with db_connection() as db:
        with db.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS social_accounts(
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(40) NOT NULL UNIQUE,
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS social_posts(
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(160) NOT NULL,caption TEXT NOT NULL,
                scheduled_at DATETIME NOT NULL,status VARCHAR(30) NOT NULL DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS social_post_accounts(
                post_id INT NOT NULL,account_id INT NOT NULL,
                PRIMARY KEY(post_id,account_id))""")
        db.commit()
    return True

def admin(request):
    return request.session.get("authenticated") is True

def form_allowed(request,token):
    return bool(token and secrets.compare_digest(
        str(token),str(request.session.get("csrf",""))))

def redirect(message=""):
    return RedirectResponse("/"+("?error="+quote(message) if message else ""),status_code=303)

@app.get("/health")
def health():
    return {"status":"ok","service":"social-leaguecut"}

@app.get("/")
def home(request:Request):
    configured=all(os.getenv(key) for key in
        ("ADMIN_PASSWORD","SESSION_SECRET","DB_USER","DB_PASSWORD","DB_NAME"))
    if "csrf" not in request.session:
        request.session["csrf"]=secrets.token_urlsafe(32)
    data={"request":request,"logged":admin(request),"configured":configured,
          "csrf":request.session["csrf"],"error":request.query_params.get("error",""),
          "google_configured":all(os.getenv(k) for k in
             ("GOOGLE_CLIENT_ID","GOOGLE_CLIENT_SECRET","GOOGLE_REDIRECT_URI")),
          "monitor":None}
    if admin(request):
        try:
            initialized()
            with db_connection() as db:
                data["monitor"]=monitor_report(db)
        except Exception:
            data["error"]="Não foi possível consultar os relatórios no MySQL. Confira o serviço e os logs."
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
