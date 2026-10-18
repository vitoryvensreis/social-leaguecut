"""No network calls or real Google credentials are needed."""
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

class GoogleOAuthTests(unittest.TestCase):
    def test_pkce_verifier_is_shared_across_redirect(self):
        config={
            "GOOGLE_CLIENT_ID":"test-client-id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET":"not-a-secret",
            "GOOGLE_REDIRECT_URI":"https://social.leaguecut.com.br/drive/callback",
        }
        with patch.dict(os.environ, config):
            from app.drive import flow
            verifier="a"*64
            initial=flow(code_verifier=verifier)
            url,state=initial.authorization_url(access_type="offline",prompt="consent")
            params=parse_qs(urlsplit(url).query)
            self.assertEqual(params.get("code_challenge_method"),["S256"])
            self.assertIn("code_challenge",params)
            self.assertEqual(initial.code_verifier,verifier)
            callback=flow(state=state,code_verifier=verifier)
            self.assertEqual(callback.code_verifier,verifier)
            self.assertEqual(callback.redirect_uri,config["GOOGLE_REDIRECT_URI"])

if __name__=="__main__":
    unittest.main()
