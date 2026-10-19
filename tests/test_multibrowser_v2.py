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
    def test_fixed_viewport(self):self.assertEqual(VIEWPORT,{"width":1280,"height":800})
    def test_names(self):
        self.assertTrue(multibrowser.NAME.fullmatch("augusto14082"))
        self.assertFalse(multibrowser.NAME.fullmatch("bad@example.com"))
    def test_invalid_click(self):
        with self.assertRaises(ValidationError):multibrowser.ClickPayload(x=1400,y=10)
    def test_key_whitelist(self):
        with self.assertRaises(ValidationError):multibrowser.KeyPayload(key="Control+Shift+I")

    def test_wheel_and_navigation_bounds(self):
        self.assertEqual(multibrowser.WheelPayload(dx=0,dy=650).dy,650)
        with self.assertRaises(ValidationError):
            multibrowser.WheelPayload(dx=0,dy=9000)
        with self.assertRaises(ValidationError):
            multibrowser.NavigatePayload(action="evil")
        self.assertEqual(multibrowser.NavigatePayload(action="back").action,"back")
        self.assertEqual(multibrowser.KeyPayload(key="ControlOrMeta+A").key,"ControlOrMeta+A")

    def test_direct_keyboard_capture_and_no_modal_recording(self):
        s=(Path(__file__).parent.parent/"app/templates/multibrowser.html").read_text(encoding="utf-8")
        self.assertIn('id="remoteKeyboard"',s)
        self.assertIn('oninput="remoteKeyboardInput(event)"',s)
        self.assertIn('onwheel="remoteWheel(event)"',s)
        self.assertIn("const recording=(img.id==='trainScreen'&&el('trainRecord').checked)",s)
        self.assertIn("remoteCommand('type'",s)
        self.assertIn("remoteCommand('wheel'",s)
        self.assertIn("recorded===false",s)

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
    def test_training_tabs_and_horizontal_landscape_cards(self):
        s=(Path(__file__).parent.parent/"app/templates/multibrowser.html").read_text(encoding="utf-8")
        self.assertIn('id="tab-automacao"',s)
        self.assertIn('id="tab-treino"',s)
        self.assertIn('id="tab-contas"',s)
        self.assertIn('id="tab-legendas"',s)
        self.assertIn('id="tilesRail"',s)
        self.assertIn('aspect-ratio:8/5',s)
        self.assertIn('clamp(460px,28vw,700px)',s)
        self.assertIn('flex-wrap:nowrap!important',s)
        self.assertIn('id="trainScreen"',s)
        self.assertIn('id="trainRecord"',s)
        self.assertIn("if(!confirm('Etapa",s)
        self.assertIn("login-password",s)
        self.assertIn("trainReplayPosition",s)

    def test_saved_training_flows_and_bounds(self):
        self.assertEqual(multibrowser.SaveTrainingFlow(slot=1,name="Criar Reel").slot,1)
        with self.assertRaises(ValidationError):
            multibrowser.SaveTrainingFlow(slot=17,name="Criar Reel")
        with self.assertRaises(ValidationError):
            multibrowser.LoadTrainingFlow(slot=0)
        source=(Path(__file__).parent.parent/"app/multibrowser.py").read_text(encoding="utf-8")
        self.assertIn("social_mb_saved_flows",source)
        self.assertIn('"/api/training/flows"',source)
        self.assertIn("replay-step",source)

    def test_template_inline_javascript_syntax(self):
        import shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("Node.js não disponível para checagem do JavaScript")
        s=(Path(__file__).parent.parent/"app/templates/multibrowser.html").read_text(encoding="utf-8")
        script=s.split("<script>",1)[1].split("</script>",1)[0]
        script=script.replace("{{csrf|tojson}}",'"unit-test-csrf"')
        result=subprocess.run(["node","--check"],input=script,text=True,capture_output=True,timeout=12)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_training_template_renders_without_accounts(self):
        from jinja2 import Environment, FileSystemLoader
        templates=Path(__file__).parent.parent/"app/templates"
        env=Environment(loader=FileSystemLoader(str(templates)),autoescape=True)
        html=env.get_template("multibrowser.html").render(
            csrf="test",slots=range(1,17),byslot={},captions=[],error="",
            drive_configured=False,viewport=VIEWPORT
        )
        self.assertIn('id="tileShot16"',html)
        self.assertIn('id="tileShot1"',html)
        self.assertIn("Laboratório de treino",html)
        self.assertEqual(html.count('class="tile"'),16)

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
        b.page.mouse.click.assert_awaited_once_with(20,40)
    async def test_click_exception_is_not_server_500(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.mouse.click=AsyncMock(side_effect=TypeError("unexpected argument"))
        with self.assertRaises(RuntimeError) as e:await b.click(1,20,40)
        self.assertIn("Falha ao clicar",str(e.exception))

    async def test_assisted_username_fills_correct_field(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://www.instagram.com/accounts/login/"
        loc=MagicMock()
        loc.first.fill=AsyncMock()
        b.page.locator.return_value=loc
        await b.fill_login_field(1,"username","augusto14082")
        b.page.locator.assert_called_once_with('input[name="username"]')
        loc.first.fill.assert_awaited_once_with("augusto14082",timeout=7500)

    async def test_assisted_password_uses_password_input(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://www.instagram.com/accounts/login/"
        loc=MagicMock()
        loc.first.fill=AsyncMock()
        b.page.locator.return_value=loc
        await b.fill_login_field(1,"password","dummy-only")
        b.page.locator.assert_called_once_with('input[name="password"], input[type="password"]')

    async def test_login_fields_not_sent_to_other_domains(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://example.org/"
        with self.assertRaises(RuntimeError):
            await b.fill_login_field(1,"password","dummy-only")
        b.page.locator.assert_not_called()

    async def test_password_not_on_interface_after_use(self):
        html=(Path(__file__).parent.parent/"app/templates/multibrowser.html").read_text()
        self.assertIn('type="password"',html)
        self.assertIn("input.value=''",html)
        self.assertIn("img.naturalWidth||VIEWPORT.width",html)


    async def test_remote_scroll(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.mouse.wheel=AsyncMock()
        await b.wheel(1,0,370)
        b.page.mouse.wheel.assert_awaited_once_with(0,370)

    async def test_remote_browsing_history(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.go_back=AsyncMock()
        await b.navigate(1,"back")
        b.page.go_back.assert_awaited_once_with(wait_until="domcontentloaded",timeout=15000)
        with self.assertRaises(RuntimeError):
            await b.navigate(1,"open-arbitrary-url")

    async def test_login_recording_guard_detects_password_field(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.url="https://www.instagram.com/"
        element=MagicMock()
        element.count=AsyncMock(return_value=1)
        b.page.locator.return_value=element
        self.assertTrue(await b.is_login_screen(1))
        element.count=AsyncMock(return_value=0)
        self.assertFalse(await b.is_login_screen(1))
        b.page.url="https://www.instagram.com/accounts/login/"
        self.assertTrue(await b.is_login_screen(1))

    async def test_text_not_saved(self):
        b=BrowserManager()
        b.active_slot=1
        b.page=MagicMock()
        b.page.keyboard.insert_text=AsyncMock()
        await b.type_text(1,"temporary password")
        b.page.keyboard.insert_text.assert_awaited_once()

if __name__=="__main__":unittest.main()
