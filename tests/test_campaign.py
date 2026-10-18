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
        for account_id,asset,local_day,utc,caption in rows:
            self.assertNotIn((account_id,local_day),pairs)
            pairs.add((account_id,local_day))
            per_account[account_id].append(asset)
        for account_id in accounts:
            self.assertEqual(len(per_account[account_id]),30)
            self.assertEqual(len(set(per_account[account_id])),30)

    def test_captions_change_and_times_are_spread_across_10_to_19(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        accounts=list(range(1,17))
        captions=["Legenda A","Legenda B","Legenda C"]
        rows=build_plan(accounts,["video-01","video-02"],date(2026,10,19),time(10,0),30,
                        captions=captions,end_at=time(19,0))
        self.assertEqual(len(rows),480)
        from collections import defaultdict
        by_day=defaultdict(list)
        by_account=defaultdict(list)
        for account_id,asset,day,due,caption in rows:
            local=due.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(ZoneInfo("America/Fortaleza"))
            minute=local.hour*60+local.minute
            self.assertGreaterEqual(minute,600)
            self.assertLessEqual(minute,1140)
            by_day[day].append(minute)
            by_account[account_id].append(caption)
        for day,minutes in by_day.items():
            self.assertEqual(len(minutes),16)
            self.assertEqual(len(set(minutes)),16)
            ordered=sorted(minutes)
            self.assertTrue(all(b-a>=12 for a,b in zip(ordered,ordered[1:])))
        for captions_for_account in by_account.values():
            self.assertEqual(set(captions_for_account[:3]),set(captions))

    def test_multiline_caption_separator(self):
        from app.campaign import split_captions
        parsed=split_captions("linha 1\\nlinha 2\\n---\\nLegenda B")
        self.assertEqual(len(parsed),2)
        self.assertIn("linha 2",parsed[0])

    def test_videos_repeat_only_after_exhaustion(self):
        rows=build_plan([1],["A","B"],date(2026,10,19),time(10,0),6)
        self.assertEqual(len(rows),6)
        for start in (0,2,4):
            self.assertEqual(set(row[1] for row in rows[start:start+2]),{"A","B"})

    def test_empty_folder_never_schedules_jobs(self):
        self.assertEqual(build_plan([1],[],date(2026,10,19),time(10,0)),[])

if __name__=="__main__":
    unittest.main()
