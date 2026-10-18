"""Catalog local Windows videos without uploading media or exposing PC paths.

All endpoints require the motor token. Local videos live ONLY on the PC;
the VPS keeps display metadata and schedule references for monitoring.
"""
import re
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,Field
from app.motor_api import authorize
from app.campaign import init as init_campaign

router=APIRouter(prefix="/api/motor/library",tags=["windows-library"])
LOCAL_FOLDER="local-windows"
VIDEO_TYPES={"mp4":"video/mp4","mov":"video/quicktime","m4v":"video/x-m4v","webm":"video/webm"}

class LocalVideo(BaseModel):
    id: str=Field(min_length=38,max_length=70)
    name: str=Field(min_length=1,max_length=255)
    size_bytes: int=Field(gt=0,le=4*1024*1024*1024)

class LocalInventory(BaseModel):
    videos: list[LocalVideo]=Field(min_length=1,max_length=2000)

def ensure_table(db):
    init_campaign(db)
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_local_inventory(
             drive_id VARCHAR(160) PRIMARY KEY,
             updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        )""")
    db.commit()

def db_conn():
    from app.main import db_connection
    return db_connection()

def validate_video(video):
    if not re.fullmatch(r"local_[a-f0-9]{64}",video.id):
        raise HTTPException(422,detail="ID de vídeo local inválido.")
    if video.name in (".","..") or "/" in video.name or "\\" in video.name:
        raise HTTPException(422,detail="Nome de vídeo inválido.")
    ext=video.name.rsplit(".",1)[-1].lower()
    if ext not in VIDEO_TYPES:
        raise HTTPException(422,detail="Aceitamos vídeos MP4, MOV, M4V ou WEBM.")
    return VIDEO_TYPES[ext]

@router.post("/sync")
def sync_local(payload:LocalInventory,authorization:str|None=Header(default=None)):
    authorize(authorization)
    ids=[video.id for video in payload.videos]
    if len(ids)!=len(set(ids)):
        raise HTTPException(422,detail="Biblioteca contém vídeos duplicados.")
    mimes=[validate_video(video) for video in payload.videos]
    with db_conn() as db:
        ensure_table(db)
        try:
            with db.cursor() as c:
                # Metadata is retained for past published posts; current
                # available files live in the separate inventory table.
                c.execute("DELETE FROM social_local_inventory")
                for video,mime in zip(payload.videos,mimes):
                    c.execute("""INSERT INTO social_drive_assets
                        (drive_id,folder_id,name,mime_type,size_bytes)
                        VALUES(%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE folder_id=VALUES(folder_id),
                          name=VALUES(name),mime_type=VALUES(mime_type),
                          size_bytes=VALUES(size_bytes)""",
                          (video.id,LOCAL_FOLDER,video.name,mime,video.size_bytes))
                    c.execute("INSERT INTO social_local_inventory(drive_id) VALUES(%s)",(video.id,))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"status":"synced","videos":len(payload.videos),"source":"windows","uploaded_bytes":0}

@router.get("")
def local_library(authorization:str|None=Header(default=None)):
    authorize(authorization)
    with db_conn() as db:
        ensure_table(db)
        with db.cursor() as c:
            c.execute("""SELECT a.drive_id AS id,a.name,a.size_bytes
                 FROM social_local_inventory i
                 JOIN social_drive_assets a ON a.drive_id=i.drive_id
                 ORDER BY a.name LIMIT 2000""")
            return {"videos":c.fetchall(),"source":"windows"}
