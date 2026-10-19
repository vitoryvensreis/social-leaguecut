"""Hybrid Playwright training tests."""
import unittest
from unittest.mock import AsyncMock, MagicMock
from app.browser_runtime import BrowserManager, VIEWPORT
from app.training_selectors import INSPECT_TARGET_JS

class TestTraining(unittest.IsolatedAsyncioTestCase):
    async def test_record_selector(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://www.instagram.com/"
        b.page.locator.return_value.count=AsyncMock(return_value=0)
        b.page.evaluate=AsyncMock(return_value={"selector":"button","label":"Cortar","tag":"button"})
        b.page.mouse.click=AsyncMock()
        result=await b.click_and_describe(1,10,20)
        self.assertEqual(result["selector"],"button")
        b.page.mouse.click.assert_awaited_once_with(10,20)

    async def test_no_replay_in_login(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://www.instagram.com/accounts/login/"
        with self.assertRaises(RuntimeError):
            await b.replay_training_click(1,10,20,"button")

    def test_viewport(self):
        self.assertEqual(VIEWPORT,{"width":1280,"height":800})
        self.assertIn("elementFromPoint",INSPECT_TARGET_JS)
