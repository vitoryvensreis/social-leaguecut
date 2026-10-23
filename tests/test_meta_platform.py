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

    def test_instagram_media_stream_supports_head_get_and_range(self):
        from fastapi.testclient import TestClient
        import tempfile
        from pathlib import Path
        opaque_id = "a" * 40
        with tempfile.TemporaryDirectory() as directory, patch.object(meta,"MEDIA_DIR",Path(directory)):
            path=Path(directory)/(opaque_id+".mp4")
            path.write_bytes(b"fake-mp4-sample")
            client=TestClient(meta.app)
            head=client.head("/ig-video/"+opaque_id)
            self.assertEqual(head.status_code,200)
            self.assertEqual(head.headers["content-type"],"video/mp4")
            get=client.get("/ig-video/"+opaque_id,headers={"Range":"bytes=0-3"})
            self.assertEqual(get.status_code,206)
            self.assertEqual(get.content,b"fake")
            self.assertEqual(client.get("/ig-video/../secret").status_code,404)

    def test_legacy_media_url_is_remapped_only_for_own_uploads(self):
        valid="b" * 40
        original=meta.PUBLIC_URL+"/media/"+valid+".mp4"
        self.assertEqual(meta.normalize_owned_video_url(original),
                         meta.PUBLIC_URL+"/ig-video/"+valid)
        self.assertEqual(meta.normalize_owned_video_url("https://cdn.example.com/file.mp4"),
                         "https://cdn.example.com/file.mp4")

    def test_web_routes_are_meta_only(self):
        route_paths = {r.path for r in meta.app.routes}
        self.assertIn("/accounts", route_paths)
        self.assertIn("/schedule", route_paths)
        self.assertIn("/upload", route_paths)
        self.assertNotIn("/multibrowser", route_paths)


if __name__ == "__main__":
    unittest.main()
