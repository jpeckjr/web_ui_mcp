# Backup of src/adapters/informatica.py taken 2026-10-06 before the
# asset_path refactor.

import re
from typing import Any

from src.browser.playwright_driver import PlaywrightDriver
from config.settings import INFORMATICA_URL


class InformaticaAdapter:
    SEARCH_SELECTOR = '[data-testid="table-toolbar-search"]'
    ROW_SELECTOR = "a.iics-explore__table__name__text"
    EDIT_SELECTOR = (
        'button:has(span.infaButton-label:text-is("Edit"))'
    )
    PAGE_TITLE_SELECTOR = ".infaPanelTitle-name.page-title"
    HEADING_SELECTOR = (
        "h1, h2, .infaPanelTitle-name, "
        '[data-testid="breadcrumb"]'
    )

    def __init__(self, browser: PlaywrightDriver):
        self.browser = browser

    async def open(self) -> dict[str, str]:
        await self.browser.goto(INFORMATICA_URL)
        return {
            "url": await self.browser.get_current_url(),
            "title": await self.browser.get_title(),
        }

    async def inspect_page(self) -> dict[str, str]:
        page = self._get_page()
        return {
            "url": page.url,
            "title": await page.title(),
        }

    def _get_page(self):
        page = self.browser.page
        if page is None:
            raise RuntimeError(
                "Playwright has not been started."
            )
        return page

    async def _click_unique(self, locator: Any, description: str) -> None:
        count = await locator.count()
        if count != 1:
            raise RuntimeError(
                f"Expected exactly one match for {description}, "
                f"found {count}."
            )
        await locator.click()

    async def _wait_for_explore_listing(self) -> None:
        page = self._get_page()
        await page.locator(self.SEARCH_SELECTOR).wait_for(state="visible", timeout=15000)
        await page.locator(self.ROW_SELECTOR).first.wait_for(state="visible", timeout=15000)

    async def _open_explore(self) -> None:
        page = self._get_page()
        search = page.locator(self.SEARCH_SELECTOR)
        if await search.count() and await search.first.is_visible():
            return
        explore = page.locator(
            'a:has(span.d-shell__nav__links__link__label'
            ':text-is("Explore"))'
        )
        await self._click_unique(explore, "Explore navigation link")
        await self._wait_for_explore_listing()

    async def _get_rendered_rows(self) -> list[dict]:
        page = self._get_page()
        return await page.locator(self.ROW_SELECTOR).evaluate_all(
            """els => els.map(el => ({
                text: (el.innerText || '').trim(),
                title: (el.getAttribute('title') || '').trim()
            }))"""
        )

    @staticmethod
    def _row_matches(row: dict, name: str) -> bool:
        return (
            (row.get("text") or "").strip() == name
            or (row.get("title") or "").strip() == name
        )

    async def _wait_for_listing_change(self, old_rows, expected_name, timeout_ms=15000) -> None:
        page = self._get_page()
        await page.wait_for_function(
            """({ oldRows, expectedName, rowSelector, headingSelector }) => {
                const rows = Array.from(document.querySelectorAll(rowSelector));
                const currentRows = rows.map(el => ({
                    text: (el.innerText || '').trim(),
                    title: (el.getAttribute('title') || '').trim()
                }));
                const changed = JSON.stringify(currentRows) !== JSON.stringify(oldRows);
                const headings = Array.from(document.querySelectorAll(headingSelector))
                    .map(el => (el.innerText || '').trim());
                const headingMatches = headings.some(text =>
                    text === expectedName ||
                    text.startsWith(expectedName + ' ') ||
                    text.startsWith(expectedName + ' ('));
                return changed || headingMatches;
            }""",
            arg={
                "oldRows": old_rows,
                "expectedName": expected_name,
                "rowSelector": self.ROW_SELECTOR,
                "headingSelector": self.HEADING_SELECTOR,
            },
            timeout=timeout_ms,
        )
        await self._wait_for_explore_listing()

    async def _click_exact_row(self, name: str, description: str) -> None:
        page = self._get_page()
        rows = page.locator(self.ROW_SELECTOR)
        await rows.first.wait_for(state="visible", timeout=15000)
        title_matches = rows.filter(has=page.locator(f'[title="{name}"]'))
        title_count = await title_matches.count()
        text_matches = rows.filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"))
        text_count = await text_matches.count()
        if title_count == 1:
            target = title_matches
        elif text_count == 1:
            target = text_matches
        else:
            rendered = await self._get_rendered_rows()
            raise RuntimeError(
                f"Could not uniquely identify {description} "
                f"{name!r}. Text matches: {text_count}; "
                f"title matches: {title_count}. "
                f"Rendered rows: {rendered[:50]}"
            )
        await target.click()

    async def _search_and_open_item(self, name: str, description: str) -> dict:
        page = self._get_page()
        search = page.locator(self.SEARCH_SELECTOR)
        await search.wait_for(state="visible", timeout=15000)
        await search.fill(name)
        await search.press("Enter")
        await page.wait_for_timeout(500)
        rows = page.locator(self.ROW_SELECTOR)
        await rows.first.wait_for(state="visible", timeout=15000)
        rendered = await self._get_rendered_rows()
        matches = [row for row in rendered if self._row_matches(row, name)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Find returned {len(matches)} exact matches for "
                f"{description} {name!r}. "
                f"Rendered rows: {rendered[:50]}. "
                "Confirm the search scope and result before proceeding."
            )
        await self._click_exact_row(name, description)
        return {"opened": name, "type": description, "method": "Find", "url": page.url}

    async def _open_named_explore_item(self, name: str, description: str) -> dict:
        page = self._get_page()
        await self._wait_for_explore_listing()
        rendered = await self._get_rendered_rows()
        matches = [row for row in rendered if self._row_matches(row, name)]
        if len(matches) == 0:
            return await self._search_and_open_item(name, description)
        if len(matches) != 1:
            raise RuntimeError(
                f"Found {len(matches)} exact rows for "
                f"{description} {name!r}: {matches}"
            )
        old_rows = rendered
        await self._click_exact_row(name, description)
        await self._wait_for_listing_change(old_rows=old_rows, expected_name=name)
        current_rows = await self._get_rendered_rows()
        if current_rows == old_rows:
            raise RuntimeError(
                f"Clicked {description} {name!r}, but the Explore "
                "listing did not change."
            )
        return {"opened": name, "type": description, "method": "row_click", "url": page.url}

    async def _search_mapping_task(self, mapping_task: str) -> dict:
        page = self._get_page()
        await self._search_and_open_item(mapping_task, "mapping task")
        title = page.locator(self.PAGE_TITLE_SELECTOR)
        await title.wait_for(state="visible", timeout=15000)
        actual_title = (await title.inner_text()).strip()
        if actual_title != mapping_task:
            raise RuntimeError(
                f"Opened editor title {actual_title!r} does not match "
                f"requested mapping task {mapping_task!r}."
            )
        return {
            "opened": mapping_task,
            "type": "mapping task",
            "editor_title": actual_title,
            "url": page.url,
        }

    async def open_mapping_task(self, project, folder, mapping_task, expected_asset_id=None) -> dict:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (project, folder, mapping_task)
        ):
            raise ValueError(
                "project, folder, and mapping_task "
                "must be non-empty strings."
            )
        page = self._get_page()
        await self._open_explore()
        project_result = await self._open_named_explore_item(project, "project")
        folder_result = await self._open_named_explore_item(folder, "folder")
        task_result = await self._search_mapping_task(mapping_task)
        return {
            "success": True,
            "project": project,
            "folder": folder,
            "requested_mapping_task": mapping_task,
            "expected_asset_id": expected_asset_id,
            "asset_id_verification": "not_performed_ui_only",
            "navigation": [project_result, folder_result, task_result],
            "changes_made": False,
            "saved": False,
            "url": page.url,
            "title": await page.title(),
        }
