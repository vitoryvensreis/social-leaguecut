import unittest
from fastapi import HTTPException
from app.local_library import LocalVideo,validate_video,LOCAL_FOLDER
from app.motor_campaign import CampaignSettings
from datetime import date

class LocalWindowsInventoryTests(unittest.TestCase):
    def test_valid_metadata_has_no_pc_path(self):
        item=LocalVideo(id="local_"+"a"*64,name="reel_01.mp4",size_bytes=1200000)
        self.assertEqual(validate_video(item),"video/mp4")
        self.assertEqual(LOCAL_FOLDER,"local-windows")
        self.assertNotIn("C:\\",repr(item.model_dump()))

    def test_bad_identifier_rejected(self):
        item=LocalVideo(id="local_"+"z"*64,name="reel_01.mp4",size_bytes=1200000)
        with self.assertRaises(HTTPException):
            validate_video(item)

    def test_invalid_video_type_rejected(self):
        item=LocalVideo(id="local_"+"f"*64,name="segredo.txt",size_bytes=500)
        with self.assertRaises(HTTPException):
            validate_video(item)

    def test_campaign_accepts_local_identifier(self):
        settings=CampaignSettings(folder_url=LOCAL_FOLDER,captions=["Legenda"],start_date=date(2026,10,20))
        self.assertEqual(settings.folder_url,LOCAL_FOLDER)

if __name__=="__main__":
    unittest.main()
