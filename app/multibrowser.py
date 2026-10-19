"""Authenticated MultiBrowser administration and supervised remote browser control."""
import os, re, secrets, json
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from .browser_runtime import browser_manager

router=APIRouter(prefix="/multibrowser",tags=["multibrowser"])
templates=Jinja2Templates(directory=str(Path(__file__).parent/"templates"))
SLOTS=16
NAME=re.compile(r"^[a-z0-9._]{1,30}$")

class AccountPayload(BaseModel):
    username:str=Field(min_length=1,max_length=40)
    label:str=Field(default="",max_length=90)

class CaptionPayload(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    body:str=Field(min_length=1,max_length=2200)

class ClickPayload(BaseModel):
    x:float=Field(ge=0,le=960)
    y:float=Field(ge=0,le=720)

class TypePayload(BaseModel):
    text:str=Field(min_length=1,max_length=2200)

class SaveTrainingFlow(BaseModel):
    slot:int=Field(ge=1,le=16)
    name:str=Field(min_length=2,max_length=100)

class LoadTrainingFlow(BaseModel):
    slot:int=Field(ge=1,le=16)

class KeyPayload(BaseModel):
    key:str=Field(pattern=r"^(Enter|Tab|Escape|Backspace|ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Home|End|Delete|ControlOrMeta\+A)$")

class WheelPayload(BaseModel):
    dx:float=Field(ge=-3000,le=3000)
    dy:float=Field(ge=-3000,le=3000)

class NavigatePayload(BaseModel):
    action:str=Field(pattern=r"^(back|forward|reload)$")

class LoginPasswordPayload(BaseModel):
    password:str=Field(min_length=1,max_length=256)

def db_conn():
    from .main import db_connection
    return db_connection()

def auth(req):
    from .main import admin
    if not admin(req): raise HTTPException(401,"Faça login no painel")

def write(req):
    from .main import form_allowed
    auth(req)
    if not form_allowed(req,req.headers.get("x-csrf-token","")):
        raise HTTPException(403,"Token CSRF inválido")
    origin=req.headers.get("origin","")
    if origin:
        if urlsplit(origin).scheme!="https" or urlsplit(origin).netloc.lower()!=req.headers.get("host","").lower():
            raise HTTPException(403,"Origem não autorizada")

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_mb_accounts (
          slot TINYINT UNSIGNED PRIMARY KEY,
          username VARCHAR(30) NOT NULL UNIQUE,label VARCHAR(90) NOT NULL DEFAULT '',
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_mb_captions (
          id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,title VARCHAR(100) NOT NULL,
          body TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_mb_saved_flows (
          id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
          name VARCHAR(100) NOT NULL,
          origin_slot TINYINT UNSIGNED NOT NULL,
          steps_json MEDIUMTEXT NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_mb_recordings (
          id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
          slot TINYINT UNSIGNED NOT NULL,kind VARCHAR(25) NOT NULL,
          x SMALLINT UNSIGNED NULL,y SMALLINT UNSIGNED NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,INDEX(slot,id)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci""")
    db.commit()

def state(db):
    init(db)
    with db.cursor() as c:
        c.execute("SELECT slot,username,label FROM social_mb_accounts ORDER BY slot")
        accounts=c.fetchall()
        c.execute("SELECT id,title,body FROM social_mb_captions ORDER BY id DESC LIMIT 150")
        captions=c.fetchall()
    return accounts,captions

def account(db,slot):
    if not 1<=slot<=16:raise HTTPException(404,"Posição inválida")
    with db.cursor() as c:
        c.execute("SELECT username FROM social_mb_accounts WHERE slot=%s",(slot,))
        row=c.fetchone()
    if not row:raise HTTPException(404,"Cadastre uma conta antes de abrir a tela")
    return row["username"]

@router.get("",response_class=HTMLResponse)
def home(request:Request):
    if not request.session.get("authenticated"):return RedirectResponse("/",303)
    if "csrf" not in request.session:request.session["csrf"]=secrets.token_urlsafe(32)
    err=""
    try:
        with db_conn() as db:accounts,captions=state(db)
    except Exception:
        accounts,captions=[],[]
        err="Banco de dados indisponível; confira a VPS."
    byslot={int(row["slot"]):row for row in accounts}
    return templates.TemplateResponse(request,"multibrowser.html",{
        "request":request,"csrf":request.session["csrf"],"slots":range(1,17),
        "byslot":byslot,"captions":captions,"error":err,
        "drive_configured":all(os.getenv(k) for k in ("GOOGLE_CLIENT_ID","GOOGLE_CLIENT_SECRET","GOOGLE_REDIRECT_URI"))
    })

@router.get("/api/state")
def get_state(request:Request):
    auth(request)
    with db_conn() as db:accounts,captions=state(db)
    return {"accounts":accounts,"captions":captions,"browser_active_slot":browser_manager.active_slot}

@router.post("/api/accounts/{slot}")
def upsert(slot:int,payload:AccountPayload,request:Request):
    write(request)
    if not 1<=slot<=16:raise HTTPException(422,"Use uma das 16 posições")
    username=payload.username.strip().lstrip("@").lower()
    if not NAME.fullmatch(username):raise HTTPException(422,"Usuário Instagram inválido")
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT slot FROM social_mb_accounts WHERE username=%s",(username,))
            existing=c.fetchone()
            if existing and int(existing["slot"])!=slot:
                raise HTTPException(409,"Essa conta já ocupa outra posição")
            try:
                c.execute("""INSERT INTO social_mb_accounts(slot,username,label) VALUES(%s,%s,%s)
                  ON DUPLICATE KEY UPDATE username=VALUES(username),label=VALUES(label)""",
                  (slot,username,payload.label.strip()))
            except Exception as exc:
                if getattr(exc,"args",[None])[0]==1062:
                    raise HTTPException(409,"Usuário já cadastrado em outra posição") from None
                raise
        db.commit()
    return {"ok":True,"slot":slot,"username":username}

@router.delete("/api/accounts/{slot}")
async def delete_account(slot:int,request:Request):
    write(request)
    if not 1<=slot<=16:raise HTTPException(404,"Posição inválida")
    if browser_manager.active_slot==slot:await browser_manager.close()
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("DELETE FROM social_mb_accounts WHERE slot=%s",(slot,))
            c.execute("DELETE FROM social_mb_recordings WHERE slot=%s",(slot,))
        db.commit()
    # Browser profile remains on disk until a separate explicit secure deletion.
    return {"ok":True,"note":"Cadastro removido; sessão do navegador preservada"}

@router.post("/api/captions")
def add_caption(payload:CaptionPayload,request:Request):
    write(request)
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("INSERT INTO social_mb_captions(title,body) VALUES(%s,%s)",(payload.title.strip(),payload.body))
            ident=c.lastrowid
        db.commit()
    return {"id":ident}

@router.put("/api/captions/{ident}")
def edit_caption(ident:int,payload:CaptionPayload,request:Request):
    write(request)
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT id FROM social_mb_captions WHERE id=%s",(ident,))
            if not c.fetchone():raise HTTPException(404,"Legenda não encontrada")
            c.execute("UPDATE social_mb_captions SET title=%s,body=%s WHERE id=%s",(payload.title.strip(),payload.body,ident))
        db.commit()
    return {"ok":True}

@router.delete("/api/captions/{ident}")
def delete_caption(ident:int,request:Request):
    write(request)
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("DELETE FROM social_mb_captions WHERE id=%s",(ident,))
        db.commit()
    return {"ok":True}

@router.post("/api/browser/{slot}/open")
async def open_browser(slot:int,request:Request):
    write(request)
    with db_conn() as db:username=account(db,slot)
    try:await browser_manager.open(slot,username)
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from exc
    return {"ok":True,"slot":slot,"viewport":{"width":960,"height":720}}

@router.post("/api/browser/close")
async def close_browser(request:Request):
    write(request)
    await browser_manager.close()
    return {"ok":True}

@router.get("/api/browser/{slot}/screenshot")
async def screenshot(slot:int,request:Request):
    auth(request)
    pixels=await browser_manager.screenshot(slot)
    if pixels is None:raise HTTPException(409,"Navegador indisponível")
    return Response(pixels,media_type="image/jpeg",headers={"Cache-Control":"no-store"})

@router.post("/api/browser/{slot}/click")
async def click(slot:int,payload:ClickPayload,request:Request):
    write(request)
    try:await browser_manager.click(slot,payload.x,payload.y)
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    return {"ok":True}

@router.post("/api/browser/{slot}/login-username")
async def login_username(slot:int,request:Request):
    write(request)
    with db_conn() as db:
        username=account(db,slot)
    try:
        await browser_manager.fill_login_field(slot,"username",username)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from None
    return {"ok":True}

@router.post("/api/browser/{slot}/login-password")
async def login_password(slot:int,payload:LoginPasswordPayload,request:Request):
    write(request)
    try:
        await browser_manager.fill_login_field(slot,"password",payload.password)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from None
    # Never retain or log credentials; the field is sent only to the active browser.
    return {"ok":True}

@router.post("/api/browser/{slot}/type")
async def type_text(slot:int,payload:TypePayload,request:Request):
    write(request)
    try:await browser_manager.type_text(slot,payload.text)
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    # Never record login secrets or any typed text.
    return {"ok":True}

@router.post("/api/browser/{slot}/key")
async def key(slot:int,payload:KeyPayload,request:Request):
    write(request)
    try:await browser_manager.press(slot,payload.key)
    except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    return {"ok":True}

@router.post("/api/browser/{slot}/wheel")
async def scroll_remote_browser(slot:int,payload:WheelPayload,request:Request):
    write(request)
    try:
        await browser_manager.wheel(slot,payload.dx,payload.dy)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from None
    return {"ok":True}

@router.post("/api/browser/{slot}/navigate")
async def navigate_remote_browser(slot:int,payload:NavigatePayload,request:Request):
    write(request)
    try:
        await browser_manager.navigate(slot,payload.action)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from None
    return {"ok":True}

@router.post("/api/browser/{slot}/record-click")
async def record_click(slot:int,payload:ClickPayload,request:Request):
    write(request)
    with db_conn() as db:account(db,slot)
    # Login, password and verification clicks are never saved, even if the
    # training checkbox was accidentally left enabled.
    try:
        protected=await browser_manager.is_login_screen(slot)
        await browser_manager.click(slot,payload.x,payload.y)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from None
    if protected:
        return {"ok":True,"recorded":False,"note":"Login: clique realizado sem gravação"}
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("INSERT INTO social_mb_recordings(slot,kind,x,y) VALUES(%s,'click',%s,%s)",
                      (slot,round(payload.x),round(payload.y)))
        db.commit()
    return {"ok":True,"recorded":True,"note":"Coordenada de treino registrada"}

@router.get("/api/browser/{slot}/recording")
def recording(slot:int,request:Request):
    auth(request)
    with db_conn() as db:
        account(db,slot)
        init(db)
        with db.cursor() as c:
            c.execute("SELECT id,kind,x,y FROM social_mb_recordings WHERE slot=%s ORDER BY id",(slot,))
            rows=c.fetchall()
    return {"steps":rows}

@router.delete("/api/browser/{slot}/recording")
def clear_recording(slot:int,request:Request):
    write(request)
    with db_conn() as db:
        account(db,slot)
        with db.cursor() as c:c.execute("DELETE FROM social_mb_recordings WHERE slot=%s",(slot,))
        db.commit()
    return {"ok":True}

@router.post("/api/browser/{slot}/replay-preview")
def replay_preview(slot:int,request:Request):
    write(request)
    with db_conn() as db:
        account(db,slot)
        with db.cursor() as c:
            c.execute("SELECT x,y FROM social_mb_recordings WHERE slot=%s ORDER BY id LIMIT 3",(slot,))
            rows=c.fetchall()
    if not rows:raise HTTPException(409,"Nenhum clique gravado")
    return {"steps":rows,"note":"Prévia somente; nenhuma publicação ou replay executado"}

@router.get("/api/training/flows")
def list_training_flows(request:Request):
    auth(request)
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT id,name,origin_slot,steps_json FROM social_mb_saved_flows ORDER BY id DESC LIMIT 100")
            rows=c.fetchall()
    return {"flows":[{"id":r["id"],"name":r["name"],"slot":r["origin_slot"],"steps":len(json.loads(r["steps_json"]))} for r in rows]}

@router.post("/api/training/flows")
def save_training_flow(payload:SaveTrainingFlow,request:Request):
    write(request)
    title=payload.name.strip()
    if len(title)<2:raise HTTPException(422,"Informe o nome do fluxo")
    with db_conn() as db:
        init(db)
        account(db,payload.slot)
        with db.cursor() as c:
            c.execute("SELECT x,y,kind FROM social_mb_recordings WHERE slot=%s ORDER BY id LIMIT 101",(payload.slot,))
            rows=c.fetchall()
            if not rows or len(rows)>100:raise HTTPException(409,"Grave de 1 a 100 cliques antes de salvar")
            steps=[{"x":int(r["x"]),"y":int(r["y"])} for r in rows if r["kind"]=="click"]
            if not steps:raise HTTPException(409,"Sem cliques para salvar")
            c.execute("INSERT INTO social_mb_saved_flows(name,origin_slot,steps_json) VALUES(%s,%s,%s)",
                      (title,payload.slot,json.dumps(steps,separators=(",",":"))))
            ident=c.lastrowid
        db.commit()
    return {"ok":True,"id":ident,"steps":len(steps)}

@router.post("/api/training/flows/{flow_id}/load")
def load_training_flow(flow_id:int,payload:LoadTrainingFlow,request:Request):
    write(request)
    with db_conn() as db:
        init(db)
        account(db,payload.slot)
        with db.cursor() as c:
            c.execute("SELECT steps_json FROM social_mb_saved_flows WHERE id=%s",(flow_id,))
            row=c.fetchone()
            if not row:raise HTTPException(404,"Fluxo não encontrado")
            try:steps=json.loads(row["steps_json"])
            except (ValueError,TypeError):raise HTTPException(409,"Fluxo inválido") from None
            if not isinstance(steps,list) or not 1<=len(steps)<=100:
                raise HTTPException(409,"Quantidade de cliques inválida")
            for x in steps:
                if not isinstance(x,dict) or type(x.get("x")) is not int or type(x.get("y")) is not int or not 0<=x["x"]<=960 or not 0<=x["y"]<=720:
                    raise HTTPException(409,"Coordenadas inválidas")
            c.execute("DELETE FROM social_mb_recordings WHERE slot=%s",(payload.slot,))
            c.executemany("INSERT INTO social_mb_recordings(slot,kind,x,y) VALUES(%s,'click',%s,%s)",
                          [(payload.slot,x["x"],x["y"]) for x in steps])
        db.commit()
    return {"ok":True,"steps":len(steps)}

@router.delete("/api/training/flows/{flow_id}")
def delete_training_flow(flow_id:int,request:Request):
    write(request)
    with db_conn() as db:
        init(db)
        with db.cursor() as c:c.execute("DELETE FROM social_mb_saved_flows WHERE id=%s",(flow_id,))
        db.commit()
    return {"ok":True}
