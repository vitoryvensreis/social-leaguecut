"""Static safety checks for account connection; no external Instagram calls."""
import os
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from app.instagram import cipher, failure_reason

class AuthenticationSecurityTests(unittest.TestCase):
    def test_instagram_key_is_separate_from_proxy_key(self):
        key=Fernet.generate_key().decode()
        with patch.dict(os.environ,{"INSTAGRAM_ENCRYPTION_KEY":key}):
            encrypted=cipher().encrypt(b"never-plaintext").decode()
            self.assertNotIn("never-plaintext",encrypted)
            self.assertEqual(cipher().decrypt(encrypted.encode()),b"never-plaintext")

    def test_challenge_pauses_authentication(self):
        class ChallengeRequired(Exception):
            pass
        status,message=failure_reason(ChallengeRequired())
        self.assertEqual(status,"needs_verification")
        self.assertIn("verificação",message)

    def test_unknown_failure_is_paused_not_flagged_bad_password(self):
        class UnexpectedClientResponse(Exception):
            pass
        status,message=failure_reason(UnexpectedClientResponse())
        self.assertEqual(status,"paused")
        self.assertIn("diagnóstico",message)

    def test_proxy_error_is_distinguished(self):
        class ProxyError(Exception):
            pass
        status,message=failure_reason(ProxyError())
        self.assertEqual(status,"paused")
        self.assertIn("proxy",message.lower())

    def test_bad_password_is_recognized(self):
        class BadPassword(Exception):
            pass
        status,message=failure_reason(BadPassword())
        self.assertEqual(status,"failed")
        self.assertIn("Credenciais",message)

    def test_network_error_does_not_recommend_rotation(self):
        status,message=failure_reason(TimeoutError())
        self.assertEqual(status,"paused")
        self.assertIn("proxy fixo",message)

if __name__=="__main__":
    unittest.main()
