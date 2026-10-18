import unittest
from unittest.mock import patch
from app.bulk import parse_account_rows,parse_proxy_rows
from app.insights import validate_permalink

class BulkImportTests(unittest.TestCase):
    def test_sheet_paste(self):
        rows=parse_account_rows("usuario\temail\tsenha\nconta001\tconta@example.com\tsegredo-no-teste\nconta002\tconta2@example.com\tsegundo")
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0][0],"conta001")
        self.assertEqual(rows[1][1],"conta2@example.com")

    def test_duplicate_rows(self):
        rows=parse_account_rows("u01,a@example.com,dummy\nu01,a@example.com,dummy")
        self.assertEqual(len(rows),1)

    @patch("app.bulk.public_host",return_value=True)
    def test_webshare_format(self,public_host):
        rows=parse_proxy_rows("Proxy Address,Port,Username,Password\n192.0.2.10,8080,user,password\n192.0.2.10,8080,user,password")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][1],8080)

    @patch("app.bulk.public_host",return_value=True)
    def test_colon_separated_proxy(self,public_host):
        rows=parse_proxy_rows("198.51.100.8:8080:account:secret")
        self.assertEqual(rows[0][2],"account")

    def test_instagram_permalink_validation(self):
        self.assertTrue(validate_permalink("https://www.instagram.com/reel/C8tzZxNQ2__/"))
        self.assertTrue(validate_permalink("https://instagram.com/p/C8tzZxNQ2__/"))
        self.assertFalse(validate_permalink("https://instagram.com.evil.example/reel/C8tzZxNQ2__/"))
        self.assertFalse(validate_permalink("http://127.0.0.1/reel/C8tzZxNQ2__/"))

if __name__=="__main__":
    unittest.main()
