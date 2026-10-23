"""Instagram Login (Meta) scheduling and per-Reel analytics for Social LeagueCut.

The legacy Playwright/Instagrapi modules are intentionally not imported.
Tokens are pasted by the administrator, encrypted in MySQL, and never returned.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request as URLRequest, urlopen
from zoneinfo import ZoneInfo

import pymysql
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from app.meta_discovery import init_extra, sync_account
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TZ = ZoneInfo("America/Fortaleza")
LOG = logging.getLogger("social.meta")
VERSION = os.getenv("META_GRAPH_VERSION", "v25.0")
BASE = "https://graph.instagram.com"
MEDIA_DIR = Path(os.getenv("META_MEDIA_DIR", str(ROOT.parent / "social-meta-media")))
PUBLIC_URL = os.getenv("META_PUBLIC_URL", "https://social.leaguecut.com.br").rstrip("/")
MAX_UPLOAD = 250 * 1024 * 1024
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
app = FastAPI(title="Social LeagueCut · Meta", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or "UNCONFIGURED_SOCIAL_SESSION",
    https_only=True, same_site="lax",
)

class GraphError(Exception):
    pass

def db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False, connect_timeout=4,
    )

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meta_accounts (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            ig_scoped_id VARCHAR(64) NOT NULL UNIQUE,
            instagram_user_id VARCHAR(64) NULL,
            username VARCHAR(100) NOT NULL,
            token_encrypted TEXT NOT NULL,
            token_saved_at DATETIME NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            error_hint VARCHAR(255) NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_meta_accounts_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute("""CREATE TABLE IF NOT EXISTS meta_posts (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            account_id BIGINT UNSIGNED NOT NULL,
            video_url TEXT NOT NULL,
            caption TEXT NOT NULL,
            scheduled_at_utc DATETIME NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'scheduled',
            container_id VARCHAR(80) NULL,
            media_id VARCHAR(80) NULL,
            permalink TEXT NULL,
            error_hint VARCHAR(255) NULL,
            checked_at DATETIME NULL,
            published_at DATETIME NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_meta_jobs (status, scheduled_at_utc),
            INDEX idx_meta_post_account (account_id, scheduled_at_utc)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute("""CREATE TABLE IF NOT EXISTS meta_metrics (
            post_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
            views BIGINT NULL, reach BIGINT NULL,
            likes BIGINT NULL, comments BIGINT NULL,
            saved BIGINT NULL, shares BIGINT NULL,
            checked_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute("""CREATE TABLE IF NOT EXISTS meta_metric_history (
            post_id BIGINT UNSIGNED NOT NULL,
            capture_day DATE NOT NULL,
            views BIGINT NULL, reach BIGINT NULL,
            likes BIGINT NULL, comments BIGINT NULL,
            saved BIGINT NULL, shares BIGINT NULL,
            checked_at DATETIME NOT NULL,
            PRIMARY KEY (post_id,capture_day)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    db.commit()
    init_extra(db)

def encryption():
    key = os.getenv("META_TOKEN_KEY", "")
    if key:
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError("META_TOKEN_KEY inválida; gere uma chave Fernet.") from exc
    # A distinct derived key allows existing VPS installs to upgrade without
    # writing secrets to Git. Prefer a dedicated META_TOKEN_KEY in production.
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 24:
        raise RuntimeError("Configure META_TOKEN_KEY ou SESSION_SECRET forte.")
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode(), b"social-leaguecut:meta-token:v1", 200000)
    return Fernet(base64.urlsafe_b64encode(raw))

def seal(value):
    return encryption().encrypt(value.encode()).decode()

def unseal(value):
    return encryption().decrypt(value.encode()).decode()

def graph(path, token, params=None, method="GET"):
    """Token is sent in HTTPS Authorization header, never in rendered HTML."""
    if not re.fullmatch(r"[a-zA-Z0-9_/,.-]+", path):
        raise ValueError("Invalid Graph path")
    url = BASE + "/" + VERSION + "/" + path
    encoded = urlencode(params or {})
    if method == "GET" and encoded:
        url += "?" + encoded
    request = URLRequest(
        url, data=encoded.encode() if method == "POST" else None,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "SocialLeagueCut/1.0"},
        method=method,
    )
    try:
        with urlopen(request, timeout=24) as response:
            return json.loads(response.read(1024 * 1024))
    except HTTPError as exc:
        try:
            data = json.loads(exc.read(65536))
            err = data.get("error", {})
            message = str(err.get("message", "Requisição recusada pela Meta"))
            code = err.get("code", exc.code)
            raise GraphError("Meta " + str(code) + ": " + message[:170]) from None
        except (ValueError, AttributeError):
            raise GraphError("Meta HTTP " + str(exc.code)) from None
    except (URLError, TimeoutError) as exc:
        raise GraphError("Sem resposta confirmada da Meta (" + type(exc).__name__ + ")") from None

def account_profile(token):
    try:
        data = graph("me", token, {"fields": "id,user_id,username,account_type"})
    except GraphError:
        data = graph("me", token, {"fields": "id,username"})
    scoped_id = str(data.get("id") or "").strip()
    username = str(data.get("username") or "").strip()
    if not scoped_id.isdigit() or not username:
        raise GraphError("Token não retornou um perfil profissional identificável.")
    return scoped_id, str(data.get("user_id") or ""), username

def validate_media_url(value):
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or not hostname or hostname in ("localhost", "127.0.0.1", "::1")
                or hostname.endswith((".local", ".internal"))
                or len(value) > 1800):
            return False
        # Never give Meta an obvious private-network IP literal.
        import ipaddress
        try:
            addr = ipaddress.ip_address(hostname)
            if not addr.is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False

def authenticated(request):
    return (request.session.get("authenticated") is True
            and bool(os.getenv("ADMIN_PASSWORD")) and bool(os.getenv("SESSION_SECRET")))

def check_csrf(request, csrf):
    return bool(csrf and secrets.compare_digest(
        str(csrf), str(request.session.get("csrf") or "")))

def require_form(request, csrf):
    if not authenticated(request):
        raise HTTPException(status_code=401, detail="Faça login.")
    if not check_csrf(request, csrf):
        raise HTTPException(status_code=403, detail="Sessão inválida. Atualize a página.")

def go(message="", error=False):
    from urllib.parse import quote
    kind = "error" if error else "success"
    return RedirectResponse("/" + ("?" + kind + "=" + quote(message) if message else ""), status_code=303)

def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def fmt_local(value):
    if not value:
        return "—"
    return value.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d/%m/%Y %H:%M")

@app.get("/health")
def health():
    return {"status": "ok", "service": "social-leaguecut-meta"}

@app.get("/")
def home(request: Request):
    csrf = request.session.get("csrf")
    if not csrf:
        csrf = secrets.token_urlsafe(32)
        request.session["csrf"] = csrf
    values = {"request": request, "logged": authenticated(request), "csrf": csrf,
              "error": request.query_params.get("error", ""),
              "success": request.query_params.get("success", ""),
              "configured": all(os.getenv(k) for k in
                  ("ADMIN_PASSWORD", "SESSION_SECRET", "DB_USER", "DB_PASSWORD", "DB_NAME")),
              "accounts": [], "posts": [], "top_posts": [], "totals": {}, "now_local":
                  datetime.now(TZ).strftime("%Y-%m-%dT%H:%M")}
    if authenticated(request):
        try:
            with db_connection() as db:
                init(db)
                with db.cursor() as c:
                    c.execute("""SELECT a.id,a.username,a.ig_scoped_id,a.instagram_user_id,
                            a.status,a.error_hint,a.token_saved_at,
                            pr.name,pr.biography,pr.website,pr.account_type,pr.avatar_url,
                            pr.followers_count,pr.follows_count,pr.media_count,
                            pr.yesterday_views,pr.yesterday_reach,pr.yesterday_profile_visits,
                            pr.yesterday_accounts_engaged,pr.insights_day,pr.insights_error,
                            pr.media_error,pr.synced_at,pr.imported_count,
                            (SELECT COUNT(*) FROM meta_posts p WHERE p.account_id=a.id) jobs,
                            (SELECT COUNT(*) FROM meta_posts p WHERE p.account_id=a.id
                               AND p.status IN ('published','imported')) published
                        FROM meta_accounts a
                        LEFT JOIN meta_account_profiles pr ON pr.account_id=a.id
                        ORDER BY a.username""")
                    values["accounts"] = c.fetchall()
                    c.execute("""SELECT p.id,p.caption,p.scheduled_at_utc,p.status,
                            p.container_id,p.media_id,p.permalink,p.error_hint,
                            p.video_url,p.published_at,a.username,
                            m.views,m.reach,m.likes,m.comments,m.saved,m.shares,m.checked_at,
                            d.media_type,d.product_type,d.thumbnail_url,d.media_url
                        FROM meta_posts p
                        JOIN meta_accounts a ON a.id=p.account_id
                        LEFT JOIN meta_metrics m ON m.post_id=p.id
                        LEFT JOIN meta_media_details d ON d.media_id=p.media_id
                        ORDER BY p.scheduled_at_utc DESC LIMIT 200""")
                    values["posts"] = c.fetchall()
                    c.execute("""SELECT
                        COUNT(*) total,
                        SUM(status IN ('published','imported')) published,
                        SUM(status='scheduled') scheduled,
                        SUM(status IN ('failed','needs_review')) attention,
                        SUM(status='imported') imported
                        FROM meta_posts""")
                    values["totals"] = c.fetchone()
                    c.execute("""SELECT COALESCE(SUM(m.views),0) views,
                        COALESCE(SUM(m.likes),0) likes,
                        COALESCE(SUM(m.comments),0) comments
                        FROM meta_metrics m
                        JOIN meta_posts p ON p.id=m.post_id WHERE p.status IN ('published','imported')""")
                    values["totals"].update(c.fetchone())
                    c.execute("""SELECT p.id,p.caption,p.permalink,a.username,m.views
                        FROM meta_posts p
                        JOIN meta_accounts a ON a.id=p.account_id
                        JOIN meta_metrics m ON m.post_id=p.id
                        WHERE p.status IN ('published','imported') AND m.views IS NOT NULL
                        ORDER BY m.views DESC,p.id DESC LIMIT 5""")
                    values["top_posts"] = c.fetchall()
            values["top_max"] = max([int(p["views"]) for p in values["top_posts"]] + [1])
            for a in values["accounts"]:
                a["synced_local"] = fmt_local(a.get("synced_at"))
                a["token_saved_local"] = fmt_local(a.get("token_saved_at"))
            for p in values["posts"]:
                p["scheduled_local"] = fmt_local(p["scheduled_at_utc"])
                p["published_local"] = fmt_local(p["published_at"])
                p["checked_local"] = fmt_local(p["checked_at"])
        except Exception:
            LOG.exception("Dashboard database error")
            values["error"] = "Erro ao consultar MySQL. Confira o serviço e as configurações."
    response = templates.TemplateResponse(request, "meta_dashboard.html", values)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.post("/login")
def login(request: Request, password: str = Form(...), csrf: str = Form(...)):
    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected or not os.getenv("SESSION_SECRET") or not check_csrf(request, csrf):
        return go("Configuração ou sessão inválida.", True)
    if secrets.compare_digest(password.encode(), expected.encode()):
        request.session.clear()
        request.session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
        return go()
    return go("Senha incorreta.", True)

@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    require_form(request, csrf)
    request.session.clear()
    return go()

@app.post("/accounts")
def save_account(request: Request, csrf: str = Form(...), token: str = Form(...)):
    require_form(request, csrf)
    token = token.strip()
    if not (30 <= len(token) <= 2048):
        return go("Token inválido ou incompleto.", True)
    try:
        scoped_id, user_id, username = account_profile(token)
        encrypted = seal(token)
        with db_connection() as db:
            init(db)
            with db.cursor() as c:
                c.execute("""INSERT INTO meta_accounts
                    (ig_scoped_id,instagram_user_id,username,token_encrypted,token_saved_at,status,error_hint)
                    VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(),'active',NULL)
                    ON DUPLICATE KEY UPDATE
                    instagram_user_id=VALUES(instagram_user_id),
                    username=VALUES(username), token_encrypted=VALUES(token_encrypted),
                    token_saved_at=UTC_TIMESTAMP(),status='active',error_hint=NULL""",
                    (scoped_id, user_id, username, encrypted))
                c.execute("SELECT id FROM meta_accounts WHERE ig_scoped_id=%s", (scoped_id,))
                account_id = c.fetchone()["id"]
            db.commit()
            try:
                outcome = sync_account(db, account_id, graph, get_token, max_pages=1)
                return go("Conta @" + username + " conectada. " +
                          str(outcome["imported"]) + " publicações importadas; consulte o painel.")
            except Exception:
                LOG.warning("New token saved but initial profile sync unavailable account=%s", account_id)
                return go("Conta @" + username +
                          " conectada. Clique em 'Sincronizar dados' para importar posts e métricas.")
    except (GraphError, RuntimeError, pymysql.MySQLError) as exc:
        return go("Não foi possível cadastrar: " + str(exc)[:190], True)

@app.post("/accounts/{account_id}/sync")
def sync_account_action(account_id: int, request: Request, csrf: str = Form(...)):
    require_form(request, csrf)
    try:
        with db_connection() as db:
            init(db)
            result = sync_account(db, account_id, graph, get_token)
        message = ("@" + result["username"] + ": perfil atualizado; " +
                   str(result["imported"]) + " post(s) novo(s) importado(s).")
        if result["media_error"] or result["insights_partial"]:
            message += " Algumas informações não foram disponibilizadas pela Meta."
        return go(message)
    except GraphError as exc:
        return go("Sincronização não autorizada: " + str(exc)[:160], True)
    except Exception:
        LOG.exception("Failed account sync id=%s", account_id)
        return go("Não foi possível sincronizar agora. Verifique o token e as permissões na Meta.", True)

@app.post("/accounts/{account_id}/toggle")
def toggle_account(account_id: int, request: Request, csrf: str = Form(...)):
    require_form(request, csrf)
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("""UPDATE meta_accounts SET status=IF(status='paused','active','paused')
                WHERE id=%s""", (account_id,))
        db.commit()
    return go("Situação da conta atualizada.")

@app.post("/schedule")
def schedule(request: Request, csrf: str = Form(...),
             account_ids: list[int] = Form(...), video_url: str = Form(...),
             caption: str = Form(""), scheduled_at: str = Form(...),
             interval_minutes: int = Form(0)):
    require_form(request, csrf)
    account_ids = sorted(set(account_ids))
    if not account_ids or len(account_ids) > 100:
        return go("Selecione de 1 a 100 contas.", True)
    if not validate_media_url(video_url.strip()):
        return go("Informe URL de vídeo HTTPS pública (ou envie um MP4 primeiro).", True)
    if not 0 <= interval_minutes <= 180 or len(caption) > 2200:
        return go("Legenda ou intervalo inválido.", True)
    try:
        local = datetime.strptime(scheduled_at, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
        start = local.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return go("Data ou horário inválido.", True)
    if start < now_utc() - timedelta(minutes=2):
        return go("Selecione um horário atual ou futuro.", True)
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            placeholders = ",".join(["%s"] * len(account_ids))
            c.execute("SELECT id FROM meta_accounts WHERE status='active' AND id IN (" +
                      placeholders + ")", account_ids)
            active = {int(a["id"]) for a in c.fetchall()}
            if len(active) != len(account_ids):
                return go("Uma das contas está pausada ou não existe.", True)
            for idx, account_id in enumerate(account_ids):
                when = start + timedelta(minutes=idx * interval_minutes)
                c.execute("""INSERT INTO meta_posts
                    (account_id,video_url,caption,scheduled_at_utc,status)
                    VALUES (%s,%s,%s,%s,'scheduled')""",
                    (account_id, video_url.strip(), caption, when))
        db.commit()
    return go(str(len(account_ids)) + " publicação(ões) agendada(s) no fuso de Fortaleza.")

@app.post("/batch")
def batch_schedule(request: Request, csrf: str = Form(...),
                   account_ids: list[int] = Form(...),
                   video_urls_text: str = Form(...),
                   caption: str = Form(""), first_date: str = Form(...),
                   first_time: str = Form("10:00"), interval_minutes: int = Form(30)):
    """Schedule one Reel per day per selected account; stagger accounts."""
    require_form(request, csrf)
    account_ids = sorted(set(account_ids))
    urls = [s.strip() for s in video_urls_text.splitlines() if s.strip()]
    if not 1 <= len(urls) <= 60 or not 1 <= len(account_ids) <= 100:
        return go("Use 1–60 URLs de vídeos e selecione as contas.", True)
    if any(not validate_media_url(url) for url in urls):
        return go("Cada linha deve conter uma URL HTTPS pública válida.", True)
    if len(caption) > 2200 or not 0 <= interval_minutes <= 180:
        return go("Legenda ou intervalo inválido.", True)
    try:
        day = datetime.strptime(first_date, "%Y-%m-%d").date()
        hour = datetime.strptime(first_time, "%H:%M").time()
    except ValueError:
        return go("Data ou horário inválido.", True)
    last_slot = datetime.combine(day, hour) + timedelta(minutes=(len(account_ids)-1)*interval_minutes)
    if hour.hour < 10 or last_slot.hour >= 19:
        return go("Escolha horários dentro da janela 10:00–19:00 de Fortaleza.", True)
    inserted = skipped = 0
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            placeholders = ",".join(["%s"] * len(account_ids))
            c.execute("SELECT id FROM meta_accounts WHERE status='active' AND id IN (" +
                      placeholders + ")", account_ids)
            if len(c.fetchall()) != len(account_ids):
                return go("Há contas pausadas ou inexistentes na seleção.", True)
            for day_idx, url in enumerate(urls):
                local_start = datetime.combine(day + timedelta(days=day_idx), hour).replace(tzinfo=TZ)
                for idx, account_id in enumerate(account_ids):
                    planned = (local_start + timedelta(minutes=idx * interval_minutes))
                    when = planned.astimezone(timezone.utc).replace(tzinfo=None)
                    if when < now_utc():
                        skipped += 1
                        continue
                    # Idempotent from the operator perspective: do not book a
                    # second Reel in an occupied slot for this account.
                    c.execute("""SELECT id FROM meta_posts
                        WHERE account_id=%s AND scheduled_at_utc=%s
                          AND status<>'cancelled' LIMIT 1""", (account_id, when))
                    if c.fetchone():
                        skipped += 1
                        continue
                    c.execute("""INSERT INTO meta_posts
                        (account_id,video_url,caption,scheduled_at_utc,status)
                        VALUES (%s,%s,%s,%s,'scheduled')""",
                        (account_id, url, caption, when))
                    inserted += 1
        db.commit()
    return go(str(inserted) + " Reels agendados; " + str(skipped) +
              " horários anteriores ou já ocupados foram ignorados.")

@app.post("/posts/{post_id}/cancel")
def cancel(post_id: int, request: Request, csrf: str = Form(...)):
    require_form(request, csrf)
    with db_connection() as db:
        with db.cursor() as c:
            c.execute("UPDATE meta_posts SET status='cancelled' WHERE id=%s AND status='scheduled'",
                      (post_id,))
            changed = c.rowcount
        db.commit()
    return go("Agendamento cancelado." if changed else "O job já foi iniciado; não cancelado.", not changed)

@app.post("/upload")
async def upload(request: Request, csrf: str = Form(...), video: UploadFile = File(...)):
    require_form(request, csrf)
    if not video.filename or not video.filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Envie um arquivo MP4.")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = secrets.token_hex(20) + ".mp4"
    target = MEDIA_DIR / name
    total = 0
    try:
        with target.open("xb") as handle:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Limite: 250 MB.")
                handle.write(chunk)
        if total < 1024:
            raise HTTPException(status_code=400, detail="Vídeo vazio.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await video.close()
    return JSONResponse({"url": PUBLIC_URL + "/media/" + name, "bytes": total},
        headers={"Cache-Control": "no-store"})

@app.get("/media/{name}")
def media(name: str):
    if not re.fullmatch(r"[0-9a-f]{40}\.mp4", name):
        raise HTTPException(status_code=404)
    file = MEDIA_DIR / name
    if not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file, media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=3600", "Accept-Ranges": "bytes"})

def get_token(db, account_id):
    with db.cursor() as c:
        c.execute("""SELECT token_encrypted,
                  COALESCE(NULLIF(instagram_user_id,''),ig_scoped_id) AS publish_id
                  FROM meta_accounts WHERE id=%s AND status='active'""",
                  (account_id,))
        data = c.fetchone()
    if not data:
        raise GraphError("Conta inexistente ou pausada.")
    return unseal(data["token_encrypted"]), data["publish_id"]

def set_state(db, post_id, expected, new, **fields):
    assignments = ["status=%s"]
    args = [new]
    for field, value in fields.items():
        if field not in ("container_id", "media_id", "permalink", "error_hint",
                         "published_at", "checked_at"):
            raise ValueError("Invalid database field")
        assignments.append(field + "=%s")
        args.append(value)
    args.extend([post_id, expected])
    with db.cursor() as c:
        c.execute("UPDATE meta_posts SET " + ",".join(assignments) +
                  " WHERE id=%s AND status=%s", args)
        changed = c.rowcount
    db.commit()
    return changed == 1

def set_problem(db, post_id, status, message):
    with db.cursor() as c:
        c.execute("UPDATE meta_posts SET status=%s,error_hint=%s WHERE id=%s",
                  (status, str(message)[:240], post_id))
    db.commit()

def publish_cycle(db, limit=20):
    with db.cursor() as c:
        c.execute("""SELECT id,account_id,video_url,caption,status,container_id,updated_at
             FROM meta_posts WHERE
                (status='scheduled' AND scheduled_at_utc<=UTC_TIMESTAMP())
                OR (status='processing' AND updated_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 15 SECOND))
                OR (status IN ('creating','publishing')
                    AND updated_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 10 MINUTE))
             ORDER BY scheduled_at_utc,id LIMIT %s""", (limit,))
        jobs = c.fetchall()
    for job in jobs:
        pid = job["id"]
        state = job["status"]
        if state in ("creating", "publishing"):
            # A network response may have been lost after the external write.
            # Never automatically repeat an uncertain publish request.
            set_state(db, pid, state, "needs_review",
                error_hint="Resultado incerto. Confira o Instagram antes de reagendar.")
            continue
        try:
            token, ig_id = get_token(db, job["account_id"])
            if state == "scheduled":
                if not set_state(db, pid, "scheduled", "creating"):
                    continue
                created = graph(ig_id + "/media", token, {
                    "media_type": "REELS", "video_url": job["video_url"],
                    "caption": job["caption"], "share_to_feed": "true"}, "POST")
                if not created.get("id"):
                    raise GraphError("A Meta não informou o identificador do contêiner.")
                set_state(db, pid, "creating", "processing", container_id=str(created["id"]))
            else:
                details = graph(str(job["container_id"]), token, {"fields": "status_code,status"})
                result = details.get("status_code", "")
                if result == "FINISHED":
                    if not set_state(db, pid, "processing", "publishing"):
                        continue
                    published = graph(ig_id + "/media_publish", token,
                                      {"creation_id": job["container_id"]}, "POST")
                    media_id = str(published.get("id", ""))
                    if not media_id:
                        raise GraphError("A Meta não confirmou o ID do Reel.")
                    # This DB write is deliberately before the optional permalink read.
                    set_state(db, pid, "publishing", "published",
                              media_id=media_id, published_at=now_utc(), error_hint=None)
                    try:
                        data = graph(media_id, token, {"fields": "id,permalink"})
                        with db.cursor() as c:
                            c.execute("UPDATE meta_posts SET permalink=%s WHERE id=%s",
                                      (data.get("permalink"), pid))
                        db.commit()
                    except GraphError:
                        LOG.warning("Permalink unavailable for post %s", pid)
                elif result in ("ERROR", "EXPIRED"):
                    set_state(db, pid, "processing", "failed",
                        error_hint="A Meta informou contêiner " + result)
                elif job["updated_at"] < now_utc() - timedelta(hours=23):
                    set_state(db, pid, "processing", "failed",
                        error_hint="O contêiner não ficou pronto dentro de 23 horas.")
        except (GraphError, RuntimeError) as exc:
            # No automatic retry: an HTTP timeout after /media_publish could
            # otherwise create a second Reel.
            current = "needs_review" if state == "processing" else "failed"
            with db.cursor() as c:
                c.execute("SELECT status FROM meta_posts WHERE id=%s", (pid,))
                current_state = c.fetchone()["status"]
            if current_state in ("creating", "publishing"):
                current = "needs_review" if current_state == "publishing" else "failed"
            set_problem(db, pid, current, exc)
            LOG.warning("Post %s requires attention: %s", pid, type(exc).__name__)
        except Exception:
            LOG.exception("Unexpected job failure %s", pid)
            set_problem(db, pid, "needs_review", "Falha inesperada; revisão manual necessária.")

def extract_metric(payload):
    series = payload.get("data") or []
    if not series:
        return None
    node = series[0]
    if isinstance(node.get("total_value"), dict):
        return node["total_value"].get("value")
    values = node.get("values") or []
    return values[-1].get("value") if values else None

def metrics_cycle(db, limit=12):
    with db.cursor() as c:
        c.execute("""SELECT p.id,p.account_id,p.media_id
            FROM meta_posts p JOIN meta_accounts a ON a.id=p.account_id
            LEFT JOIN meta_metrics m ON m.post_id=p.id
            WHERE p.status IN ('published','imported') AND p.media_id IS NOT NULL
              AND a.status='active'
              AND (m.checked_at IS NULL
                   OR m.checked_at<DATE_SUB(UTC_TIMESTAMP(),INTERVAL 6 HOUR))
            ORDER BY COALESCE(m.checked_at,'1970-01-01') ASC,p.id DESC
            LIMIT %s""", (limit,))
        items = c.fetchall()
    for item in items:
        try:
            token, _ = get_token(db, item["account_id"])
            fields = graph(item["media_id"], token,
                           {"fields": "id,permalink,like_count,comments_count"})
            numbers = {"likes": fields.get("like_count"), "comments": fields.get("comments_count")}
            # Some metrics are unavailable for some media or app permissions.
            # Query separately so missing "reach" never hides available "views".
            for metric in ("views", "reach", "saved", "shares"):
                try:
                    value = extract_metric(graph(item["media_id"] + "/insights", token,
                                                 {"metric": metric}))
                    numbers[metric] = int(value) if isinstance(value, (int, float)) else None
                except GraphError:
                    numbers[metric] = None
            with db.cursor() as c:
                c.execute("""INSERT INTO meta_metrics
                    (post_id,views,reach,likes,comments,saved,shares,checked_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE
                    views=COALESCE(VALUES(views),views),
                    reach=COALESCE(VALUES(reach),reach),
                    likes=COALESCE(VALUES(likes),likes),
                    comments=COALESCE(VALUES(comments),comments),
                    saved=COALESCE(VALUES(saved),saved),
                    shares=COALESCE(VALUES(shares),shares),
                    checked_at=UTC_TIMESTAMP()""",
                    (item["id"], numbers["views"], numbers["reach"],
                     numbers["likes"], numbers["comments"], numbers["saved"], numbers["shares"]))
                c.execute("""INSERT INTO meta_metric_history
                    (post_id,capture_day,views,reach,likes,comments,saved,shares,checked_at)
                    VALUES (%s,UTC_DATE(),%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())
                    ON DUPLICATE KEY UPDATE
                    views=COALESCE(VALUES(views),views),
                    reach=COALESCE(VALUES(reach),reach),
                    likes=COALESCE(VALUES(likes),likes),
                    comments=COALESCE(VALUES(comments),comments),
                    saved=COALESCE(VALUES(saved),saved),
                    shares=COALESCE(VALUES(shares),shares),
                    checked_at=UTC_TIMESTAMP()""",
                    (item["id"], numbers["views"], numbers["reach"],
                     numbers["likes"], numbers["comments"], numbers["saved"], numbers["shares"]))
                if fields.get("permalink"):
                    c.execute("UPDATE meta_posts SET permalink=%s WHERE id=%s",
                              (fields["permalink"], item["id"]))
            db.commit()
        except (GraphError, RuntimeError):
            LOG.warning("Metrics unavailable post=%s", item["id"])
        except Exception:
            LOG.exception("Metrics read error for post=%s", item["id"])

def run_cycle():
    if not all(os.getenv(k) for k in ("DB_USER", "DB_PASSWORD", "DB_NAME")):
        return
    with db_connection() as db:
        init(db)
        publish_cycle(db)
        metrics_cycle(db)

async def service_loop():
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except Exception:
            LOG.exception("Meta service loop failed")
        await asyncio.sleep(45)

@app.on_event("startup")
async def start_worker():
    if os.getenv("META_WORKER_ENABLED", "true").lower() == "true":
        app.state.meta_task = asyncio.create_task(service_loop())

@app.on_event("shutdown")
async def stop_worker():
    task = getattr(app.state, "meta_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
