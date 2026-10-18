import unittest
from datetime import date, time
from collections import defaultdict
from app.campaign import build_plan, folder_id_from_input

class DailyCampaignTests(unittest.TestCase):
    def test_google_folder_link_and_raw_id(self):
        folder="1ZaEvqpHP7FIpVJoDGT_C33K2LwJmPcxT"
        self.assertEqual(folder_id_from_input("https://drive.google.com/drive/u/0/folders/"+folder),folder)
        self.assertEqual(folder_id_from_input(folder),folder)
        self.assertIsNone(folder_id_from_input("https://other.example/folders/"+folder))

    def test_16_accounts_get_one_daily_post_without_repeat(self):
        accounts=list(range(1,17))
        assets=["video"+str(i) for i in range(30)]
        rows=build_plan(accounts,assets,date(2026,10,19),time(17,0),30)
        self.assertEqual(len(rows),480)
        pairs=set()
        per_account=defaultdict(list)
        for account_id,asset,local_day,utc in rows:
            self.assertNotIn((account_id,local_day),pairs)
            pairs.add((account_id,local_day))
            per_account[account_id].append(asset)
        for account_id in accounts:
            self.assertEqual(len(per_account[account_id]),30)
            self.assertEqual(len(set(per_account[account_id])),30)

    def test_videos_repeat_only_after_exhaustion(self):
        rows=build_plan([1],["A","B"],date(2026,10,19),time(10,0),6)
        self.assertEqual(len(rows),6)
        for start in (0,2,4):
            self.assertEqual(set(row[1] for row in rows[start:start+2]),{"A","B"})

    def test_empty_folder_never_schedules_jobs(self):
        self.assertEqual(build_plan([1],[],date(2026,10,19),time(10,0)),[])

if __name__=="__main__":
    unittest.main()
