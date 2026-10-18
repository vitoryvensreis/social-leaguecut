import unittest
from datetime import datetime, timezone
from pathlib import Path
from jinja2 import Environment
from app.main import app
from app.monitor import to_local,csv_cell

class MonitoringOnlyTests(unittest.TestCase):
    def test_dashboard_template_parses(self):
        path=Path(__file__).resolve().parents[1]/"app"/"templates"/"index.html"
        Environment().parse(path.read_text(encoding="utf-8"))

    def test_only_monitoring_and_drive_site_routes(self):
        def flatten(routes,prefix=""):
            found=set()
            for route in routes:
                path=prefix+getattr(route,"path","")
                found.add(path)
                if getattr(route,"routes",None):
                    found.update(flatten(route.routes,path))
            return found
        paths=flatten(app.routes)
        print('DEBUG ROOT ROUTES',[(type(r).__name__,getattr(r,'path',None),getattr(r,'name',None)) for r in app.routes])
        from app.monitor import router as mr
        print('DEBUG ROUTER ATTRS',[(type(r).__name__,[k for k in dir(r) if k in ('router','routes','path','prefix') or 'router' in k]) for r in app.routes])
        print('DEBUG MONITOR ROUTES',[(type(r).__name__,getattr(r,'path',None)) for r in mr.routes])
        for path in ("/","/login","/logout","/reports/publications.csv",
                     "/api/motor/accounts","/api/motor/jobs/claim",
                     "/drive/connect","/drive/callback",
                     "/api/motor/campaign","/api/motor/campaign/generate"):
            self.assertIn(path,paths)
        for path in ("/proxy/test","/accounts","/posts",
                     "/campaign/settings","/campaign/generate",
                     "/instagram/accounts/{account_id}/connect","/bulk/accounts",
                     "/insights/posts/{job_id}/record"):
            self.assertNotIn(path,paths)

    def test_utc_display_fortaleza(self):
        self.assertEqual(to_local(datetime(2026,10,18,15,30,0)),"18/10/2026 12:30")
        self.assertEqual(to_local(None),"—")

    def test_excel_formula_safe_in_reports(self):
        self.assertEqual(csv_cell("=HYPERLINK('evil')"),"'=HYPERLINK('evil')")
        self.assertEqual(csv_cell("@cmd"),"'@cmd")
        self.assertEqual(csv_cell("abc"),"abc")

if __name__=="__main__":
    unittest.main()
