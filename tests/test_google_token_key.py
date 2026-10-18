import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from app.drive import crypt

class GoogleTokenEncryptionTests(unittest.TestCase):
    def test_old_proxy_key_remains_compatible(self):
        key=Fernet.generate_key().decode()
        with patch.dict("os.environ",{"PROXY_ENCRYPTION_KEY":key},clear=True):
            token=crypt().encrypt(b"google-oauth-fixture")
            self.assertEqual(crypt().decrypt(token),b"google-oauth-fixture")

    def test_drive_specific_key_takes_precedence(self):
        original=Fernet.generate_key().decode()
        with patch.dict("os.environ",{"GOOGLE_TOKEN_ENCRYPTION_KEY":original,
                                      "PROXY_ENCRYPTION_KEY":Fernet.generate_key().decode()},clear=True):
            cipher=crypt()
            self.assertEqual(cipher.decrypt(cipher.encrypt(b"test")),b"test")

    def test_missing_keys_fail_safely(self):
        with patch.dict("os.environ",{},clear=True):
            with self.assertRaisesRegex(ValueError,"not configured"):
                crypt()

if __name__=="__main__":
    unittest.main()
