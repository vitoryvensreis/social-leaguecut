"""Unit coverage for the Meta-only replacement without calling Instagram."""
import os
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ.setdefault("SESSION_SECRET", "secure-unit-test-session-secret-not-for-production")
from app import meta_platform as meta


class MetaPlatformTests(unittest.TestCase):
    def test_encrypt_round_trip_and_secret_not_plaintext(self):
        token = "IGQ_Test_" + "x" * 50
        ciphertext = meta.seal(token)
        self.assertNotIn(token, ciphertext)
        self.assertEqual(meta.unseal(ciphertext), token)

    def test_media_urls_must_be_public_https(self):
        self.assertTrue(meta.validate_media_url("https://media.example.com/reel.mp4"))
        for value in ("http://example.com/a.mp4", "https://localhost/file",
                      "https://127.0.0.1/file", "https://10.1.0.1/a",
                      "https://user:pass@example.com/file"):
            self.assertFalse(meta.validate_media_url(value))

    def test_fortaleza_timezone(self):
        local = meta.fmt_local(datetime(2026, 10, 22, 13, 30))
        self.assertEqual(local, "22/10/2026 10:30")

    def test_account_profile_uses_scoped_id(self):
        with patch.object(meta, "graph", return_value={
                "id": "123456", "user_id": "17841400123", "username": "testepublico"}):
            self.assertEqual(meta.account_profile("token"),
                             ("123456", "17841400123", "testepublico"))

    def test_publication_requires_identifier(self):
        with patch.object(meta, "graph", return_value={"username": "testepublico"}):
            with self.assertRaises(meta.GraphError):
                meta.account_profile("token")

    def test_web_routes_are_meta_only(self):
        route_paths = {r.path for r in meta.app.routes}
        self.assertIn("/accounts", route_paths)
        self.assertIn("/schedule", route_paths)
        self.assertIn("/upload", route_paths)
        self.assertNotIn("/multibrowser", route_paths)


if __name__ == "__main__":
    unittest.main()
