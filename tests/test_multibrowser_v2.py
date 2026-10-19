"""MultiBrowser: browser isolation, safety constraints and UTF-8 captions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
from app.browser_runtime import BrowserManager, VIEWPORT
from app import multibrowser

class ValidationTests(unittest.TestCase):
    def test_sixteen_slots(self):self.assertEqual(multibrowser.SLOTS,16)
    def test_fixed_viewport(self):self.assertEqual(VIEWPORT,{"width":960,"height":720})
    def test_names(self):
        self.assertTrue(multibrowser.NAME.fullmatch("augusto14082"))
        self.assertFalse(multibrowser.NAME.fullmatch("bad@example.com"))
    def test_invalid_click(self):
        with self.assertRaises(ValidationError):multibrowser.ClickPayload(x=970,y=10)
    def test_key_whitelist(self):
        with self.assertRaises(ValidationError):multibrowser.KeyPayload(key="Control+Shift+I")
    def test_emoji_caption(self):
        c=multibrowser.CaptionPayload(title="Padrão",body="Olá 🚀 #Negócios")
        self.assertIn("🚀",c.body)
    def test_caption_bound(self):
        with self.assertRaises(ValidationError):
            multibrowser.CaptionPayload(title="X",body="a"*2201)
    def test_cookie_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict("os.environ",{"LEAGUECUT_BROWSER_DIR":root}):
                worker=BrowserManager()
                a=worker._directory(1,"conta.a")
                b=worker._directory(1,"conta.b")
                self.assertNotEqual(a,b)
                self.assertTrue(str(a).startswith(root))
                with self.assertRaises(RuntimeError):worker._directory(1,"../../root")
    def test_no_secrets_in_template(self):
        s=(Path(__file__).parent.parent/"app/templates/multibrowser.html").read_text()
        self.assertNotIn("instagrapi",s.lower())
        self.assertIn("record-click",s)
    def test_request_must_be_admin(self):
        with patch("app.main.admin",return_value=False):
            with self.assertRaises(HTTPException) as e:multibrowser.auth(MagicMock())
        self.assertEqual(e.exception.status_code,401)
    def test_csrf(self):
        req=MagicMock()
        req.headers={"x-csrf-token":"","origin":"","host":"social.leaguecut.com.br"}
        with patch("app.main.admin",return_value=True),patch("app.main.form_allowed",return_value=False):
            with self.assertRaises(HTTPException) as e:multibrowser.write(req)
        self.assertEqual(e.exception.status_code,403)

class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_idle(self):
        b=BrowserManager()
        await b.close()
        self.assertIsNone(b.active_slot)
    async def test_inactive_screenshot(self):
        b=BrowserManager()
        self.assertIsNone(await b.screenshot(1))
    async def test_inactive_click(self):
        b=BrowserManager()
        with self.assertRaises(RuntimeError):await b.click(1,10,20)
    async def test_active_click(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.mouse.click=AsyncMock()
        await b.click(1,20,40)
        b.page.mouse.click.assert_awaited_once_with(20,40,timeout=10000)
    async def test_text_not_saved(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.keyboard.insert_text=AsyncMock()
        await b.type_text(1,"temporary password")
        b.page.keyboard.insert_text.assert_awaited_once()

if __name__=="__main__":unittest.main()
