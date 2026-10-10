

from pathlib import Path



from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)

from config.settings import PLAYWRIGHT_HEADLESS, PLAYWRIGHT_TIMEOUT


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BROWSER_PROFILE_DIR = PROJECT_ROOT / "browser_profile"


class PlaywrightDriver:
    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def is_alive(self) -> bool:
        """True if the page exists and its window has not been closed."""
        return self.page is not None and not self.page.is_closed()

    async def start(self):
        if self.is_alive():
            return

        # A previous browser window was closed (by the user or a crash):
        # drop the stale handles before launching a fresh one.
        if self.page is not None or self.context is not None:
            try:
                await self.stop()
            except Exception:
                self.page = self.context = self.browser = self.playwright = None

        self.playwright = await async_playwright().start()

        # launch_persistent_context starts its own browser; a separate
        # chromium.launch() here would spawn an unused second browser.
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=PLAYWRIGHT_HEADLESS,
        )

        # Reuse the tab the persistent context opens instead of adding one.
        self.page = (
            self.context.pages[0]
            if self.context.pages
            else await self.context.new_page()
        )

        self.page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

    async def set_items_per_page(self, value: str = "100") -> dict:
        """Set and verify the Explore Items per Page dropdown."""
        if not self.page:
            raise RuntimeError("Playwright has not been started.")

        dropdown_bars = self.page.locator('[data-testid="dropdown-bar"]')
        count = await dropdown_bars.count()
        if count == 0:
            raise RuntimeError(
                "Could not find the Items per Page dropdown. "
                "Confirm that an Explore listing page is open."
            )
        if count != 1:
            raise RuntimeError(
                f"Expected one pagination dropdown, found {count}. "
                "Scope the locator to the pagination container before continuing."
            )

        dropdown = dropdown_bars.nth(0)
        value_input = dropdown.locator('[data-testid="dropdown-search"]')
        current_value = await value_input.input_value()
        if current_value == value:
            return {
                "success": True,
                "items_per_page": current_value,
                "changed": False,
            }

        await dropdown.locator('[data-testid="dropdown-button"]').click()
        option = self.page.locator(f'[data-testid="{value}"][role="option"]')
        option_count = await option.count()
        if option_count != 1:
            raise RuntimeError(
                f"Expected exactly one page-size option {value!r}, "
                f"found {option_count}."
            )
        await option.click()

        # The listing re-renders after a page-size change; wait for the
        # input to reflect the new value instead of reading it immediately.
        try:
            await self.page.wait_for_function(
                "([el, v]) => el && el.value === v",
                arg=[await value_input.element_handle(), value],
                timeout=10000,
            )
        except Exception:
            pass

        selected_value = await value_input.input_value()
        if selected_value != value:
            raise RuntimeError(
                f"Failed to set Items per Page to {value!r}; "
                f"displayed value is {selected_value!r}."
            )
        return {
            "success": True,
            "items_per_page": selected_value,
            "changed": True,
        }

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

    async def read_element(
        self,
        selector: str,
        attribute: str | None = None,
        visible_only: bool = True,
        max_chars: int = 20000,
    ) -> dict:
        """
        Read one element without interacting with it.

        * input / textarea / select -> its current value (input_value),
          which returns the FULL text even when the box is scrolled.
        * any other element        -> its rendered text (inner_text).
        * attribute given          -> that attribute's value instead.

        Fails if the selector matches zero or several elements, so a vague
        selector cannot silently read the wrong thing.
        """
        if not self.page:
            raise RuntimeError("Browser not started")

        full_selector = f"{selector} >> visible=true" if visible_only else selector
        locator = self.page.locator(full_selector)

        count = await locator.count()
        if count != 1:
            raise RuntimeError(
                f"read_element expected exactly 1 match for {full_selector!r}, "
                f"found {count}. Make the selector more specific."
            )

        tag = (await locator.evaluate("el => el.tagName")).lower()

        if attribute:
            kind = f"attribute:{attribute}"
            value = await locator.get_attribute(attribute)
        elif tag in {"input", "textarea", "select"}:
            kind = "value"
            value = await locator.input_value()
        else:
            kind = "text"
            value = await locator.inner_text()

        value = value or ""
        return {
            "selector": full_selector,
            "tag": tag,
            "kind": kind,
            "length": len(value),
            "truncated": len(value) > max_chars,
            "value": value[:max_chars],
        }

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