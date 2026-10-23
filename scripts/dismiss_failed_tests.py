"""One-time, non-destructive cleanup of the two failed test sessions.

The user asked to remove the erroneous test entries shown on Oct 22,
2026.  We only hide failed or uncertain entries for @vitoryvens from
the specific original test window.  We do not delete media, metrics,
tokens, published records, or any Instagram content.
"""
from datetime import datetime
from app.meta_platform import db_connection, init

USERNAME="vitoryvens"
# Two screenshots show tests at 22:14 and 22:42 on 2026-10-22
# in Fortaleza (UTC-3), so the UTC window is 2026-10-23 01:00-02:00.
START=datetime(2026,10,23,1,0,0)
END=datetime(2026,10,23,2,0,0)

def dismiss_old_tests(db):
    init(db)
    with db.cursor() as cur:
        cur.execute("""UPDATE meta_posts p
            JOIN meta_accounts a ON a.id=p.account_id
            SET p.status='dismissed'
            WHERE a.username=%s
              AND p.status IN ('failed','needs_review')
              AND p.scheduled_at_utc >= %s
              AND p.scheduled_at_utc < %s""",(USERNAME,START,END))
        changed=cur.rowcount
    db.commit()
    return changed

if __name__=="__main__":
    with db_connection() as connection:
        count=dismiss_old_tests(connection)
    print("OLD_FAILED_TESTS_HIDDEN",count)
