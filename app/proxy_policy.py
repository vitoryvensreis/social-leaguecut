"""Proxy allocation for future workers.

Stable, deterministic assignment by account; fail closed when connection fails.
Do not interpret Instagram authentication challenges or rate limits as proxy failures.
"""
import hashlib
from app.proxies import init

def assign_proxy(db, account_id: int):
    init(db)
    with db.cursor() as cur:
        cur.execute("SELECT id,host,port,last_result FROM social_proxies WHERE last_result='ok' ORDER BY id")
        proxies = cur.fetchall()
    if not proxies:
        return None
    selection = int.from_bytes(hashlib.sha256(str(account_id).encode()).digest()[:8], "big") % len(proxies)
    return proxies[selection]

def should_retry_network_error(exc):
    """Only connection-level failures qualify for a cautious retry; no session/IP rotation."""
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))
