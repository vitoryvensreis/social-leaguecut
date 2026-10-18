"""Read-only media preflight endpoint: no claim may happen during preview."""
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.motor_api import preview_test_job

class Cursor:
    def __init__(self,current=None,job=None):
        self.current=current
        self.job=job
        self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,args=None):
        self.queries.append((sql,args))
        self.last_sql=sql
    def fetchone(self):
        if "SELECT status FROM social_daily_jobs" in self.last_sql:
            return self.current
        return self.job

class DB:
    def __init__(self,c):self.c=c
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def cursor(self):return self.c

class PreviewTests(unittest.TestCase):
    def test_read_only_preview_never_claims(self):
        c=Cursor(None,{"id":1,"account_id":5,"drive_id":"local_"+"a"*64,
                        "caption":"Exemplo","username":"conta"})
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"a"*64}), \
            patch("app.motor_api.db_conn",return_value=DB(c)), \
            patch("app.motor_api.setup"):
            r=preview_test_job(5,authorization="Bearer "+"a"*64)
        self.assertEqual(r["job"]["drive_id"],"local_"+"a"*64)
        self.assertTrue(all("INSERT" not in sql and "UPDATE" not in sql
                            for sql,_ in c.queries))

    def test_review_today_blocks_test_without_mutation(self):
        c=Cursor({"status":"needs_review"},None)
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"a"*64}), \
            patch("app.motor_api.db_conn",return_value=DB(c)), \
            patch("app.motor_api.setup"):
            with self.assertRaises(HTTPException) as exc:
                preview_test_job(5,authorization="Bearer "+"a"*64)
        self.assertEqual(exc.exception.status_code,409)

if __name__=="__main__":unittest.main()
