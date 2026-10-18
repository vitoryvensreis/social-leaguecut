"""Read-only reports for the VPS control tower.

Only the Windows motor can create/update publication states via authenticated
/api/motor endpoints. No Instagram logins are made from this server.
"""
import csv
import io
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from app.campaign import init as init_campaign
from app.insights import init as init_insights
from app.drive import init as init_drive

router=APIRouter()
TZ=ZoneInfo("America/Fortaleza")
SNAPSHOT_JOIN="""LEFT JOIN (
  SELECT s.job_id,s.views,s.likes,s.comments,s.capture_day,s.result
  FROM social_video_metrics s
  INNER JOIN (
    SELECT job_id,MAX(capture_day) AS last_day
    FROM social_video_metrics GROUP BY job_id
  ) latest ON latest.job_id=s.job_id AND latest.last_day=s.capture_day
) m ON m.job_id=j.id"""

def prepared(db):
    init_campaign(db)
    init_insights(db)
    init_drive(db)

def to_local(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d/%m/%Y %H:%M") if dt else "—"

def normalize(row):
    item=dict(row)
    item["scheduled_local"]=to_local(item["planned_utc"])
    overdue=item["status"]=="planned" and item["planned_utc"]<datetime.now(timezone.utc).replace(tzinfo=None)
    item["display_status"]="overdue" if overdue else item["status"]
    item["published_local"]=to_local(item.get("published_at"))
    item["views_display"]=str(item["views"]) if item.get("views") is not None else "N/D"
    item["likes_display"]=str(item["likes"]) if item.get("likes") is not None else "N/D"
    item["comments_display"]=str(item["comments"]) if item.get("comments") is not None else "N/D"
    item["checked_display"]=str(item["capture_day"]) if item.get("capture_day") else "Não consultado"
    return item

def report(db):
    """All counts reflect persisted jobs; no invented reach or view values."""
    prepared(db)
    with db.cursor() as c:
        c.execute("SELECT COUNT(*) AS n FROM social_daily_jobs WHERE status='planned' AND planned_utc<UTC_TIMESTAMP()")
        overdue=c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM social_accounts")
        account_count=c.fetchone()["n"]
        c.execute("SELECT status,COUNT(*) AS n FROM social_daily_jobs GROUP BY status")
        statuses={row["status"]:row["n"] for row in c.fetchall()}
        c.execute("SELECT status,COUNT(*) AS n FROM social_accounts GROUP BY status")
        account_status={row["status"]:row["n"] for row in c.fetchall()}
        c.execute("SELECT COUNT(*) AS n FROM social_drive_assets")
        asset_count=c.fetchone()["n"]
        c.execute("SELECT COUNT(*) AS n FROM social_google_tokens")
        drive_connected=bool(c.fetchone()["n"])
        c.execute("SELECT folder_id,start_date,days FROM social_campaign_settings WHERE id=1")
        settings=c.fetchone()
        c.execute(f"""SELECT COUNT(*) AS reel_count, COALESCE(SUM(m.views),0) AS known_views,
                COALESCE(SUM(m.likes),0) AS known_likes,
                COALESCE(SUM(m.comments),0) AS known_comments,
                COUNT(m.views) AS reels_with_views
            FROM social_daily_jobs j {SNAPSHOT_JOIN}
            WHERE j.status='published'""")
        known=c.fetchone()
        c.execute(f"""SELECT a.id,a.username,a.status,
                COUNT(j.id) AS scheduled,
                SUM(CASE WHEN j.status='published' THEN 1 ELSE 0 END) AS published,
                SUM(CASE WHEN j.status='planned' THEN 1 ELSE 0 END) AS planned,
                SUM(CASE WHEN j.status IN ('missed','needs_review','failed') THEN 1 ELSE 0 END) AS attention,
                COALESCE(SUM(CASE WHEN j.status='published' THEN m.views END),0) AS known_views
            FROM social_accounts a
            LEFT JOIN social_daily_jobs j ON j.account_id=a.id
            {SNAPSHOT_JOIN}
            GROUP BY a.id,a.username,a.status ORDER BY known_views DESC,a.username""")
        accounts=c.fetchall()
        fields="""j.id,j.local_day,j.planned_utc,j.published_at,j.status,j.permalink,
                  j.caption,a.username,d.name AS video,
                  m.views,m.likes,m.comments,m.capture_day,m.result AS metrics_result"""
        common=f"""FROM social_daily_jobs j JOIN social_accounts a ON a.id=j.account_id
                   LEFT JOIN social_drive_assets d ON d.drive_id=j.drive_id
                   {SNAPSHOT_JOIN}"""
        c.execute(f"""SELECT {fields} {common}
            WHERE j.status='planned' AND j.planned_utc>=UTC_TIMESTAMP()
            ORDER BY j.planned_utc ASC LIMIT 80""")
        upcoming=[normalize(row) for row in c.fetchall()]
        c.execute(f"""SELECT {fields} {common}
            WHERE j.status<>'planned'
               OR (j.status='planned' AND j.planned_utc<UTC_TIMESTAMP())
            ORDER BY COALESCE(j.published_at,j.planned_utc) DESC LIMIT 120""")
        history=[normalize(row) for row in c.fetchall()]
        c.execute("""SELECT local_day,
            SUM(status='published') AS published,
            SUM(status='missed') AS missed,
            SUM(status='needs_review') AS review,
            COUNT(*) AS total
            FROM social_daily_jobs
            WHERE local_day BETWEEN DATE_SUB(CURDATE(),INTERVAL 29 DAY)
                AND DATE_ADD(CURDATE(),INTERVAL 1 DAY)
            GROUP BY local_day ORDER BY local_day ASC""")
        daily=[{**row,"date":str(row["local_day"])} for row in c.fetchall()]
    return {
        "accounts_count":account_count,"asset_count":asset_count,
        "drive_connected":drive_connected,"campaign":settings,
        "statuses":statuses,"account_status":account_status,
        "totals":known,"accounts":accounts,"upcoming":upcoming,
        "history":history,"daily":daily,
        "overdue":overdue,
        "needs_attention":overdue+sum(statuses.get(s,0) for s in ("missed","needs_review","failed")),
    }

def csv_cell(v):
    if v is None: return ""
    value=str(v)
    # Guard against formula injection when exporting to spreadsheets.
    if value.lstrip().startswith(("=","+","-","@")):
        return "'"+value
    return value

@router.get("/reports/publications.csv")
def export_csv(request:Request):
    from app.main import admin, db_connection
    if not admin(request):
        raise HTTPException(status_code=403, detail="Forbidden")
    with db_connection() as db:
        prepared(db)
        with db.cursor() as c:
            c.execute(f"""SELECT j.id,a.username,j.local_day,j.planned_utc,j.published_at,
                   j.status,d.name AS video,j.permalink,m.views,m.likes,m.comments,
                   m.capture_day
                   FROM social_daily_jobs j JOIN social_accounts a ON a.id=j.account_id
                   LEFT JOIN social_drive_assets d ON d.drive_id=j.drive_id
                   {SNAPSHOT_JOIN}
                   ORDER BY j.planned_utc DESC LIMIT 15000""")
            rows=c.fetchall()
    buffer=io.StringIO()
    writer=csv.writer(buffer,delimiter=";")
    writer.writerow(["ID","Conta","Dia","Horário Fortaleza","Publicado em Fortaleza",
                     "Status","Vídeo","Link Instagram","Views","Curtidas",
                     "Comentários","Última consulta"])
    for row in rows:
        writer.writerow([csv_cell(row["id"]),csv_cell(row["username"]),
            csv_cell(row["local_day"]),csv_cell(to_local(row["planned_utc"])),
            csv_cell(to_local(row["published_at"])),csv_cell(row["status"]),
            csv_cell(row["video"]),csv_cell(row["permalink"]),
            csv_cell(row["views"]),csv_cell(row["likes"]),
            csv_cell(row["comments"]),csv_cell(row["capture_day"])])
    body="\ufeff"+buffer.getvalue()
    return StreamingResponse(iter([body.encode("utf-8")]),media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":"attachment; filename=social-leaguecut-relatorio.csv",
                 "Cache-Control":"no-store"})
