"""Account routing and non-publishing queue preview.

Assign a stable proxy to each account. Never fall back to the VPS IP.
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from urllib.parse import quote
from app.proxy_policy import assign_proxy

router = APIRouter()

def init(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS social_account_routes (
            account_id INT PRIMARY KEY, proxy_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS social_job_events (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            post_id INT NOT NULL, account_id INT NOT NULL,
            event VARCHAR(32) NOT NULL, detail VARCHAR(255) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
    db.commit()

def auto_assign_proxy(db, account_id: int):
    """Balance new accounts over healthy proxies and keep each assignment stable."""
    init(db)
    with db.cursor() as c:
        c.execute("""SELECT p.id,COUNT(r.account_id) AS assignments
          FROM social_proxies p
          LEFT JOIN social_account_routes r ON r.proxy_id=p.id
          WHERE p.last_result='ok'
          GROUP BY p.id
          ORDER BY assignments ASC, RAND()
          LIMIT 1""")
        choice=c.fetchone()
        if not choice:
            return None
        c.execute("INSERT IGNORE INTO social_account_routes(account_id,proxy_id) VALUES(%s,%s)",
                  (account_id,choice["id"]))
    db.commit()
    return choice["id"]

def list_routes(db):
    init(db)
    with db.cursor() as c:
        c.execute("""SELECT a.id AS account_id,a.username,r.proxy_id,
          p.label AS proxy_label,p.last_result AS proxy_status
          FROM social_accounts a
          LEFT JOIN social_account_routes r ON r.account_id=a.id
          LEFT JOIN social_proxies p ON p.id=r.proxy_id ORDER BY a.id""")
        return c.fetchall()

def preview(db):
    init(db)
    with db.cursor() as c:
        c.execute("""SELECT sp.id AS post_id,sp.title,sp.scheduled_at,
          a.id AS account_id,a.username,r.proxy_id,p.label AS proxy_label,
          p.last_result AS proxy_status
          FROM social_post_accounts pa
          JOIN social_posts sp ON sp.id=pa.post_id
          JOIN social_accounts a ON a.id=pa.account_id
          LEFT JOIN social_account_routes r ON r.account_id=a.id
          LEFT JOIN social_proxies p ON p.id=r.proxy_id
          WHERE sp.status='draft'
          ORDER BY sp.scheduled_at ASC LIMIT 200""")
        rows=c.fetchall()
        for row in rows:
            row["readiness"]="proxy ready" if row["proxy_id"] and row["proxy_status"]=="ok" else "proxy needed"
        return rows

def authorized(request,csrf):
    from app.main import admin,form_allowed
    return admin(request) and form_allowed(request,csrf)

@router.post("/accounts/{account_id}/route")
def route(request:Request,account_id:int,proxy_id:int=Form(...),csrf:str=Form(...)):
    if not authorized(request,csrf):
        return JSONResponse({"error":"Forbidden"},status_code=403)
    from app.main import db_connection
    with db_connection() as db:
        init(db)
        with db.cursor() as c:
            c.execute("SELECT id FROM social_accounts WHERE id=%s",(account_id,))
            if not c.fetchone():
                return RedirectResponse("/?error=Conta inexistente",status_code=303)
            if proxy_id==0:
                c.execute("DELETE FROM social_account_routes WHERE account_id=%s",(account_id,))
            else:
                c.execute("SELECT id FROM social_proxies WHERE id=%s AND last_result='ok'",(proxy_id,))
                if not c.fetchone():
                    return RedirectResponse("/?error=Teste o proxy antes",status_code=303)
                c.execute("""INSERT INTO social_account_routes(account_id,proxy_id)
                 VALUES(%s,%s) ON DUPLICATE KEY UPDATE proxy_id=VALUES(proxy_id)""",(account_id,proxy_id))
        db.commit()
    return RedirectResponse("/",status_code=303)
