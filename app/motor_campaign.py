"""Campaign administration via local Windows motor token.

The public web dashboard stays read-only. Windows may manage captions,
Google Drive folder and scheduled posts through this authenticated API.
"""
import json
import logging
from datetime import date,datetime,time
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from googleapiclient.discovery import build

from app.motor_api import authorize
from app.campaign import (
    init,settings,build_plan,stored_captions,split_captions,
    folder_id_from_input,schedule_offsets,mysql_clock,TZ,MAX_DRIVE_FILES
)
from app.drive import creds as drive_credentials, store as save_credentials

router=APIRouter(prefix="/api/motor/campaign",tags=["local-campaign"])
logger=logging.getLogger("social.motor_campaign")

class CampaignSettings(BaseModel):
    folder_url: str=Field(min_length=10,max_length=500)
    captions: list[str]=Field(min_length=1,max_length=30)
    start_date: date
    start_time: time=time(10,0)
    end_time: time=time(19,0)
    days: int=Field(default=30,ge=1,le=30)

def db_conn():
    from app.main import db_connection
    return db_connection()

@router.get("")
def read_settings(authorization:str|None=Header(default=None)):
    authorize(authorization)
    with db_conn() as db:
        config=settings(db)
        with db.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM social_drive_assets")
            assets=c.fetchone()["n"]
            c.execute("SELECT status,COUNT(*) AS n FROM social_daily_jobs GROUP BY status")
            statuses={r["status"]:r["n"] for r in c.fetchall()}
    if not config:
        return {"configured":False,"assets":assets,"statuses":statuses}
    return {
        "configured":True,"folder_id":config["folder_id"],
        "captions":stored_captions(config),
        "start_date":str(config["start_date"]),
        "start_time":mysql_clock(config["start_time"]).strftime("%H:%M"),
        "end_time":mysql_clock(config["end_time"]).strftime("%H:%M"),
        "days":config["days"],"assets":assets,"statuses":statuses,
    }

@router.put("")
def update_settings(payload:CampaignSettings,authorization:str|None=Header(default=None)):
    authorize(authorization)
    folder=folder_id_from_input(payload.folder_url)
    if not folder:
        raise HTTPException(422,detail="Endereço da pasta do Drive inválido")
    if payload.start_date<datetime.now(TZ).date():
        raise HTTPException(422,detail="A data inicial não pode estar no passado")
    if any(not caption.strip() or len(caption)>2200 for caption in payload.captions):
        raise HTTPException(422,detail="Legendas devem ter entre 1 e 2200 caracteres")
    try:
        schedule_offsets(16,payload.start_time,payload.end_time,None)
    except ValueError:
        raise HTTPException(422,detail="Janela insuficiente para 16 perfis separados por 30 minutos")
    normalized=list(dict.fromkeys(x.strip() for x in payload.captions))
    with db_conn() as db:
        init(db)
        with db.cursor() as c:
            c.execute("""INSERT INTO social_campaign_settings(id,folder_id,caption,
                captions_json,start_date,start_time,end_time,days)
                VALUES(1,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE folder_id=VALUES(folder_id),
                   caption=VALUES(caption),captions_json=VALUES(captions_json),
                   start_date=VALUES(start_date),start_time=VALUES(start_time),
                   end_time=VALUES(end_time),days=VALUES(days)""",
                   (folder,normalized[0],json.dumps(normalized,ensure_ascii=False),
                    payload.start_date,payload.start_time,payload.end_time,payload.days))
        db.commit()
    return {"status":"saved","captions":len(normalized)}

@router.post("/sync-drive")
def sync_drive(authorization:str|None=Header(default=None)):
    authorize(authorization)
    with db_conn() as db:
        config=settings(db)
        if not config:
            raise HTTPException(409,detail="Configure a campanha primeiro")
        credentials=drive_credentials(db)
        if not credentials:
            raise HTTPException(409,detail="Conecte o Google Drive no site primeiro")
        folder=config["folder_id"]
        try:
            api=build("drive","v3",credentials=credentials,cache_discovery=False)
            meta=api.files().get(fileId=folder,fields="mimeType",supportsAllDrives=True).execute()
            if meta.get("mimeType")!="application/vnd.google-apps.folder":
                raise HTTPException(422,detail="ID não pertence a uma pasta")
            files=[]
            page_token=None
            while True:
                page=api.files().list(q=f"'{folder}' in parents and trashed = false",
                    fields="nextPageToken,files(id,name,mimeType,size)",
                    pageSize=1000,pageToken=page_token,
                    supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
                files.extend(item for item in page.get("files",[])
                    if item.get("mimeType","").startswith("video/")
                    and item.get("id") and int(item.get("size") or 0)>0)
                page_token=page.get("nextPageToken")
                if not page_token:
                    break
                if len(files)>=MAX_DRIVE_FILES:
                    raise HTTPException(422,detail="Pasta excede o limite de 2000 vídeos")
            if len(files)>MAX_DRIVE_FILES:
                raise HTTPException(422,detail="Pasta excede o limite de 2000 vídeos")
            with db.cursor() as c:
                for item in files:
                    c.execute("""INSERT INTO social_drive_assets(drive_id,folder_id,name,mime_type,size_bytes)
                        VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                        folder_id=VALUES(folder_id),name=VALUES(name),
                        mime_type=VALUES(mime_type),size_bytes=VALUES(size_bytes)""",
                        (item["id"],folder,item.get("name","Vídeo")[:255],
                         item["mimeType"][:100],int(item.get("size",0))))
            db.commit()
            save_credentials(db,credentials)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Local motor Drive sync error class=%s",type(exc).__name__)
            raise HTTPException(503,detail="Falha ao sincronizar a pasta do Drive")
    return {"status":"synced","videos":len(files)}

@router.post("/generate")
def generate_schedule(authorization:str|None=Header(default=None)):
    authorize(authorization)
    with db_conn() as db:
        config=settings(db)
        if not config:
            raise HTTPException(409,detail="Configure a campanha primeiro")
        if config["start_date"]<datetime.now(TZ).date():
            raise HTTPException(409,detail="Ajuste a data inicial para hoje ou depois")
        with db.cursor() as c:
            c.execute("SELECT id FROM social_accounts ORDER BY id")
            accounts=[r["id"] for r in c.fetchall()]
            c.execute("SELECT drive_id FROM social_drive_assets WHERE folder_id=%s ORDER BY drive_id",(config["folder_id"],))
            videos=[r["drive_id"] for r in c.fetchall()]
        if not accounts or not videos:
            raise HTTPException(409,detail="Cadastre contas e sincronize vídeos antes de gerar")
        try:
            rows=build_plan(accounts,videos,config["start_date"],
                mysql_clock(config["start_time"]),config["days"],
                captions=stored_captions(config),
                end_at=mysql_clock(config["end_time"]))
        except ValueError:
            raise HTTPException(422,detail="Horários insuficientes para a quantidade de contas")
        with db.cursor() as c:
            # Do not rewrite completed, uncertain or already claimed posts.
            c.execute("DELETE FROM social_daily_jobs WHERE status='planned'")
            added=0
            for aid,video,day,due,caption in rows:
                c.execute("""INSERT IGNORE INTO social_daily_jobs
                    (account_id,drive_id,local_day,planned_utc,caption,status)
                    VALUES(%s,%s,%s,%s,%s,'planned')""",(aid,video,day,due,caption))
                added+=c.rowcount
        db.commit()
    return {"status":"generated","scheduled":added}
