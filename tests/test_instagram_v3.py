"""Offline compatibility tests: these tests never contact Instagram."""
import inspect
import unittest
from importlib.metadata import version
from instagrapi import Client
from app.instagram import failure_reason

class InstaV3CompatibilityTests(unittest.TestCase):
    def test_pinned_instagram_sdk_version(self):
        self.assertEqual(version("instagrapi"),"3.0.26")

    def test_existing_client_interface_remains_compatible(self):
        # No network requests; validate methods used by account connection.
        for method in ("login","set_proxy","get_settings","set_settings","media_info"):
            self.assertTrue(callable(getattr(Client,method,None)),method)
        self.assertIn("verification_code",inspect.signature(Client.login).parameters)

    def test_outdated_app_response_is_diagnosed_without_raw_data(self):
        class UnknownError(Exception):
            error_type="needs_upgrade"
            message="Your version of Instagram is out of date"
        status,message=failure_reason(UnknownError("secret-placeholder"))
        self.assertEqual(status,"paused")
        self.assertIn("desatualizada",message)
        self.assertNotIn("secret-placeholder",message)

    def test_unclassified_response_does_not_blame_credentials(self):
        class UnknownError(Exception):
            pass
        status,message=failure_reason(UnknownError("unexpected"))
        self.assertEqual(status,"paused")
        self.assertIn("API privada",message)

if __name__=="__main__":
    unittest.main()
