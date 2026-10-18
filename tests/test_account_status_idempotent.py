"""Protect idempotent account updates and distinguish pre-upload failures."""
import hashlib
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from fastapi import HTTPException
from app.motor_api import account_status, AccountStatus, authorize

class FakeCursor:
    rowcount=0  # MySQL unchanged UPDATE: this used to cause false HTTP 404
    def __init__(self): self.commands=[]
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,statement,args=None): self.commands.append((statement,args))
    def fetchone(self): return {"id":3}

class FakeDb:
    def __init__(self): self.cursor_obj=FakeCursor();self.committed=False
    def cursor(self):return self.cursor_obj
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def commit(self): self.committed=True

class AccountStatusTests(unittest.TestCase):
    def test_same_status_does_not_claim_deploy_missing(self):
        db=FakeDb()
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}),patch("app.motor_api.db_conn",return_value=db):
            result=account_status(3,AccountStatus(status="connected"),authorization="Bearer "+"x"*64)
        self.assertEqual(result,{"status":"ok"})
        self.assertTrue(db.committed)
        self.assertTrue(any("SELECT id FROM social_accounts" in q for q,_ in db.cursor_obj.commands))

    def test_missing_token_denied(self):
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}):
            with self.assertRaises(HTTPException):
                authorize("Bearer wrong")

if __name__=="__main__":
    unittest.main()
