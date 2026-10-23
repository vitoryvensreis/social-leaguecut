"""Read a connected Instagram professional account through official Instagram Login APIs.

Profiles, published media, and available insights are copied to MySQL. Token
handling and Graph HTTPS requests live in meta_platform.py; this module never
discloses credentials or uses browser scraping.
"""
from datetime import datetime, timedelta, timezone

def init_extra(db):
    with db.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS meta_account_profiles (
            account_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
            name VARCHAR(255) NULL,
            biography TEXT NULL,
            website TEXT NULL,
            account_type VARCHAR(50) NULL,
            avatar_url TEXT NULL,
            followers_count BIGINT NULL,
            follows_count BIGINT NULL,
            media_count BIGINT NULL,
            yesterday_views BIGINT NULL,
            yesterday_reach BIGINT NULL,
            yesterday_profile_visits BIGINT NULL,
            yesterday_accounts_engaged BIGINT NULL,
            insights_day DATE NULL,
            synced_at DATETIME NULL,
            insights_error VARCHAR(255) NULL,
            media_error VARCHAR(255) NULL,
            imported_count INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        c.execute("""CREATE TABLE IF NOT EXISTS meta_media_details (
            media_id VARCHAR(80) NOT NULL PRIMARY KEY,
            account_id BIGINT UNSIGNED NOT NULL,
            media_type VARCHAR(40) NULL,
            product_type VARCHAR(40) NULL,
            thumbnail_url TEXT NULL,
            media_url TEXT NULL,
            original_timestamp DATETIME NULL,
            INDEX idx_meta_media_account (account_id,original_timestamp)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    db.commit()

def integer_or_none(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)) or (isinstance(value, str) and value.isdecimal()):
        return int(value)
    return None

def parse_ig_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc).replace(tzinfo=None) if not parsed.tzinfo else parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None

def profile_fields(graph, token):
    """Fallback to available fields when an optional field is unsupported."""
    required = graph("me", token, {"fields": "id,username"})
    extra = ["user_id", "name", "biography", "website", "account_type",
             "profile_picture_url", "followers_count", "follows_count", "media_count"]
    try:
        required.update(graph("me", token, {"fields": ",".join(["id", "username"] + extra)}))
    except Exception:
        for field in extra:
            try:
                required.update(graph("me", token, {"fields": field}))
            except Exception:
                continue
    return required

def latest_value(data):
    series = data.get("data") or []
    if not series:
        return None
    first = series[0]
    if isinstance(first.get("total_value"), dict):
        return integer_or_none(first["total_value"].get("value"))
    values = first.get("values") or []
    if values:
        return integer_or_none(values[-1].get("value"))
    return None

def sync_account(db, account_id, graph, token_getter, max_pages=2):
    """Idempotent import: a Meta media ID creates at most one local post.

    Does not alter published/scheduled state of existing jobs. A failed media
    read never destroys previously imported posts or credentials.
    """
    init_extra(db)
    token, professional_id = token_getter(db, account_id)
    info = profile_fields(graph, token)
    if not info.get("username"):
        raise ValueError("A Meta não retornou username para esta conta.")
    if str(info.get("user_id") or "").isdigit():
        professional_id = str(info["user_id"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.cursor() as c:
        c.execute("""UPDATE meta_accounts SET username=%s,
            instagram_user_id=CASE WHEN %s<>'' THEN %s ELSE instagram_user_id END,
            error_hint=NULL WHERE id=%s""",
            (info["username"], str(info.get("user_id") or ""),
             str(info.get("user_id") or ""), account_id))
        c.execute("""INSERT INTO meta_account_profiles
            (account_id,name,biography,website,account_type,avatar_url,
             followers_count,follows_count,media_count,synced_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE
            name=VALUES(name),biography=VALUES(biography),
            website=VALUES(website),account_type=VALUES(account_type),
            avatar_url=VALUES(avatar_url),
            followers_count=VALUES(followers_count),
            follows_count=VALUES(follows_count),
            media_count=VALUES(media_count),synced_at=UTC_TIMESTAMP()""",
            (account_id, info.get("name"),info.get("biography"),
             info.get("website"),info.get("account_type"),
             info.get("profile_picture_url"),
             integer_or_none(info.get("followers_count")),
             integer_or_none(info.get("follows_count")),
             integer_or_none(info.get("media_count"))))
    db.commit()

    media_error = None
    imported = 0
    cursor = None
    for page in range(max(1, min(max_pages, 4))):
        params = {"fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
                  "limit": 25}
        if cursor:
            params["after"] = cursor
        try:
            response = graph(professional_id + "/media", token, params)
        except Exception as exc:
            if page == 0:
                try:
                    params["fields"] = "id,caption,media_type,permalink,timestamp"
                    response = graph(professional_id + "/media", token, params)
                except Exception as fallback_error:
                    media_error = str(fallback_error)[:240]
                    break
            else:
                media_error = str(exc)[:240]
                break
        for media in response.get("data", []):
            media_id = str(media.get("id") or "")
            if not media_id.isdecimal() or len(media_id) > 80:
                continue
            published_at = parse_ig_time(media.get("timestamp")) or now
            permalink = (str(media.get("permalink") or "").strip() or None)
            with db.cursor() as c:
                c.execute("""SELECT id FROM meta_posts
                    WHERE account_id=%s AND media_id=%s LIMIT 1""", (account_id, media_id))
                existing = c.fetchone()
                if existing:
                    c.execute("""UPDATE meta_posts SET
                        permalink=COALESCE(%s,permalink)
                        WHERE id=%s""", (permalink, existing["id"]))
                    post_id = existing["id"]
                else:
                    c.execute("""INSERT INTO meta_posts
                        (account_id,video_url,caption,scheduled_at_utc,status,
                         media_id,permalink,published_at)
                        VALUES (%s,%s,%s,%s,'imported',%s,%s,%s)""",
                        (account_id, str(media.get("media_url") or permalink or ""),
                         str(media.get("caption") or "")[:2200],
                         published_at, media_id, permalink, published_at))
                    post_id = c.lastrowid
                    imported += 1
                c.execute("""INSERT INTO meta_media_details
                    (media_id,account_id,media_type,product_type,thumbnail_url,
                     media_url,original_timestamp)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                     media_type=COALESCE(VALUES(media_type),media_type),
                     product_type=COALESCE(VALUES(product_type),product_type),
                     thumbnail_url=COALESCE(VALUES(thumbnail_url),thumbnail_url),
                     media_url=COALESCE(VALUES(media_url),media_url),
                     original_timestamp=COALESCE(VALUES(original_timestamp),original_timestamp)""",
                    (media_id, account_id,media.get("media_type"),
                     media.get("media_product_type"),media.get("thumbnail_url"),
                     media.get("media_url"),published_at))
                if media.get("like_count") is not None or media.get("comments_count") is not None:
                    c.execute("""INSERT INTO meta_metrics
                        (post_id,likes,comments,checked_at)
                        VALUES (%s,%s,%s,DATE_SUB(UTC_TIMESTAMP(),INTERVAL 7 HOUR))
                        ON DUPLICATE KEY UPDATE
                        likes=COALESCE(VALUES(likes),likes),
                        comments=COALESCE(VALUES(comments),comments)""",
                        (post_id,integer_or_none(media.get("like_count")),
                         integer_or_none(media.get("comments_count"))))
            db.commit()
        paging = response.get("paging") or {}
        next_cursor = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not next_cursor or cursor == next_cursor:
            break
        cursor = next_cursor

    # These are daily account-level insights, not summed Reel insights.
    # Missing permissions or low-volume privacy thresholds yield N/D, not 0.
    yesterday = (datetime.now(timezone.utc)-timedelta(days=1)).date()
    since = int(datetime.combine(yesterday, datetime.min.time(), timezone.utc).timestamp())
    until = since + 86400
    insight_values = {}
    failures = []
    for metric in ("views","reach","profile_views","accounts_engaged"):
        try:
            data = graph(professional_id + "/insights", token,
                         {"metric":metric,"period":"day","since":since,"until":until})
            insight_values[metric] = latest_value(data)
        except Exception as exc:
            insight_values[metric] = None
            failures.append(metric + ": " + type(exc).__name__)
    with db.cursor() as c:
        c.execute("""UPDATE meta_account_profiles SET
           yesterday_views=%s,yesterday_reach=%s,
           yesterday_profile_visits=%s,yesterday_accounts_engaged=%s,
           insights_day=%s,insights_error=%s,media_error=%s,
           imported_count=(SELECT COUNT(*) FROM meta_posts
               WHERE account_id=%s AND media_id IS NOT NULL)
           WHERE account_id=%s""",
           (insight_values["views"],insight_values["reach"],
            insight_values["profile_views"],insight_values["accounts_engaged"],
            yesterday, ", ".join(failures)[:255] if failures else None,
            media_error, account_id, account_id))
    db.commit()
    return {"imported":imported,"username":info["username"],
            "media_error":media_error,"insights_partial":bool(failures)}
