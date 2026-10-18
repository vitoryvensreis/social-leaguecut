"""Local Windows motor API. MySQL and Drive tokens never leave the VPS.

Motor only calls HTTPS endpoints with a separate long-lived API token.
No proxy fallback, no unattended Instagram logins. Claim-before-publish
prevents implicit retries after uncertain upload outcomes.
"""
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List
from app.drive import creds as google_creds
from app.campaign import init as campaign_init
from app.insights import init as metrics_init, validate_permalink
from google.auth.transport.requests import AuthorizedSession

router=APIRouter(prefix="/api/motor",tags=["local-motor"])
TZ=ZoneInfo("America/Fortaleza")
MAX_MEDIA=500*1024*1024

def authorize(header):
    token=os.getenv("MOTOR_API_TOKEN","")
    supplied=header.removeprefix("Bearer ").strip() if header and header.startswith("Bearer ") else ""
    if len(token)<32 or not supplied or not secrets.compare_digest(token,supplied):
        raise HTTPException(401,detail="Unauthorized")

def setup(db):
    campaign_init(db)
    metrics_init(db)
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_motor_claims(
          job_id BIGINT PRIMARY KEY, token_hash CHAR(64) NOT NULL,
          claimed_at DATETIME NOT NULL, finished_at DATETIME NULL
        )""")
    db.commit()

def db_conn():
    from app.main import db_connection
    return db_connection()

class ClaimRequest(BaseModel):
    ready_accounts: List[int] = Field(default_factory=list, max_length=100)

class AccountStatus(BaseModel):
    status: str

class Result(BaseModel):
    claim_token: str=Field(min_length=32,max_length=128)
    outcome: str
    permalink: str|None=None
    media_pk: str|None=None

class Metric(BaseModel):
    job_id: int
    views: int|None=None
    likes: int|None=None
    comments: int|None=None
    result: str="ok"

@router.get("/health")
def motor_health(authorization:str|None=Header(default=None)):
    authorize(authorization)
    return {"status":"ok","time_utc":datetime.now(timezone.utc).isoformat()}

@router.get("/accounts")
def accounts(authorization:str|None=Header(default=None)):
    authorize(authorization)
    with db_conn() as db:
        with db.cursor() as c:
            c.execute("SELECT id,username,status FROM social_accounts ORDER BY id")
            return {"accounts":c.fetchall()}

@router.post("/accounts/{account_id}/status")
def account_status(account_id:int,update:AccountStatus,authorization:str|None=Header(default=None)):
    authorize(authorization)
    if update.status not in ("connected","needs_verification","paused","disconnected"):
        raise HTTPException(400,detail="Invalid status")
    with db_conn() as db:
        with db.cursor() as c:
            c.execute("UPDATE social_accounts SET status=%s WHERE id=%s",(update.status,account_id))
            if c.rowcount!=1:
                raise HTTPException(404,detail="Account not found")
        db.commit()
    return {"status":"ok"}

@router.post("/jobs/claim")
def claim(payload:ClaimRequest,authorization:str|None=Header(default=None)):
    authorize(authorization)
    ready=sorted({value for value in payload.ready_accounts if isinstance(value,int) and value>0})
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    valid_after=now-timedelta(minutes=90)
    with db_conn() as db:
        setup(db)
        with db.cursor() as c:
            # Never retry uncertain uploads, even after the PC crashes.
            c.execute("""UPDATE social_daily_jobs j JOIN social_motor_claims m ON m.job_id=j.id
                SET j.status='needs_review'
                WHERE j.status='publishing'
                  AND m.claimed_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 90 MINUTE)""")
            c.execute("""UPDATE social_daily_jobs SET status='missed'
                WHERE status='planned' AND planned_utc<%s""",(valid_after,))
            if not ready:
                db.commit()
                return {"job":None}
            placeholder=",".join(["%s"]*len(ready))
            query=f"""SELECT j.id,j.account_id,j.drive_id,j.caption,j.planned_utc,a.username
                FROM social_daily_jobs j
                JOIN social_accounts a ON a.id=j.account_id
                WHERE j.status='planned' AND a.status='connected'
                  AND j.account_id IN ({placeholder})
                  AND j.planned_utc BETWEEN %s AND %s
                ORDER BY j.planned_utc,j.id LIMIT 1 FOR UPDATE"""
            c.execute(query,(*ready,valid_after,now))
            job=c.fetchone()
            if not job:
                db.commit()
                return {"job":None}
            token=secrets.token_urlsafe(40)
            digest=hashlib.sha256(token.encode()).hexdigest()
            c.execute("UPDATE social_daily_jobs SET status='publishing' WHERE id=%s AND status='planned'",(job["id"],))
            c.execute("""INSERT INTO social_motor_claims(job_id,token_hash,claimed_at)
                VALUES(%s,%s,UTC_TIMESTAMP())""",(job["id"],digest))
        db.commit()
    return {"job":{"id":job["id"],"account_id":job["account_id"],
                   "username":job["username"],"drive_id":job["drive_id"],
                   "caption":job["caption"],"claim_token":token}}

@router.post("/jobs/{job_id}/result")
def publish_result(job_id:int,payload:Result,authorization:str|None=Header(default=None)):
    authorize(authorization)
    if payload.outcome not in ("published","needs_review"):
        raise HTTPException(400,detail="Invalid outcome")
    if payload.outcome=="published" and (not payload.permalink or not validate_permalink(payload.permalink)):
        raise HTTPException(400,detail="Valid permalink required")
    if payload.media_pk is not None and not re.fullmatch(r"[0-9]{5,40}",payload.media_pk):
        raise HTTPException(400,detail="Invalid media identifier")
    with db_conn() as db:
        setup(db)
        with db.cursor() as c:
            c.execute("""SELECT m.token_hash,j.status FROM social_motor_claims m
                JOIN social_daily_jobs j ON j.id=m.job_id WHERE m.job_id=%s FOR UPDATE""",(job_id,))
            row=c.fetchone()
            if not row or row["status"]!="publishing" or not secrets.compare_digest(
                row["token_hash"],hashlib.sha256(payload.claim_token.encode()).hexdigest()
            ):
                raise HTTPException(409,detail="Job already finished or invalid claim")
            if payload.outcome=="published":
                c.execute("""UPDATE social_daily_jobs SET status='published',permalink=%s,
                    media_pk=%s,published_at=UTC_TIMESTAMP() WHERE id=%s""",
                    (payload.permalink,payload.media_pk,job_id))
            else:
                c.execute("UPDATE social_daily_jobs SET status='needs_review' WHERE id=%s",(job_id,))
            c.execute("UPDATE social_motor_claims SET finished_at=UTC_TIMESTAMP() WHERE job_id=%s",(job_id,))
        db.commit()
    return {"status":"ok","outcome":payload.outcome}

@router.get("/media/{drive_id}")
def download(drive_id:str,authorization:str|None=Header(default=None)):
    authorize(authorization)
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,160}",drive_id):
        raise HTTPException(400,detail="Invalid media ID")
    with db_conn() as db:
        with db.cursor() as c:
            c.execute("""SELECT drive_id,size_bytes,mime_type FROM social_drive_assets
                WHERE drive_id=%s""",(drive_id,))
            file=c.fetchone()
        if not file or not file["mime_type"].startswith("video/") or not 0<int(file["size_bytes"])<=MAX_MEDIA:
            raise HTTPException(404,detail="Video unavailable or over limit")
        credentials=google_creds(db)
        if not credentials:
            raise HTTPException(503,detail="Google Drive not connected")
    def chunks():
        session=AuthorizedSession(credentials)
        response=None
        try:
            response=session.get(
                f"https://www.googleapis.com/drive/v3/files/{quote(drive_id,safe='')}?alt=media",
                stream=True,timeout=35,
            )
            response.raise_for_status()
            limit=MAX_MEDIA
            for piece in response.iter_content(chunk_size=1024*1024):
                if piece:
                    limit-=len(piece)
                    if limit<0:
                        raise RuntimeError("File exceeded media limit")
                    yield piece
        finally:
            if response is not None:
                response.close()
            session.close()
    return StreamingResponse(chunks(),media_type="application/octet-stream",
                             headers={"Cache-Control":"no-store",
                                      "Content-Disposition":"attachment; filename=clip.mp4"})

@router.get("/metrics/pending")
def pending_metrics(authorization:str|None=Header(default=None)):
    authorize(authorization)
    today=datetime.now(TZ).date()
    with db_conn() as db:
        setup(db)
        with db.cursor() as c:
            c.execute("""SELECT j.id,j.account_id,j.media_pk,j.permalink
                FROM social_daily_jobs j LEFT JOIN social_video_metrics m
                ON m.job_id=j.id AND m.capture_day=%s
                WHERE j.status='published' AND j.permalink IS NOT NULL
                  AND m.id IS NULL
                ORDER BY j.published_at ASC LIMIT 80""",(today,))
            return {"jobs":c.fetchall(),"day":str(today)}

@router.post("/metrics")
def save_metric(item:Metric,authorization:str|None=Header(default=None)):
    authorize(authorization)
    if item.result not in ("ok","unavailable"):
        raise HTTPException(400,detail="Invalid metric result")
    for value in (item.views,item.likes,item.comments):
        if value is not None and not 0<=value<=10**13:
            raise HTTPException(400,detail="Invalid metric")
    today=datetime.now(TZ).date()
    with db_conn() as db:
        setup(db)
        with db.cursor() as c:
            c.execute("SELECT id FROM social_daily_jobs WHERE id=%s AND status='published'",(item.job_id,))
            if not c.fetchone():
                raise HTTPException(404,detail="Not a published job")
            c.execute("""INSERT IGNORE INTO social_video_metrics
              (job_id,capture_day,views,likes,comments,result)
              VALUES(%s,%s,%s,%s,%s,%s)""",
              (item.job_id,today,item.views,item.likes,item.comments,item.result))
        db.commit()
    return {"status":"ok"}
