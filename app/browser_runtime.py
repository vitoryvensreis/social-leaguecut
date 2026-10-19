"""Persistent single-active-browser manager for 16 fixed Instagram slots."""
import asyncio, os, re
from pathlib import Path
VIEWPORT={"width":1280,"height":800}
from .training_selectors import INSPECT_TARGET_JS

class BrowserManager:
    def __init__(self):
        self.active_slot=None
        self.active_username=None
        self.context=None
        self.page=None
        self.driver=None
        self.lock=asyncio.Lock()

    def _directory(self,slot,username):
        if not re.fullmatch(r"[a-z0-9._]{1,30}",username):
            raise RuntimeError("Usuário inválido")
        base=Path(os.getenv("LEAGUECUT_BROWSER_DIR",str(Path.home()/".local/share/leaguecut-browser")))
        base.mkdir(parents=True,exist_ok=True,mode=0o700)
        try: base.chmod(0o700)
        except OSError: pass
        path=base/f"slot-{slot:02d}-{username}"
        path.mkdir(parents=True,exist_ok=True,mode=0o700)
        return path

    async def open(self,slot,username):
        async with self.lock:
            if self.context is not None and self.active_slot==slot and self.active_username==username:
                return
            await self._close_unlocked()
            try:
                from playwright.async_api import async_playwright
                self.driver=await async_playwright().start()
                headless=os.getenv("LEAGUECUT_BROWSER_HEADLESS","1")=="1" or not os.getenv("DISPLAY")
                self.context=await self.driver.chromium.launch_persistent_context(
                    user_data_dir=str(self._directory(slot,username)),headless=headless,
                    viewport=VIEWPORT,device_scale_factor=1,is_mobile=False,has_touch=False,accept_downloads=False,
                    locale="pt-BR",args=["--no-first-run","--disable-dev-shm-usage"])
                self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()
                self.active_slot,self.active_username=slot,username
                await self.page.goto("https://www.instagram.com/",wait_until="domcontentloaded",timeout=45000)
            except Exception as exc:
                await self._close_unlocked()
                raise RuntimeError(f"Falha ao iniciar Chromium ({type(exc).__name__}). Confira a instalação Playwright na VPS.") from None

    async def _close_unlocked(self):
        context,driver=self.context,self.driver
        self.context=self.page=self.driver=None
        self.active_slot=self.active_username=None
        if context:
            try: await asyncio.wait_for(context.close(),timeout=12)
            except Exception: pass
        if driver:
            try: await driver.stop()
            except Exception: pass

    async def close(self):
        async with self.lock: await self._close_unlocked()

    def _page_for(self,slot):
        if slot!=self.active_slot or self.page is None:
            raise RuntimeError("Abra o navegador desta conta primeiro")
        return self.page

    async def screenshot(self,slot):
        async with self.lock:
            if self.active_slot!=slot or self.page is None: return None
            try: return await self.page.screenshot(type="jpeg",quality=68,timeout=10000)
            except Exception: return None

    async def click(self,slot,x,y):
        # Mouse.click takes x/y, but does not accept a timeout argument.
        async with self.lock:
            try:
                await self._page_for(slot).mouse.click(x,y)
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("Falha ao clicar na tela remota. Atualize a imagem ou reabra o navegador.") from None

    async def type_text(self,slot,value):
        async with self.lock:
            try: await self._page_for(slot).keyboard.insert_text(value)
            except RuntimeError: raise
            except Exception:
                raise RuntimeError("Falha ao digitar. Verifique se o navegador continua aberto.") from None

    async def press(self,slot,key):
        async with self.lock:
            try: await self._page_for(slot).keyboard.press(key)
            except RuntimeError: raise
            except Exception:
                raise RuntimeError("Falha ao enviar tecla. Verifique se o navegador continua aberto.") from None

    async def _is_sensitive(self,page):
        url=str(page.url or "").lower()
        if any(t in url for t in (
            "/accounts/login","/challenge/","/two_factor/","/accounts/signup",
            "/accounts/password","/checkpoint/","/accounts/onetap")):
            return True
        try:
            return await page.locator('input[type="password"],input[autocomplete="one-time-code"]').count()>0
        except Exception:
            return False

    async def is_login_screen(self,slot):
        """Never record credentials, login or verification screens."""
        async with self.lock:
            return await self._is_sensitive(self._page_for(slot))

    async def click_and_describe(self,slot,x,y):
        """Capture a DOM selector and click under one browser lock."""
        async with self.lock:
            page=self._page_for(slot)
            protected=await self._is_sensitive(page)
            target={"selector":None,"label":"","tag":""}
            if not protected:
                try:
                    value=await page.evaluate(INSPECT_TARGET_JS,{"x":x,"y":y})
                    if isinstance(value,dict):
                        target={
                            "selector":str(value["selector"])[:1024] if value.get("selector") else None,
                            "label":str(value.get("label") or "")[:100],
                            "tag":str(value.get("tag") or "")[:20]
                        }
                except Exception:
                    pass  # Coordinates remain available for supervised replay.
            await page.mouse.click(x,y)
            return {"protected":protected,**target}

    async def replay_training_click(self,slot,x,y,selector=None):
        """Try a unique HTML target, then recorded desktop coordinates."""
        async with self.lock:
            page=self._page_for(slot)
            if await self._is_sensitive(page):
                raise RuntimeError("Replay bloqueado durante login ou verificação")
            if selector:
                try:
                    target=page.locator(selector)
                    if await target.count()==1 and await target.is_visible():
                        await target.click(timeout=3000)
                        return {"method":"element","note":"Elemento HTML localizado"}
                except Exception:
                    pass
            await page.mouse.click(x,y)
            return {"method":"coordinates","note":"Elemento não encontrado; usadas coordenadas do desktop"}

    async def wheel(self,slot,dx,dy):
        """Scroll the active Chromium page like a physical mouse wheel."""
        async with self.lock:
            try:
                await self._page_for(slot).mouse.wheel(dx,dy)
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("Não foi possível rolar a página do navegador.") from None

    async def navigate(self,slot,action):
        """Basic browsing controls, without recording or changing sessions."""
        if action not in ("back","forward","reload"):
            raise RuntimeError("Ação de navegação inválida")
        async with self.lock:
            page=self._page_for(slot)
            try:
                if action=="back":
                    await page.go_back(wait_until="domcontentloaded",timeout=15000)
                elif action=="forward":
                    await page.go_forward(wait_until="domcontentloaded",timeout=15000)
                else:
                    await page.reload(wait_until="domcontentloaded",timeout=20000)
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("Não foi possível navegar. Verifique a sessão remota.") from None

    async def fill_login_field(self,slot,field,value):
        """Fill only the actual Instagram login control; no credential persistence."""
        if field not in ("username","password"):
            raise RuntimeError("Campo de login inválido")
        async with self.lock:
            page=self._page_for(slot)
            from urllib.parse import urlsplit
            if urlsplit(page.url).hostname not in ("instagram.com","www.instagram.com"):
                raise RuntimeError("Navegador não está no Instagram")
            selector=('input[name="username"]' if field=="username" else
                      'input[name="password"], input[type="password"]')
            try:
                await page.locator(selector).first.fill(value,timeout=7500)
            except Exception:
                raise RuntimeError("Campo de login indisponível. Confirme que o Instagram está na tela de login.") from None

browser_manager=BrowserManager()
