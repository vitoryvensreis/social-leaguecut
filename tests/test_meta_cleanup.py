"""Verify a one-time cleanup remains limited to historical failing tests."""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from scripts import dismiss_failed_tests as cleanup

class SafeDismissTests(unittest.TestCase):
    def test_only_the_failed_fortaleza_test_window_for_one_profile(self):
        db=MagicMock()
        cur=db.cursor.return_value.__enter__.return_value
        cur.rowcount=2
        with patch.object(cleanup,"init") as init:
            removed=cleanup.dismiss_old_tests(db)
        init.assert_called_once_with(db)
        sql,args=cur.execute.call_args.args
        self.assertIn("p.status IN ('failed','needs_review')",sql)
        self.assertIn("a.username=%s",sql)
        self.assertIn("SET p.status='dismissed'",sql)
        self.assertEqual(args[0],"vitoryvens")
        self.assertEqual(args[1],datetime(2026,10,23,1,0))
        self.assertEqual(args[2],datetime(2026,10,23,2,0))
        self.assertEqual(removed,2)
        db.commit.assert_called_once()

if __name__=="__main__":
    unittest.main()
