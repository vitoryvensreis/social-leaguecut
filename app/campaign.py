"""Google Drive folder-backed daily campaign planner.

Planning only: this module NEVER posts to Instagram and NEVER handles Instagram
credentials. Planned jobs stay inert until a separately approved publisher runs.
"""
import logging
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
from googleapiclient.discovery import build
from app.drive import creds as drive_credentials, store as save_credentials

router = APIRouter()
logger = logging.getLogger("social.campaign")
TZ = ZoneInfo("America/Fortaleza")
DAYS = 30
MAX_DRIVE_FILES = 2000
FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,160}$")

def folder_id_from_input(text: str) -> str | None:
    value = text.strip()
    if "drive.google.com" in value:
        parsed = urlparse(value)
        if parsed.hostname != "drive.google.com":
            return None
        match = re.search(r"/folders/([A-Za-z0-9_-]{10,160})", parsed.path)
        value = match.group(1) if match else ""
    return value if FOLDER_PATTERN.fullmatch(value) else None

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_campaign_settings (
          id TINYINT PRIMARY KEY, folder_id VARCHAR(160) NOT NULL,
          caption TEXT NOT NULL, start_date DATE NOT NULL,
          start_time TIME NOT NULL, timezone VARCHAR(64) NOT NULL DEFAULT 'America/Fortaleza',
          days SMALLINT NOT NULL DEFAULT 30,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_drive_assets (
          drive_id VARCHAR(160) PRIMARY KEY, folder_id VARCHAR(160) NOT NULL,
          name VARCHAR(255) NOT NULL, mime_type VARCHAR(100) NOT NULL,
          size_bytes BIGINT NOT NULL DEFAULT 0,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_folder(folder_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_daily_jobs (
          id BIGINT AUTO_INCREMENT PRIMARY KEY, account_id INT NOT NULL,
          drive_id VARCHAR(160) NOT NULL,
          local_day DATE NOT NULL, planned_utc DATETIME NOT NULL,
          caption TEXT NOT NULL, status VARCHAR(25) NOT NULL DEFAULT 'planned',
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY once_per_account_day(account_id,local_day),
          INDEX idx_due(status,planned_utc)
        )""")
    db.commit()

def settings(db):
    init(db)
    with db.cursor() as c:
        c.execute("SELECT * FROM social_campaign_settings WHERE id=1")
        return c.fetchone()

def summary(db):
    init(db)
    with db.cursor() as c:
        c.execute("SELECT COUNT(*) AS n FROM social_drive_assets WHERE folder_id=(SELECT folder_id FROM social_campaign_settings WHERE id=1)")
        assets = c.fetchone()["n"]
        c.execute("SELECT status, COUNT(*) AS n FROM social_daily_jobs GROUP BY status")
        counts = {r["status"]:r["n"] for r in c.fetchall()}
        c.execute("""SELECT j.local_day, j.planned_utc, j.status, a.username, d.name
        FROM social_daily_jobs j JOIN social_accounts a ON a.id=j.account_id
        LEFT JOIN social_drive_assets d ON d.drive_id=j.drive_id
        ORDER BY j.planned_utc LIMIT 24""")
        items = c.fetchall()
    return {"asset_count":assets,"job_counts":counts,"jobs":items}

def authorized(request,csrf):
    from app.main import admin, form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+quote(message), status_code=303)

def build_plan(accounts: list[int], asset_ids: list[str], start: date, at: time, days: int = DAYS) -> list[tuple]:
    """Each account receives its own shuffled cycle; no repeats before cycle exhaustion.

    Daily post times are spread across two hours beginning at the chosen time.
    The planner does not dispatch requests to Instagram.
    """
    if not accounts or not asset_ids:
        return []
    if not 1 <= days <= DAYS:
        raise ValueError("Unsupported duration")
    unique_assets=list(dict.fromkeys(asset_ids))
    rng=secrets.SystemRandom()
    assignments={account_id: [] for account_id in accounts}
    for account_id in accounts:
        for offset in range(days):
            if offset % len(unique_assets)==0:
                cycle=unique_assets[:]
                rng.shuffle(cycle)
            assignments[account_id].append(cycle[offset % len(unique_assets)])
    rows=[]
    for offset in range(days):
        day=start + timedelta(days=offset)
        order=accounts[:]
        rng.shuffle(order)
        # Stagger one post per account over a two-hour daily window.
        spacing=max(1, 120 // max(1,len(order)))
        for slot,account_id in enumerate(order):
            local_dt=datetime.combine(day,at,tzinfo=TZ)+timedelta(minutes=slot*spacing)
            rows.append((account_id,assignments[account_id][offset],local_dt.date(),local_dt.astimezone(timezone.utc).replace(tzinfo=None)))
    return rows

@router.post("/campaign/settings")
def save_settings(request: Request, csrf: str=Form(...), folder_url: str=Form(...),
                  caption: str=Form(""), start_date: str=Form(...), start_time: str=Form(...)):
    if not authorized(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    folder=folder_id_from_input(folder_url)
    try:
        day=date.fromisoformat(start_date)
        at=time.fromisoformat(start_time)
    except ValueError:
        return go("Data ou horário inválidos")
    now=datetime.now(TZ)
    if not folder or len(caption)>2200 or day<now.date():
        return go("Confira pasta, legenda e data da campanha")
    if at.hour>21 or (at.hour==21 and at.minute>30):
        return go("Escolha um horário até 21:30 para manter todos os posts no mesmo dia.")
    if day==now.date() and datetime.combine(day,at,tzinfo=TZ)<=now:
        return go("O horário escolhido para hoje já passou. Escolha outro horário ou comece amanhã.")
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("""INSERT INTO social_campaign_settings(id,folder_id,caption,start_date,start_time,days)
            VALUES(1,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            folder_id=VALUES(folder_id),caption=VALUES(caption),
            start_date=VALUES(start_date),start_time=VALUES(start_time),days=VALUES(days)""",
                (folder,caption,day,at,DAYS))
        db.commit()
    return go("Configuração da campanha salva. Sincronize a pasta antes de gerar a programação.")

@router.post("/campaign/sync")
def sync_folder(request: Request, csrf: str=Form(...)):
    if not authorized(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        config=settings(db)
        if not config:
            return go("Salve primeiro a pasta do Drive e a legenda.")
        folder=config["folder_id"]
        credentials=drive_credentials(db)
        if not credentials:
            return go("Conecte o Google Drive antes de sincronizar a pasta.")
        try:
            drive=build("drive","v3",credentials=credentials,cache_discovery=False)
            meta=drive.files().get(fileId=folder,fields="id,name,mimeType",supportsAllDrives=True).execute()
            if meta.get("mimeType")!="application/vnd.google-apps.folder":
                return go("O endereço não corresponde a uma pasta Google Drive.")
            video_files=[]
            token=None
            while len(video_files)<MAX_DRIVE_FILES:
                page=drive.files().list(
                    q=f"'{folder}' in parents and trashed = false",
                    fields="nextPageToken,files(id,name,mimeType,size)",
                    pageSize=1000,pageToken=token,
                    supportsAllDrives=True,includeItemsFromAllDrives=True
                ).execute()
                for item in page.get("files",[]):
                    if item.get("mimeType","").startswith("video/") and item.get("id"):
                        video_files.append(item)
                token=page.get("nextPageToken")
                if not token:
                    break
            if len(video_files)>=MAX_DRIVE_FILES and token:
                return go("Pasta muito grande. Limite de 2000 vídeos por sincronização.")
            init(db)
            with db.cursor() as c:
                for item in video_files:
                    c.execute("""INSERT INTO social_drive_assets(drive_id,folder_id,name,mime_type,size_bytes)
                    VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                    folder_id=VALUES(folder_id),name=VALUES(name),
                    mime_type=VALUES(mime_type),size_bytes=VALUES(size_bytes)""",
                        (item["id"],folder,item.get("name","sem-nome")[:255],
                         item.get("mimeType","")[:100],int(item.get("size",0))))
            db.commit()
            save_credentials(db,credentials)
            return go(f"Pasta sincronizada: {len(video_files)} vídeos encontrados.")
        except Exception as exc:
            logger.warning("Folder sync failed reason=%s",type(exc).__name__)
            return go("Não foi possível ler a pasta. Confira o link, as permissões e a Drive API.")

@router.post("/campaign/generate")
def generate(request: Request, csrf: str=Form(...)):
    if not authorized(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        config=settings(db)
        if not config:
            return go("Configure a campanha primeiro.")
        if config["start_date"]<datetime.now(TZ).date():
            return go("A data inicial passou. Atualize a campanha para começar hoje ou depois.")
        with db.cursor() as c:
            c.execute("SELECT id FROM social_accounts ORDER BY id LIMIT 17")
            accounts=[r["id"] for r in c.fetchall()]
            c.execute("SELECT drive_id FROM social_drive_assets WHERE folder_id=%s ORDER BY drive_id",(config["folder_id"],))
            assets=[r["drive_id"] for r in c.fetchall()]
        if not 1<=len(accounts)<=16:
            return go("Cadastre de 1 a 16 contas para planejar a campanha.")
        if not assets:
            return go("Sincronize primeiro uma pasta que contenha vídeos.")
        at=config["start_time"]
        if isinstance(at,timedelta):
            seconds=int(at.total_seconds())
            at=time(hour=(seconds//3600)%24,minute=(seconds//60)%60)
        rows=build_plan(accounts,assets,config["start_date"],at,DAYS)
        with db.cursor() as c:
            for account_id,drive_id,day,due in rows:
                c.execute("""INSERT IGNORE INTO social_daily_jobs(account_id,drive_id,local_day,planned_utc,caption,status)
                VALUES(%s,%s,%s,%s,%s,'planned')""",
                    (account_id,drive_id,day,due,config["caption"]))
            created=c.rowcount
        db.commit()
    return go(f"Programação gerada: até {len(rows)} itens. Duplicatas do mesmo dia foram ignoradas. Publicador ainda desligado.")
