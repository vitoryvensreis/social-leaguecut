import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.motor_api import authorize, ClaimRequest, Result

class MotorAPITests(unittest.TestCase):
    def test_missing_motor_api_token_is_rejected(self):
        with patch.dict(os.environ, {"MOTOR_API_TOKEN":""}):
            with self.assertRaises(HTTPException):
                authorize("Bearer any-key")

    def test_valid_motor_api_token_is_accepted(self):
        secret="a"*64
        with patch.dict(os.environ, {"MOTOR_API_TOKEN":secret}):
            self.assertIsNone(authorize("Bearer "+secret))
            with self.assertRaises(HTTPException):
                authorize("Bearer "+("x"*64))

    def test_ready_accounts_are_explicit(self):
        self.assertEqual(ClaimRequest().ready_accounts,[])
        self.assertEqual(ClaimRequest(ready_accounts=[1,2]).ready_accounts,[1,2])

    def test_published_requires_proof_from_instagram(self):
        from app.insights import validate_permalink
        self.assertFalse(validate_permalink("https://evil.example/reel/AbC12345"))
        self.assertTrue(validate_permalink("https://www.instagram.com/reel/AbC12345"))
        self.assertEqual(Result(claim_token="x"*40,outcome="needs_review").outcome,"needs_review")

if __name__=="__main__":
    unittest.main()
