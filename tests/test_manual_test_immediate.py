"""Regression tests for 'TESTAR 1 REEL AGORA' semantics."""
import os
import unittest
from datetime import date,datetime,timedelta,timezone
from unittest.mock import patch
from fastapi import HTTPException
from app.motor_api import TestClaim,test_claim,TZ

class Cursor:
    def __init__(self,existing,job):
        self.existing=existing
        self.job=job
        self.queries=[]
        self.rowcount=1
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,query,params=None):
        self.queries.append((query,params))
        self.last=query
    def fetchone(self):
        if "SELECT id,status FROM social_daily_jobs" in self.last:
            return self.existing
        if "SELECT j.id,j.account_id" in self.last:
            return self.job
        return None

class DB:
    def __init__(self,c):
        self.cursor_object=c
        self.committed=False
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def cursor(self):return self.cursor_object
    def commit(self):self.committed=True
    def rollback(self):pass

class TestImmediateClaim(unittest.TestCase):
    def test_1515_slot_claim_executes_at_now_not_later(self):
        today=datetime.now(TZ).date()
        job={"id":17,"account_id":3,"drive_id":"local_"+"a"*64,
             "caption":"teste","local_day":today,"status":"planned","username":"teste"}
        cursor=Cursor({"id":17,"status":"planned"},job)
        db=DB(cursor)
        before=datetime.now(timezone.utc).replace(tzinfo=None)
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}), \\
             patch("app.motor_api.db_conn",return_value=db), \\
             patch("app.motor_api.setup"):
            result=test_claim(TestClaim(account_id=3),authorization="Bearer "+"x"*64)
        after=datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertTrue(db.committed)
        update=[params for sql,params in cursor.queries
                if "SET" in sql and "planned_utc=%s" in sql]
        self.assertEqual(len(update),1)
        self.assertEqual(update[0][0],today)
        self.assertLessEqual(before,update[0][1])
        self.assertLessEqual(update[0][1],after)
        self.assertEqual(result["job"]["execution"],"immediate")
        self.assertEqual(result["job"]["id"],17)

    def test_today_with_review_cannot_consume_tomorrow_slot(self):
        cursor=Cursor({"id":1,"status":"needs_review"},None)
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}), \\
             patch("app.motor_api.db_conn",return_value=DB(cursor)), \\
             patch("app.motor_api.setup"):
            with self.assertRaises(HTTPException) as cm:
                test_claim(TestClaim(account_id=3),authorization="Bearer "+"x"*64)
        self.assertEqual(cm.exception.status_code,409)
        self.assertTrue(all("INSERT INTO social_motor_claims" not in sql
                            for sql,_ in cursor.queries))

    def test_no_planned_job_does_not_create_duplicate(self):
        cursor=Cursor(None,None)
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"x"*64}), \\
             patch("app.motor_api.db_conn",return_value=DB(cursor)), \\
             patch("app.motor_api.setup"):
            with self.assertRaises(HTTPException) as cm:
                test_claim(TestClaim(account_id=3),authorization="Bearer "+"x"*64)
        self.assertEqual(cm.exception.status_code,409)
        self.assertTrue(all("INSERT INTO social_motor_claims" not in sql
                            for sql,_ in cursor.queries))

if __name__=="__main__":
    unittest.main()
