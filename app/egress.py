"""Preflight routing for the future Instagram publisher.

A random proxy is chosen before opening an account session; only network
connectivity is tested. No Instagram request, authentication bypass, or
automatic repost is performed here.
"""
import os, random, json, urllib.request, urllib.parse, ipaddress
from cryptography.fernet import Fernet
from app.proxies import init

def candidates(db):
    init(db)
    with db.cursor() as cur:
        cur.execute("SELECT id,label,host,port,user_secret,pass_secret,last_result FROM social_proxies WHERE last_result='ok'")
        rows=cur.fetchall()
    random.SystemRandom().shuffle(rows)
    return rows

def outbound_route(db, allow_direct=False):
    """Returns a verified proxy route or None; rejects direct fallback unless explicitly enabled."""
    key=os.environ.get("PROXY_ENCRYPTION_KEY")
    if not key:return {"status":"paused","reason":"encryption key missing"}
    cipher=Fernet(key.encode())
    attempts=[]
    for p in candidates(db):
        try:
            user=urllib.parse.quote(cipher.decrypt(p["user_secret"].encode()).decode(),safe="")
            password=urllib.parse.quote(cipher.decrypt(p["pass_secret"].encode()).decode(),safe="")
            auth=f"{user}:{password}@" if user else ""
            url=f"http://{auth}{p['host']}:{p['port']}"
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({"http":url,"https":url}))
            with opener.open("https://api.ipify.org?format=json",timeout=8) as res:
                ip=str(ipaddress.ip_address(json.load(res)["ip"]))
            return {"status":"ready","proxy_id":p["id"],"proxy_name":p["label"],"ip":ip,"connection_url":url,"attempts":attempts}
        except Exception:
            attempts.append({"proxy_id":p["id"],"status":"unavailable"})
    if allow_direct and os.environ.get("ALLOW_DIRECT_EGRESS","false").lower()=="true":
        return {"status":"direct_opt_in","proxy_id":None,"attempts":attempts}
    return {"status":"paused","reason":"all proxies unavailable","attempts":attempts}
