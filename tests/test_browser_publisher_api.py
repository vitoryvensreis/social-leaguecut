"""Offline tests for one-time manual publishing slot reservation."""
import os,unittest
from unittest.mock import patch,MagicMock
from fastapi import HTTPException
from app.motor_api import TestClaim,authorize

class ManualPostClaimTests(unittest.TestCase):
    def test_explicit_account_is_required(self):
        with self.assertRaises(Exception):
            TestClaim(account_id=0)
        self.assertEqual(TestClaim(account_id=5).account_id,5)

    def test_token_restriction(self):
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"b"*64}):
            self.assertIsNone(authorize("Bearer "+"b"*64))
            with self.assertRaises(HTTPException):
                authorize("Bearer wrong")

if __name__=="__main__":unittest.main()
