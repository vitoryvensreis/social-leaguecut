"""Manual reconciliation never replays an Instagram upload without explicit review."""
import os
import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from app.motor_api import ConfirmNoReel, clear_unpublished, review_pending

class FakeCursor:
    rowcount = 1
    def __init__(self,job=None,audit=None):
        self.job=job
        self.audit=audit
        self.executed=[]
        self.last=""
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,stmt,params=None):
        self.last=stmt
        self.executed.append((stmt,params))
    def fetchone(self):
        if "SELECT j.id,j.account_id,j.local_day" in self.last:
            return self.job
        if "SELECT id FROM social_manual_rechecks" in self.last:
            return self.audit
        return None
    def fetchall(self):return []

class FakeDb:
    def __init__(self,c):self.c=c;self.did_commit=False;self.did_rollback=False
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def cursor(self):return self.c
    def commit(self):self.did_commit=True
    def rollback(self):self.did_rollback=True

class ReconciliationTests(unittest.TestCase):
    def run_request(self,c,payload):
        db=FakeDb(c)
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}), \
             patch("app.motor_api.db_conn",return_value=db), \
             patch("app.motor_api.setup"), \
             patch("app.motor_api.init_review_audit"):
            result=clear_unpublished(27,payload,authorization="Bearer "+"x"*64)
        return result,db

    def test_review_release_requires_explicit_confirmation(self):
        with self.assertRaises(HTTPException) as e:
            self.run_request(FakeCursor(),ConfirmNoReel(username="abc",confirmed_no_reel=False))
        self.assertEqual(e.exception.status_code,422)

    def test_review_release_requires_matching_username(self):
        c=FakeCursor(job={"id":27,"account_id":3,"local_day":"2026-10-18",
             "status":"needs_review","username":"augusto14082",
             "permalink":None,"media_pk":None})
        with self.assertRaises(HTTPException) as e:
            self.run_request(c,ConfirmNoReel(username="wrong",confirmed_no_reel=True))
        self.assertEqual(e.exception.status_code,422)
        self.assertFalse(any("DELETE FROM social_motor_claims" in s for s,_ in c.executed))

    def test_published_job_cannot_be_reopened(self):
        c=FakeCursor(job={"id":27,"account_id":3,"local_day":"2026-10-18",
             "status":"published","username":"augusto14082",
             "permalink":"https://www.instagram.com/reel/abc123/","media_pk":"12345"})
        with self.assertRaises(HTTPException) as e:
            self.run_request(c,ConfirmNoReel(username="augusto14082",confirmed_no_reel=True))
        self.assertEqual(e.exception.status_code,409)

    def test_successful_explicit_reconciliation_logs_before_releasing(self):
        c=FakeCursor(job={"id":27,"account_id":3,"local_day":"2026-10-18",
             "status":"needs_review","username":"augusto14082",
             "permalink":None,"media_pk":None})
        result,db=self.run_request(c,ConfirmNoReel(username="@augusto14082",confirmed_no_reel=True))
        self.assertEqual(result["status"],"planned")
        self.assertTrue(db.did_commit)
        sql=[s for s,_ in c.executed]
        insert=next(i for i,s in enumerate(sql) if "INSERT INTO social_manual_rechecks" in s)
        delete=next(i for i,s in enumerate(sql) if "DELETE FROM social_motor_claims" in s)
        self.assertLess(insert,delete)

    def test_cannot_release_twice_on_same_day(self):
        c=FakeCursor(job={"id":27,"account_id":3,"local_day":"2026-10-18",
             "status":"needs_review","username":"augusto14082",
             "permalink":None,"media_pk":None},audit={"id":13})
        with self.assertRaises(HTTPException) as e:
            self.run_request(c,ConfirmNoReel(username="augusto14082",confirmed_no_reel=True))
        self.assertEqual(e.exception.status_code,409)

if __name__=="__main__":unittest.main()
