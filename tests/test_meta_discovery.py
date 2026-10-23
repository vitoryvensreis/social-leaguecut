"""Regression checks for Meta account discovery and new dashboard.

Tests are offline: live scopes, quotas, and publish behavior require a real
authorized Instagram tester token and are not faked in production views.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock

from jinja2 import Environment, FileSystemLoader

os.environ.setdefault("SESSION_SECRET", "secure-unit-test-session-secret-not-for-production")
from app import meta_platform as platform
from app import meta_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def test_profile_read_uses_instagram_login_and_keeps_real_values(self):
        requests = []

        def fake_graph(path, token, params):
            requests.append((path, params["fields"]))
            return {"id": "123456", "username": "proteste", "user_id": "1784000001",
                    "followers_count": 312, "media_count": 21}

        profile = discovery.profile_fields(fake_graph, "fake-token")
        self.assertEqual(profile["username"], "proteste")
        self.assertEqual(profile["followers_count"], 312)
        self.assertEqual(profile["media_count"], 21)
        self.assertTrue(all(route == "me" for route, _ in requests))

    def test_optional_profile_field_fallback(self):
        def fake_graph(path, token, params):
            if params["fields"].startswith("id,username,"):
                raise platform.GraphError("Unavailable field")
            if params["fields"] == "followers_count":
                return {"followers_count": 15}
            return {"id": "456", "username": "tester"}

        profile = discovery.profile_fields(fake_graph, "fake-token")
        self.assertEqual(profile["followers_count"], 15)

    def test_insights_missing_never_becomes_zero(self):
        self.assertIsNone(discovery.latest_value({"data": []}))
        self.assertEqual(discovery.latest_value({"data": [{
            "name": "views", "values": [{"value": 123}]}]}), 123)
        self.assertEqual(discovery.latest_value({"data": [{
            "name": "reach", "total_value": {"value": 4}}]}), 4)

    def test_timestamp_timezone(self):
        self.assertEqual(str(discovery.parse_ig_time("2026-10-22T10:00:00-0300")),
                         "2026-10-22 13:00:00")
        self.assertIsNone(discovery.parse_ig_time("invalid"))

    def test_dashboard_can_render_with_and_without_data(self):
        template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
        env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
        template = env.get_template("meta_dashboard.html")
        variables = {"csrf": "test-csrf", "error": "", "success": "",
                     "configured": True, "accounts": [], "posts": [],
                     "totals": {}, "now_local": "2026-10-22T10:00"}
        for logged in (False, True):
            html = template.render({**variables, "logged": logged})
            self.assertIn("Instagram Studio", html)
            if logged:
                self.assertIn("id=\"upload\"", html)
                self.assertIn("id=\"postsList\"", html)
                self.assertIn("Sincronize cada perfil", html)

    def test_sync_requires_authenticated_form(self):
        paths = {route.path: route.methods for route in platform.app.routes}
        self.assertIn("POST", paths["/accounts/{account_id}/sync"])
        self.assertNotIn("/multibrowser", paths)


if __name__ == "__main__":
    unittest.main()
