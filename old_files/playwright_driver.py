

from pathlib import Path



from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)

from config.settings import PLAYWRIGHT_HEADLESS, PLAYWRIGHT_TIMEOUT


from config.settings import PLAYWRIGHT_HEADLESS, PLAYWRIGHT_TIMEOUT


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BROWSER_PROFILE_DIR = PROJECT_ROOT / "browser_profile"


class PlaywrightDriver:
    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self):
        if self.page is not None:
            return

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS
        )

        # self.context = await self.browser.new_context()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=PLAYWRIGHT_HEADLESS,
        )

        self.page = await self.context.new_page()

        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

    async def goto(self, url: str):
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        await self.page.goto(url)

    async def get_title(self) -> str:
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        return await self.page.title()

    async def get_current_url(self) -> str:
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        return self.page.url

    async def press(self, selector: str, key: str):
        if not self.page:
            raise RuntimeError("Browser not started")

        await self.page.locator(selector).press(key)

    async def fill(self, selector: str, value: str):
        if not self.page:
            raise RuntimeError("Browser not started")

        await self.page.locator(selector).fill(value)

    async def get_text(self, selector: str) -> str:
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        return await self.page.locator(selector).inner_text()

    async def click(self, selector: str):
        if not self.page:
            raise RuntimeError("Browser not started")

        await self.page.locator(selector).click()

    async def drag_to(self, source_selector: str, target_selector: str):
        if not self.page:
            raise RuntimeError("Browser not started")

        source = self.page.locator(source_selector)
        target = self.page.locator(target_selector)

        await source.drag_to(target)

    async def screenshot(self, path: str):
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        await self.page.screenshot(
            path=path,
            full_page=True,
        )

    async def stop(self):
        if self.context:
            await self.context.close()

        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None