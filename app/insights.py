"""Reel permalink and daily video metrics.

Only published jobs with a permalink are eligible for metrics. An authenticated
session for the original account and its fixed proxy are required. No automatic
login, IP rotation, or retry loop takes place during metrics collection.
"""
import os, re, json, sys, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit,quote
from fastapi import APIRouter,Request,Form
from fastapi.responses import JSONResponse,RedirectResponse
from cryptography.fernet import InvalidToken

logger=logging.getLogger("social.insights")
router=APIRouter()
LOCAL_TZ=ZoneInfo("America/Fortaleza")

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_video_metrics(
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          job_id BIGINT NOT NULL, capture_day DATE NOT NULL,
          views BIGINT NULL, likes BIGINT NULL, comments BIGINT NULL,
          result VARCHAR(32) NOT NULL DEFAULT 'ok',
          captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY once_per_job_per_day(job_id,capture_day))""")
        c.execute("SHOW COLUMNS FROM social_daily_jobs")
        existing={r["Field"] for r in c.fetchall()}
        if "permalink" not in existing:
            c.execute("ALTER TABLE social_daily_jobs ADD COLUMN permalink VARCHAR(500) NULL")
        if "media_pk" not in existing:
            c.execute("ALTER TABLE social_daily_jobs ADD COLUMN media_pk VARCHAR(100) NULL")
        if "published_at" not in existing:
            c.execute("ALTER TABLE social_daily_jobs ADD COLUMN published_at DATETIME NULL")
    db.commit()

def validate_permalink(url):
    parsed=urlsplit(url.strip())
    if parsed.scheme!="https" or parsed.netloc.lower() not in ("www.instagram.com","instagram.com"):
        return False
    return bool(re.fullmatch(r"/(?:reel|p)/[A-Za-z0-9_-]{5,40}/?",parsed.path))

def dashboard(db,limit=100):
    init(db)
    with db.cursor() as c:
        c.execute("""SELECT j.id,j.status,j.local_day,a.username,j.permalink,j.media_pk,
                     (SELECT m.views FROM social_video_metrics m WHERE m.job_id=j.id
                      ORDER BY m.capture_day DESC LIMIT 1) AS last_views,
                     (SELECT m.capture_day FROM social_video_metrics m WHERE m.job_id=j.id
                      ORDER BY m.capture_day DESC LIMIT 1) AS last_checked
            FROM social_daily_jobs j
            JOIN social_accounts a ON a.id=j.account_id
            WHERE j.status='published' OR j.permalink IS NOT NULL
            ORDER BY COALESCE(j.published_at,j.planned_utc) DESC LIMIT %s""",(limit,))
        items=c.fetchall()
        c.execute("SELECT COUNT(*) AS n FROM social_daily_jobs WHERE status='published'")
        published=c.fetchone()["n"]
        c.execute("""SELECT SUM(last_sample.views) AS views FROM
            (SELECT job_id, MAX(capture_day) AS latest FROM social_video_metrics
             WHERE views IS NOT NULL GROUP BY job_id) current_snap
             JOIN social_video_metrics last_sample
              ON last_sample.job_id=current_snap.job_id AND last_sample.capture_day=current_snap.latest""")
        total=(c.fetchone() or {}).get("views") or 0
    return {"rows":items,"published":published,"last_known_views":int(total)}

def allowed(request,csrf):
    from app.main import admin,form_allowed
    return admin(request) and form_allowed(request,csrf)

def go(message):
    return RedirectResponse("/?error="+quote(message),status_code=303)

@router.post("/insights/posts/{job_id}/record")
def record(request:Request,job_id:int,csrf:str=Form(...),url:str=Form(...),media_pk:str=Form("")):
    if not allowed(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    if not validate_permalink(url) or (media_pk and not re.fullmatch(r"[0-9]{5,40}",media_pk)):
        return go("Link ou identificador de Reel inválido.")
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT status FROM social_daily_jobs WHERE id=%s",(job_id,))
            existing=c.fetchone()
            if not existing or existing["status"] not in ("planned","published"):
                return go("Postagem não encontrada ou status não permite registro.")
            c.execute("""UPDATE social_daily_jobs SET permalink=%s,media_pk=%s,
                       status='published',published_at=COALESCE(published_at,UTC_TIMESTAMP())
                       WHERE id=%s""",(url.strip(),media_pk or None,job_id))
        db.commit()
    return go("Link do Reel registrado no relatório.")

def collect_daily(db, cap_total=80, cap_per_account=5):
    if os.environ.get("ALLOW_LEGACY_VPS_INSTAGRAM_WORKER","false").lower()!="true":
        logger.info("VPS Instagram metrics worker disabled: use local Windows motor")
        return {"scanned":0,"disabled":True}
    """Save at most one snapshot per Reel per Fortaleza-local day.

    Caps prevent hundreds of private API reads on a single run.
    Errors are recorded; do not retry in the same day.
    """
    from instagrapi import Client
    from app.instagram import proxy_for_account,cipher,failure_reason
    init(db)
    today=datetime.now(LOCAL_TZ).date()
    with db.cursor() as c:
        c.execute("""SELECT j.id,j.account_id,j.permalink,j.media_pk,a.username,
                    auth.session_blob,auth.proxy_id
          FROM social_daily_jobs j
          JOIN social_accounts a ON a.id=j.account_id
          JOIN social_instagram_auth auth ON auth.account_id=j.account_id
          LEFT JOIN social_video_metrics m ON m.job_id=j.id AND m.capture_day=%s
          LEFT JOIN (SELECT job_id,MAX(capture_day) AS latest FROM social_video_metrics
                    GROUP BY job_id) previous ON previous.job_id=j.id
          WHERE j.status='published' AND j.permalink IS NOT NULL
            AND a.status='connected' AND m.id IS NULL
          ORDER BY j.account_id ASC, COALESCE(previous.latest,'1970-01-01') ASC, j.id DESC
          LIMIT 2000""",(today,))
        candidates=c.fetchall()
    per_account={}
    done=0
    for job in candidates:
        aid=job["account_id"]
        if done>=cap_total:
            break
        if per_account.get(aid,0)>=cap_per_account:
            continue
        per_account[aid]=per_account.get(aid,0)+1
        if not job["session_blob"]:
            continue
        result="ok"
        views=None
        likes=None
        comments=None
        media_pk=job["media_pk"]
        try:
            _,proxy=proxy_for_account(db,aid)
            client=Client()
            client.set_settings(json.loads(cipher().decrypt(job["session_blob"].encode()).decode()))
            client.set_proxy(proxy)
            if not media_pk:
                media_pk=str(client.media_pk_from_url(job["permalink"]))
            media=client.media_info(media_pk)
            count=getattr(media,"view_count",None)
            if count is None:
                count=getattr(media,"play_count",None)
            views=int(count) if count is not None else None
            likes=getattr(media,"like_count",None)
            comments=getattr(media,"comment_count",None)
            likes=int(likes) if likes is not None else None
            comments=int(comments) if comments is not None else None
        except Exception as exc:
            status,_=failure_reason(exc)
            result="unavailable"
            logger.warning("Daily metrics job=%s status=%s error_class=%s",
                           job["id"],status,type(exc).__name__)
            if status in ("needs_verification","paused"):
                with db.cursor() as c:
                    c.execute("UPDATE social_accounts SET status=%s WHERE id=%s",(status,aid))
                db.commit()
        with db.cursor() as c:
            c.execute("""INSERT IGNORE INTO social_video_metrics
                (job_id,capture_day,views,likes,comments,result)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (job["id"],today,views,likes,comments,result))
            if media_pk and media_pk!=job["media_pk"]:
                c.execute("UPDATE social_daily_jobs SET media_pk=%s WHERE id=%s",
                          (media_pk,job["id"]))
        db.commit()
        done+=1
    return {"scanned":done,"date":str(today)}

if __name__=="__main__":
    from app.main import db_connection
    with db_connection() as connection:
        result=collect_daily(connection)
    print(json.dumps(result))
