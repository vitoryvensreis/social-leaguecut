"""Calendar API and editorial layout regression tests (no Meta API calls)."""
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader

os.environ.setdefault("SESSION_SECRET","secure-unit-test-session-secret-not-for-production")
from app import meta_platform as meta


class CalendarTests(unittest.TestCase):
    def test_calendar_timezone_and_monday_alignment(self):
        start,end=meta.calendar_range("2026-10")
        self.assertEqual(start,datetime(2026,9,28,3,0))
        self.assertEqual(end,datetime(2026,11,7,3,0))

    def test_december_calendar_handles_new_year(self):
        start,end=meta.calendar_range("2026-12")
        self.assertEqual(start,datetime(2026,11,30,3,0))
        self.assertTrue(end.year==2027)

    def test_calendar_month_validation(self):
        for value in ("2026-13","2026-00","2026/10","bad","2026-10-01",""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    meta.calendar_range(value)

    def test_calendar_api_rejects_unauthenticated_clients(self):
        request=MagicMock()
        with patch.object(meta,"authenticated",return_value=False):
            with self.assertRaises(HTTPException) as error:
                meta.calendar_api(request,month="2026-10")
        self.assertEqual(error.exception.status_code,401)

    def test_calendar_api_formats_timezone_and_excludes_dismissed(self):
        request=MagicMock()
        cursor=MagicMock()
        cursor.fetchall.return_value=[
            {"id":42,"status":"scheduled","caption":"Teste Reel","scheduled_at_utc":datetime(2026,10,23,1,10),
             "published_at":None,"permalink":None,"error_hint":None,"account_id":1,"username":"teste"}
        ]
        db=MagicMock()
        db.__enter__.return_value=db
        db.cursor.return_value.__enter__.return_value=cursor
        with patch.object(meta,"authenticated",return_value=True), \
             patch.object(meta,"db_connection",return_value=db), \
             patch.object(meta,"init"):
            response=meta.calendar_api(request,month="2026-10",account_id=1)
        import json
        payload=json.loads(response.body)
        self.assertEqual(len(payload["events"]),1)
        self.assertEqual(payload["events"][0]["day"],"2026-10-22")
        self.assertEqual(payload["events"][0]["time"],"22:10")
        self.assertEqual(payload["events"][0]["status"],"scheduled")
        sql,args=cursor.execute.call_args.args
        self.assertIn("p.status<>'dismissed'",sql)
        self.assertIn("AND a.id=%s",sql)
        self.assertEqual(args[-1],1)
        self.assertEqual(response.headers["cache-control"],"no-store")

    def test_composer_and_calendar_keep_existing_form_contract(self):
        folder=Path(__file__).resolve().parents[1]/"app"/"templates"
        env=Environment(loader=FileSystemLoader(folder),autoescape=True)
        html=env.get_template("meta_dashboard.html").render(
            logged=True,csrf="csrf",submit_id="nonce_unique_12345678901234567890",
            accounts=[],posts=[],totals={},top_posts=[],error="",success="",
            now_local="2026-10-22T19:30")
        for snippet in (
            'id="calendario"', 'id="studioCalGrid"', 'id="studioCalListView"',
            'id="studioCalAccount"', 'id="studioCalStatus"',
            'id="scheduleForm"', 'id="publishNowBtn"', 'id="scheduleBtn"',
            'id="videoUrl"', 'id="videoPreview"', 'id="caption"',
            'id="upload"', 'id="batchForm"', 'id="postsList"'):
            self.assertIn(snippet,html)

    def test_backend_does_not_change_meta_publish_routes(self):
        routes={route.path for route in meta.app.routes}
        self.assertIn("/api/calendar",routes)
        self.assertIn("/schedule",routes)
        self.assertIn("/batch",routes)
        self.assertIn("/posts/{post_id}/cancel",routes)


if __name__=="__main__":
    unittest.main()
