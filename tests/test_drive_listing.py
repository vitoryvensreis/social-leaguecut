import json
import unittest
from unittest.mock import MagicMock, patch
from fastapi import Request
from googleapiclient.errors import HttpError
from httplib2 import Response

class DriveListingTests(unittest.TestCase):
    def test_list_uses_google_camelcase_parameter(self):
        from app.drive import files
        db = MagicMock()
        db.__enter__.return_value = db
        api = MagicMock()
        api.files.return_value.list.return_value.execute.return_value = {"files":[{"id":"one","name":"clip.mp4"}]}
        with patch("app.main.admin", return_value=True), \
             patch("app.main.db_connection", return_value=db), \
             patch("app.drive.creds", return_value=MagicMock()), \
             patch("app.drive.build", return_value=api), \
             patch("app.drive.store") as save:
            result = files(MagicMock())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads(result.body)["files"][0]["name"],"clip.mp4")
        keyword_args=api.files.return_value.list.call_args.kwargs
        self.assertEqual(keyword_args["pageSize"],100)
        self.assertNotIn("page_size",keyword_args)
        save.assert_called_once()

    def test_drive_api_disabled_has_actionable_message(self):
        from app.drive import drive_error_message
        payload={"error":{"status":"PERMISSION_DENIED","errors":[{"reason":"accessNotConfigured"}]}}
        error=HttpError(Response({"status":"403"}),json.dumps(payload).encode())
        self.assertIn("Drive API desativada",drive_error_message(error,"list_files"))

if __name__ == "__main__":
    unittest.main()
