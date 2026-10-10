"""
IICS (Informatica Cloud) UI adapter.

Navigation is driven by the asset *path* returned by the Informatica MCP
`search_objects` tool, e.g.:

    {"id": "1f0ZehpjKAZeIfED0KHeKM",
     "path": "staging/salesforce/mt_stage_salesforce_contract",
     "type": "MTT"}

The orchestrating agent calls Informatica MCP first, then passes `path`
(and optionally `id`) to this adapter. MCP servers cannot call each other
directly, so this hand-off happens in the client (Claude), not here.
"""

import re
from dataclasses import dataclass
from typing import Any

from src.browser.playwright_driver import PlaywrightDriver
from config.settings import INFORMATICA_URL


@dataclass(frozen=True)
class AssetLocation:
    project: str
    folder: str | None
    name: str

    @property
    def path(self) -> str:
        parts = [self.project, self.folder, self.name]
        return "/".join(p for p in parts if p)


def parse_asset_path(asset_path: str) -> AssetLocation:
    """
    Split an IICS v3 asset path into project / folder / asset name.

    Accepts "project/folder/asset" or "project/asset" (asset at the
    project root). IICS does not support nested folders, so anything
    deeper is rejected rather than guessed.
    """
    if not isinstance(asset_path, str) or not asset_path.strip():
        raise ValueError("asset_path must be a non-empty string.")

    parts = [p.strip() for p in asset_path.strip().strip("/").split("/")]
    if any(not p for p in parts):
        raise ValueError(f"asset_path has an empty segment: {asset_path!r}")

    if len(parts) == 3:
        return AssetLocation(project=parts[0], folder=parts[1], name=parts[2])
    if len(parts) == 2:
        return AssetLocation(project=parts[0], folder=None, name=parts[1])

    raise ValueError(
        f"Unsupported asset_path {asset_path!r}. Expected "
        "'project/folder/asset' or 'project/asset'."
    )


class InformaticaAdapter:
    SEARCH_SELECTOR = '[data-testid="table-toolbar-search"]'
    ROW_SELECTOR = "a.iics-explore__table__name__text"
    EDIT_SELECTOR = 'button:has(span.infaButton-label:text-is("Edit"))'
    PAGE_TITLE_SELECTOR = ".infaPanelTitle-name.page-title"
    HEADING_SELECTOR = (
        "h1, h2, .infaPanelTitle-name, "
        '[data-testid="breadcrumb"]'
    )
    EXPLORE_NAV_SELECTOR = (
        'a:has(span.d-shell__nav__links__link__label:text-is("Explore"))'
    )
    ITEMS_PER_PAGE = "100"

    def __init__(self, browser: PlaywrightDriver):
        self.browser = browser

    # ------------------------------------------------------------------ #
    # Basic page helpers
    # ------------------------------------------------------------------ #
    # Must stay below the MCP client's request timeout, or the call errors
    # out as "Request timed out" instead of returning ready=False.
    LOGIN_WAIT_MS = 40000

    async def open(self) -> dict:
        """
        Open IICS and wait until the app has actually loaded.

        After goto() the page may still be on the login / identity-service
        redirect. Wait for the left-hand Explore link, which only appears
        once you are signed in, so this never reports a blank page as open.
        """
        await self.browser.goto(INFORMATICA_URL)
        page = self._get_page()
        try:
            await page.locator(self.EXPLORE_NAV_SELECTOR).wait_for(
                state="visible", timeout=self.LOGIN_WAIT_MS
            )
            ready = True
        except Exception:
            ready = False

        return {
            "ready": ready,
            "note": "" if ready else (
                "Informatica did not finish loading within "
                f"{self.LOGIN_WAIT_MS // 1000}s. Complete the login in the "
                "browser window, then call open_informatica again."
            ),
            "url": page.url,
            "title": await page.title(),
        }

    async def _ensure_iics_loaded(self) -> dict:
        """
        Return immediately if the IICS app (left-hand Explore link) is
        visible. Otherwise open IICS and wait for it; if it still is not
        ready (for example, a login is needed), raise a clear error.
        """
        page = self._get_page()
        explore = page.locator(self.EXPLORE_NAV_SELECTOR)
        if await explore.count() and await explore.first.is_visible():
            return {"step": "iics", "opened": False, "url": page.url}

        result = await self.open()
        if not result.get("ready"):
            raise RuntimeError(
                "IICS is not loaded yet (current page: "
                f"{result.get('url')!r}). If a login screen is showing, sign "
                "in in the browser window, then run this tool again."
            )
        return {"step": "iics", "opened": True, "url": result.get("url")}

    async def inspect_page(self) -> dict[str, str]:
        page = self._get_page()
        return {"url": page.url, "title": await page.title()}

    def _get_page(self):
        page = self.browser.page
        if page is None:
            raise RuntimeError("Playwright has not been started.")
        return page

    async def _click_unique(self, locator: Any, description: str) -> None:
        count = await locator.count()
        if count != 1:
            raise RuntimeError(
                f"Expected exactly one match for {description}, found {count}."
            )
        await locator.click()

    # ------------------------------------------------------------------ #
    # Explore listing
    # ------------------------------------------------------------------ #
    async def _wait_for_explore_listing(self) -> None:
        page = self._get_page()
        await page.locator(self.SEARCH_SELECTOR).wait_for(
            state="visible", timeout=15000
        )
        await page.locator(self.ROW_SELECTOR).first.wait_for(
            state="visible", timeout=15000
        )

    async def _set_page_size(self) -> dict:
        """Steps 2 / 4 / 6: set Items per Page to 100 on the current listing."""
        return await self.browser.set_items_per_page(self.ITEMS_PER_PAGE)

    ALL_PROJECTS_SUFFIX = "/explore/allProjects"
    ALL_PROJECTS_LABEL = "All Projects"

    async def _open_explore(self) -> dict:
        """
        Step 1: open Explore and make sure the listing is at All Projects.

        Always starting from All Projects keeps navigation deterministic,
        even when the browser was left inside a project or folder by an
        earlier run.
        """
        page = self._get_page()
        search = page.locator(self.SEARCH_SELECTOR)

        if not (await search.count() and await search.first.is_visible()):
            await self._click_unique(
                page.locator(self.EXPLORE_NAV_SELECTOR),
                "Explore navigation link",
            )
            await self._wait_for_explore_listing()

        if page.url.endswith(self.ALL_PROJECTS_SUFFIX):
            return {"step": "explore", "reset": False, "url": page.url}

        # Inside a project or folder: go back to All Projects. Prefer the
        # breadcrumb link; fall back to the URL, which Explore uses directly
        # (observed: .../explore/allProjects).
        old_url = page.url
        crumb = page.get_by_role("link", name=self.ALL_PROJECTS_LABEL, exact=True)
        if await crumb.count() == 1:
            await crumb.click()
        elif "/explore/" in old_url:
            await page.goto(old_url.split("/explore/")[0] + self.ALL_PROJECTS_SUFFIX)
        else:
            raise RuntimeError(
                f"Cannot return to All Projects from {old_url!r}."
                + await self._location_summary()
            )

        await self._wait_for_drill_down(old_url, self.ALL_PROJECTS_LABEL)
        await self._wait_for_explore_listing()
        return {"step": "explore", "reset": True, "url": page.url}

    async def _location_summary(self) -> str:
        """Short description of where the browser is, for error messages."""
        page = self._get_page()
        try:
            lines = await page.evaluate(
                """() => (document.body.innerText || '')
                    .split('\\n').map(s => s.trim())
                    .filter(s => /\\(\\d+\\)$/.test(s)).slice(0, 5)"""
            )
            rows = await self._get_rendered_rows()
        except Exception:  # diagnostics must never mask the real error
            lines, rows = [], []
        return (
            f" Current URL: {page.url}. Headings with counts: {lines}. "
            f"First rows: {[r.get('title') or r.get('text') for r in rows[:10]]}"
        )

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

    async def _wait_for_drill_down(
        self,
        old_url: str,
        name: str,
        timeout_ms: int = 15000,
    ) -> None:
        """
        Wait until Explore has navigated into `name`.

        Both must be true (polled until timeout):
          * the URL changed: Explore changes it on every drill-down
            (allProjects -> project id -> folder id);
          * a line of page text reads exactly "<name> (<count>)", i.e. the
            heading, e.g. "staging (43)".

        `old_url` must be captured BEFORE the click.
        """
        page = self._get_page()
        try:
            await page.wait_for_function(
                """({ oldUrl, name }) => {
                    if (location.href === oldUrl) return false;
                    const prefix = name + ' (';
                    return (document.body.innerText || '')
                        .split('\\n')
                        .map(s => s.trim())
                        .some(s => s.startsWith(prefix) &&
                                   /^\\d+\\)$/.test(s.slice(prefix.length)));
                }""",
                arg={"oldUrl": old_url, "name": name},
                timeout=timeout_ms,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Did not reach {name!r} within {timeout_ms} ms "
                f"(URL before click: {old_url})."
                + await self._location_summary()
            ) from exc

    async def _wait_for_rows_replaced(
        self,
        old_rows: list[dict],
        timeout_ms: int = 15000,
    ) -> bool:
        """
        After the heading changes, wait for the table rows to re-render.

        IICS updates the heading before the rows, so reading rows right
        after the heading changes can return the previous listing. Returns
        False (instead of raising) if rows never change, e.g. an empty
        container; callers decide whether that matters.
        """
        page = self._get_page()
        try:
            await page.wait_for_function(
                """({ oldRows, rowSelector }) => {
                    const rows = Array.from(document.querySelectorAll(rowSelector))
                        .map(el => ({
                            text: (el.innerText || '').trim(),
                            title: (el.getAttribute('title') || '').trim()
                        }));
                    return JSON.stringify(rows) !== JSON.stringify(oldRows);
                }""",
                arg={"oldRows": old_rows, "rowSelector": self.ROW_SELECTOR},
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def _wait_for_exact_row(self, name: str, timeout_ms: int = 15000) -> None:
        """Wait until a row whose text or title equals `name` is rendered."""
        page = self._get_page()
        await page.wait_for_function(
            """({ name, rowSelector }) =>
                Array.from(document.querySelectorAll(rowSelector)).some(el =>
                    (el.innerText || '').trim() === name ||
                    (el.getAttribute('title') || '').trim() === name
                )""",
            arg={"name": name, "rowSelector": self.ROW_SELECTOR},
            timeout=timeout_ms,
        )

    async def _click_exact_row(self, name: str, description: str) -> None:
        """Click the single row whose title attribute or text equals `name`."""
        page = self._get_page()

        # The row <a> carries the title attribute itself, so match it with
        # an attribute selector. (filter(has=...) only checks descendants
        # and never matched the row element.)
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        title_matches = page.locator(f'{self.ROW_SELECTOR}[title="{escaped}"]')
        text_matches = page.locator(self.ROW_SELECTOR).filter(
            has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
        )

        title_count = await title_matches.count()
        text_count = await text_matches.count()

        if title_count == 1:
            target = title_matches
        elif text_count == 1:
            target = text_matches
        else:
            rendered = await self._get_rendered_rows()
            raise RuntimeError(
                f"Could not uniquely identify {description} {name!r}. "
                f"Text matches: {text_count}; title matches: {title_count}. "
                f"Rendered rows: {rendered[:50]}"
            )

        await target.click()

    async def _find(self, term: str, expected_name: str) -> list[dict]:
        """
        Step 7: type into Find and wait for the exact expected row to render.

        Returns the rendered rows so callers can verify uniqueness.
        """
        page = self._get_page()
        search = page.locator(self.SEARCH_SELECTOR)
        await search.wait_for(state="visible", timeout=15000)
        await search.fill(term)
        await search.press("Enter")

        try:
            await self._wait_for_exact_row(expected_name)
        except Exception as exc:
            rendered = await self._get_rendered_rows()
            raise RuntimeError(
                f"Find for {term!r} did not render {expected_name!r}. "
                f"Rendered rows: {rendered[:50]}"
            ) from exc

        return await self._get_rendered_rows()

    async def _open_container(self, name: str, description: str) -> dict:
        """
        Open a project or folder row, then set page size on the new listing.

        Falls back to Find if the row is not on the current page.
        """
        page = self._get_page()
        await self._wait_for_explore_listing()
        await self._set_page_size()

        rendered = await self._get_rendered_rows()
        matches = [r for r in rendered if self._row_matches(r, name)]
        method = "row_click"

        if not matches:
            rendered = await self._find(name, name)
            matches = [r for r in rendered if self._row_matches(r, name)]
            method = "find"

        if len(matches) != 1:
            raise RuntimeError(
                f"Found {len(matches)} exact rows for {description} "
                f"{name!r}: {matches}"
            )

        # Capture the baseline BEFORE clicking.
        old_url = page.url
        old_rows = rendered

        await self._click_exact_row(name, description)

        # Success = URL changed and heading reads "<name> (<count>)".
        await self._wait_for_drill_down(old_url, name)

        # Then let the rows catch up with the heading before anyone reads
        # them. Not fatal on its own: the URL + heading check already
        # proved we arrived.
        rows_replaced = await self._wait_for_rows_replaced(old_rows)
        await self._wait_for_explore_listing()

        return {
            "opened": name,
            "type": description,
            "method": method,
            "url": page.url,
            "rows_replaced": rows_replaced,
        }

    # ------------------------------------------------------------------ #
    # Mapping task
    # ------------------------------------------------------------------ #
    async def _open_task_and_edit(self, mapping_task: str) -> dict:
        """Steps 6–9: page size, Find, open exact task, click Edit, verify title."""
        page = self._get_page()

        await self._set_page_size()

        # Search the full task name, not a fragment like "contract", so the
        # exact-match check is meaningful.
        rendered = await self._find(mapping_task, mapping_task)
        matches = [r for r in rendered if self._row_matches(r, mapping_task)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Find returned {len(matches)} exact matches for mapping "
                f"task {mapping_task!r}. Find may be searching outside the "
                f"current folder. Rendered rows: {rendered[:50]}"
            )

        await self._click_exact_row(mapping_task, "mapping task")

        edit = page.locator(self.EDIT_SELECTOR)
        await edit.first.wait_for(state="visible", timeout=15000)
        await self._click_unique(edit, "Edit button")

        title = page.locator(self.PAGE_TITLE_SELECTOR)
        await title.first.wait_for(state="visible", timeout=15000)
        actual_title = (await title.first.inner_text()).strip()
        if actual_title != mapping_task:
            raise RuntimeError(
                f"Editor title {actual_title!r} does not match requested "
                f"mapping task {mapping_task!r}."
            )

        return {
            "opened": mapping_task,
            "type": "mapping task",
            "mode": "edit",
            "editor_title": actual_title,
            "url": page.url,
        }

    async def open_mapping_task(
        self,
        asset_path: str | None = None,
        project: str | None = None,
        folder: str | None = None,
        mapping_task: str | None = None,
        expected_asset_id: str | None = None,
    ) -> dict:
        """
        Navigate Explore → project → folder → mapping task → Edit.

        Preferred input is `asset_path` from Informatica MCP
        `search_objects` (e.g. "staging/salesforce/mt_stage_salesforce_contract").
        Explicit project / folder / mapping_task are still accepted.

        No fields are changed and nothing is saved.
        """
        if asset_path:
            loc = parse_asset_path(asset_path)
            for label, given, parsed in (
                ("project", project, loc.project),
                ("folder", folder, loc.folder),
                ("mapping_task", mapping_task, loc.name),
            ):
                if given and given != parsed:
                    raise ValueError(
                        f"{label}={given!r} conflicts with asset_path "
                        f"{asset_path!r} (parsed {parsed!r})."
                    )
        else:
            if not (project and mapping_task):
                raise ValueError(
                    "Provide asset_path, or at least project and mapping_task."
                )
            loc = AssetLocation(project=project, folder=folder, name=mapping_task)

        page = self._get_page()
        navigation: list[dict] = []

        # Step 0: make sure IICS is actually loaded. After the browser window
        # is closed, the MCP relaunches on about:blank; open IICS first
        # instead of failing on a missing Explore link.
        navigation.append(await self._ensure_iics_loaded())

        navigation.append(await self._open_explore())                # 1 (+ reset)
        navigation.append(await self._open_container(loc.project, "project"))  # 2–3
        if loc.folder:
            navigation.append(await self._open_container(loc.folder, "folder"))  # 4–5
        navigation.append(await self._open_task_and_edit(loc.name))   # 6–9

        # The editor URL is .../mct/<v3 asset id>/edit (confirmed in DEV),
        # so the opened task can be checked against Informatica MCP's id.
        m = re.search(r"/mct/([^/]+)/edit", page.url)
        url_asset_id = m.group(1) if m else None
        if expected_asset_id and url_asset_id != expected_asset_id:
            raise RuntimeError(
                f"Opened asset id {url_asset_id!r} does not match expected "
                f"{expected_asset_id!r}. Stopping before any edit."
            )
        if expected_asset_id:
            verification = "matched"
        elif url_asset_id:
            verification = "not_requested"
        else:
            verification = "id_not_in_url"

        return {
            "success": True,
            "asset_path": loc.path,
            "project": loc.project,
            "folder": loc.folder,
            "mapping_task": loc.name,
            "expected_asset_id": expected_asset_id,
            "asset_id_in_url": url_asset_id,
            "asset_id_verification": verification,
            "navigation": navigation,
            "changes_made": False,
            "saved": False,
            "url": page.url,
            "title": await page.title(),
        }
