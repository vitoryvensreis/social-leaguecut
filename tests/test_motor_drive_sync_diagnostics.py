import json
import os
import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response
from app.motor_campaign import sync_drive

class MotorDriveSyncDiagnosticsTests(unittest.TestCase):
    def fake_db(self):
        db=MagicMock()
        db.__enter__.return_value=db
        return db

    def test_refresh_expired_is_actionable_without_token(self):
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"z"*64}), \
             patch("app.motor_campaign.db_conn",return_value=self.fake_db()), \
             patch("app.motor_campaign.settings",return_value={"folder_id":"valid-folder-12345"}), \
             patch("app.motor_campaign.drive_credentials",side_effect=RefreshError("secret-response")):
            with self.assertRaises(HTTPException) as exc:
                sync_drive(authorization="Bearer "+"z"*64)
        self.assertEqual(exc.exception.status_code,409)
        self.assertIn("Reconecte o Google Drive",exc.exception.detail)
        self.assertNotIn("secret",exc.exception.detail)

    def test_folder_not_found_returns_404_not_generic_503(self):
        api=MagicMock()
        api.files.return_value.get.return_value.execute.side_effect=HttpError(
            Response({"status":"404"}),b'{"error":{"message":"sensitive provider response"}}')
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"z"*64}), \
             patch("app.motor_campaign.db_conn",return_value=self.fake_db()), \
             patch("app.motor_campaign.settings",return_value={"folder_id":"valid-folder-12345"}), \
             patch("app.motor_campaign.drive_credentials",return_value=MagicMock()), \
             patch("app.motor_campaign.build",return_value=api):
            with self.assertRaises(HTTPException) as exc:
                sync_drive(authorization="Bearer "+"z"*64)
        self.assertEqual(exc.exception.status_code,404)
        self.assertIn("Pasta não encontrada",exc.exception.detail)
        self.assertNotIn("sensitive",exc.exception.detail)

    def test_successful_listing_returns_count(self):
        api=MagicMock()
        api.files.return_value.get.return_value.execute.return_value={
            "id":"valid-folder-12345","name":"Conteudos Augusto - teste",
            "mimeType":"application/vnd.google-apps.folder"
        }
        api.files.return_value.list.return_value.execute.return_value={
            "files":[{"id":"abc123456789","name":"video.mp4",
                      "mimeType":"video/mp4","size":"1024"}]
        }
        with patch.dict(os.environ,{"MOTOR_API_TOKEN":"z"*64}), \
             patch("app.motor_campaign.db_conn",return_value=self.fake_db()), \
             patch("app.motor_campaign.settings",return_value={"folder_id":"valid-folder-12345"}), \
             patch("app.motor_campaign.drive_credentials",return_value=MagicMock()), \
             patch("app.motor_campaign.build",return_value=api), \
             patch("app.motor_campaign.init"), \
             patch("app.motor_campaign.save_credentials"):
            outcome=sync_drive(authorization="Bearer "+"z"*64)
        self.assertEqual(outcome["videos"],1)

if __name__=="__main__":
    unittest.main()
