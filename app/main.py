"""Production ASGI entrypoint: the official Instagram Graph API Meta Studio.

The old browser automation components are retained only in Git history and
are intentionally not mounted as routes in this application.
"""
from app.meta_platform import app, db_connection  # noqa: F401
