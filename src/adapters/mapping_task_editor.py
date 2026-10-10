"""
IICS mapping task editor: steps 10-30 of the field-promotion workflow.

Every method assumes the mapping task is already open in Edit mode
(InformaticaAdapter.open_mapping_task). Selectors and waits below were
confirmed on mt_stage_salesforce_contract in IICS DEV on 2026-10-08.

Safety rules built in:
  * Nothing here clicks Run.
  * update_source_query defaults to dry_run=True (reads, plans, cancels).
  * save_mapping_task refuses unless confirm=True, and checks the editor
    title matches the task you expect before clicking Save.
"""

from __future__ import annotations

import re
from typing import Any

from src.browser.playwright_driver import PlaywrightDriver
from src.field_planning import add_field_to_query, query_columns


class EditorError(RuntimeError):
    """Raised when the editor is not in the state a step expects."""


class MappingTaskEditor:
    # ---- confirmed selectors / texts --------------------------------- #
    TITLE = ".infaPanelTitle-name.page-title"
    TAB = 'text="{name}"'  # General / Sources / Targets / Input Parameters

    # Sources tab
    SOURCES_READY = "text=/Sources - Choose parameter values/"
    QUERY_LINK = r"text=/^\s*select\s/i"  # the blue query text
    QUERY_DIALOG_TITLE = 'text="Edit Custom Query"'
    QUERY_BOX = "textarea >> visible=true"
    VALIDATE_BTN = 'text="Validate" >> visible=true'
    QUERY_VALID_TEXT = "The custom query is valid."

    # Targets tab
    TARGETS_READY = "text=/Object \\(p_target_object/"
    PICKER_TITLE = 'text="Select Target Object"'
    REFRESH_BTN = "#refreshCacheBtn"
    PICKER_SEARCH = 'input[placeholder="Name, Label, Description, Type"] >> visible=true'
    PICKER_SEARCH_BTN = 'text="Search" >> visible=true'
    PICKER_STATUS = r"text=/Displaying (all )?\d+ (matching )?object/"

    # Input Parameters tab
    PARAMS_READY = "text=/Target Fields: \\(/"
    TARGET_FIND = 'input[placeholder="Find"] >> visible=true >> nth=1'

    # Shared buttons (only one visible at a time in each dialog)
    OK_BTN = 'text="OK" >> visible=true'
    CANCEL_BTN = 'text="Cancel" >> visible=true'
    SAVE_BTN = 'text="Save" >> visible=true'

    # Waits (ms). Each must stay under the MCP client request timeout.
    TAB_WAIT = 45000
    VALIDATE_WAIT = 45000
    PICKER_WAIT = 45000

    def __init__(self, browser: PlaywrightDriver):
        self.browser = browser

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _page(self):
        if self.browser.page is None or self.browser.page.is_closed():
            raise EditorError("Browser is not open.")
        return self.browser.page

    async def _require_editor(self, expected_task: str | None = None) -> str:
        """Confirm a mapping task editor is open; return its title."""
        page = self._page()
        if "/mct/" not in page.url or not page.url.rstrip("/").endswith("/edit"):
            raise EditorError(
                f"No mapping task editor is open (URL: {page.url}). "
                "Run open_mapping_task first."
            )
        title = page.locator(self.TITLE).first
        await title.wait_for(state="visible", timeout=15000)
        actual = (await title.inner_text()).strip()
        if expected_task and actual != expected_task:
            raise EditorError(
                f"Editor shows {actual!r}, expected {expected_task!r}."
            )
        return actual

    async def _open_tab(self, name: str, ready_selector: str) -> None:
        page = self._page()
        await page.locator(self.TAB.format(name=name)).first.click()
        await page.locator(ready_selector).first.wait_for(
            state="visible", timeout=self.TAB_WAIT
        )

    async def _unique(self, selector: str, what: str):
        loc = self._page().locator(selector)
        n = await loc.count()
        if n != 1:
            raise EditorError(f"Expected exactly one {what}, found {n} ({selector}).")
        return loc

    def _asset_id_from_url(self) -> str | None:
        m = re.search(r"/mct/([^/]+)/edit", self._page().url)
        return m.group(1) if m else None

    # ------------------------------------------------------------------ #
    # Sources: read / update the query (steps 10-17)
    # ------------------------------------------------------------------ #
    async def _open_query_dialog(self) -> None:
        page = self._page()
        await self._open_tab("Sources", self.SOURCES_READY)
        link = page.locator(self.QUERY_LINK).first
        await link.wait_for(state="visible", timeout=self.TAB_WAIT)
        await link.click()
        await page.locator(self.QUERY_DIALOG_TITLE).wait_for(
            state="visible", timeout=15000
        )
        await page.locator(self.QUERY_BOX).wait_for(state="visible", timeout=15000)

    async def _close_query_dialog(self, button: str) -> None:
        page = self._page()
        await (await self._unique(button, "dialog button")).click()
        await page.locator(self.QUERY_DIALOG_TITLE).wait_for(
            state="hidden", timeout=15000
        )

    async def read_source_query(self, expected_task: str | None = None) -> dict:
        """READ-ONLY. Open the query dialog, read the full query, Cancel."""
        task = await self._require_editor(expected_task)
        await self._open_query_dialog()
        query = await self._page().locator(self.QUERY_BOX).input_value()
        await self._close_query_dialog(self.CANCEL_BTN)
        return {
            "mapping_task": task,
            "query": query,
            "columns": query_columns(query),
            "changes_made": False,
        }

    async def update_source_query(
        self,
        field_names: list[str],
        expected_task: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """
        Add fields to the end of the source query's SELECT list.

        dry_run=True: read, build the new query, Cancel. Nothing changes.
        dry_run=False: fill, read back, Validate, and click OK only if IICS
        says "The custom query is valid." On any other outcome the dialog is
        cancelled so the original query stays in place. Nothing is saved.
        """
        if not field_names:
            raise ValueError("field_names must contain at least one field.")

        task = await self._require_editor(expected_task)
        page = self._page()
        await self._open_query_dialog()
        before = await page.locator(self.QUERY_BOX).input_value()

        existing = {c.lower() for c in query_columns(before)}
        to_add = [f for f in field_names if f.lower() not in existing]
        already = [f for f in field_names if f.lower() in existing]

        after = before
        for f in to_add:
            after = add_field_to_query(after, f)

        result: dict[str, Any] = {
            "mapping_task": task,
            "added": to_add,
            "already_present": already,
            "before": before,
            "after": after,
            "dry_run": dry_run,
        }

        if dry_run or not to_add:
            await self._close_query_dialog(self.CANCEL_BTN)
            result.update(changes_made=False, validated=None)
            return result

        box = page.locator(self.QUERY_BOX)
        await box.fill(after)
        if await box.input_value() != after:
            await self._close_query_dialog(self.CANCEL_BTN)
            raise EditorError("Query box did not accept the new text; cancelled.")

        await (await self._unique(self.VALIDATE_BTN, "Validate button")).click()
        try:
            await page.get_by_text(self.QUERY_VALID_TEXT, exact=True).wait_for(
                state="visible", timeout=self.VALIDATE_WAIT
            )
        except Exception as exc:
            dialog_text = await page.locator(self.QUERY_DIALOG_TITLE).locator(
                "xpath=ancestor::*[.//textarea][1]"
            ).inner_text()
            await self._close_query_dialog(self.CANCEL_BTN)
            raise EditorError(
                "IICS did not report the query as valid; the change was "
                f"cancelled. Dialog text: {dialog_text[:1000]!r}"
            ) from exc

        await self._close_query_dialog(self.OK_BTN)
        result.update(
            changes_made=True,
            validated=True,
            validation_message=self.QUERY_VALID_TEXT,
            saved=False,
        )
        return result

    # ------------------------------------------------------------------ #
    # Targets: refresh metadata and reselect the object (steps 18-24)
    # ------------------------------------------------------------------ #
    async def refresh_target(
        self,
        target_object: str,
        expected_task: str | None = None,
    ) -> dict:
        """
        Re-read target metadata so new Snowflake columns appear.

        Opens the target object picker, clicks refresh (#refreshCacheBtn),
        searches for `target_object`, selects the single exact match, OK.
        """
        task = await self._require_editor(expected_task)
        page = self._page()
        await self._open_tab("Targets", self.TARGETS_READY)

        current = await self._unique(
            f'text="{target_object}" >> visible=true', f"target link {target_object!r}"
        )
        await current.click()
        await page.locator(self.PICKER_TITLE).wait_for(
            state="visible", timeout=self.PICKER_WAIT
        )
        await page.locator(self.PICKER_STATUS).first.wait_for(
            state="visible", timeout=self.PICKER_WAIT
        )

        await (await self._unique(self.REFRESH_BTN, "refresh button")).click()
        search = page.locator(self.PICKER_SEARCH)
        await search.wait_for(state="visible", timeout=self.PICKER_WAIT)
        # The search box is disabled while the refresh runs.
        await page.wait_for_function(
            "el => el && !el.disabled",
            arg=await search.element_handle(),
            timeout=self.PICKER_WAIT,
        )

        await search.fill(target_object)
        await (await self._unique(self.PICKER_SEARCH_BTN, "Search button")).click()
        await page.locator(r"text=/Displaying \d+ matching object/").first.wait_for(
            state="visible", timeout=self.PICKER_WAIT
        )

        # Pick the row whose Name cell is exactly target_object.
        # The picker nests tables, so outer layout rows also "contain" the
        # match. Keep only innermost rows (no nested <tr>) with a radio.
        row = (
            page.locator('tr:not(:has(tr)):has(input[type="radio"]) >> visible=true')
            .filter(
                has=page.locator(
                    "td", has_text=re.compile(rf"^\s*{re.escape(target_object)}\s*$")
                )
            )
        )
        n = await row.count()
        if n != 1:
            await (await self._unique(self.CANCEL_BTN, "Cancel button")).click()
            raise EditorError(
                f"Expected one picker row named {target_object!r}, found {n}; "
                "picker cancelled."
            )
        await row.locator('input[type="radio"]').click()
        await (await self._unique(self.OK_BTN, "OK button")).click()
        await page.locator(self.PICKER_TITLE).wait_for(state="hidden", timeout=30000)

        return {
            "mapping_task": task,
            "target_object": target_object,
            "refreshed": True,
            "changes_made": True,
            "saved": False,
        }

    # ------------------------------------------------------------------ #
    # Input Parameters: verify field mapping (steps 25-27)
    # ------------------------------------------------------------------ #
    async def verify_mapping(
        self,
        target_fields: list[str],
        expected_task: str | None = None,
        screenshot_path: str | None = None,
    ) -> dict:
        """
        READ-ONLY. For each target field, read Name / Type / Expression.

        IICS maps fields automatically when source and target names match
        (ignoring case), so most new fields arrive already mapped. Fields
        with an empty Expression are reported as unmapped; mapping those
        (drag-and-drop) is not automated yet.
        """
        task = await self._require_editor(expected_task)
        page = self._page()
        await self._open_tab("Input Parameters", self.PARAMS_READY)

        counts = await page.evaluate(
            """() => (document.body.innerText || '').split('\\n')
                .map(s => s.trim())
                .filter(s => /^(Incoming|Target) Fields: \\(\\d+ of \\d+ mapped\\)$/.test(s))"""
        )

        rows: list[dict] = []
        for field in target_fields:
            name = field.upper()
            cell = page.locator(f"text=/^{re.escape(name)}$/")
            if await cell.count() == 0:
                # Row not rendered (long list): filter the target side.
                find = page.locator(self.TARGET_FIND)
                await find.fill(name)
                await find.press("Enter")
                await cell.first.wait_for(state="attached", timeout=15000)
            tr = cell.first.locator("xpath=ancestor::tr[1]")
            parts = [p.strip() for p in (await tr.inner_text()).split("\t")]
            parts += [""] * (3 - len(parts))
            rows.append({
                "target_field": parts[0],
                "type": parts[1],
                "expression": parts[2],
                "mapped": bool(parts[2]),
            })

        if screenshot_path:
            await page.screenshot(path=screenshot_path, full_page=True)

        return {
            "mapping_task": task,
            "field_counts": counts,
            "fields": rows,
            "all_mapped": all(r["mapped"] for r in rows),
            "unmapped": [r["target_field"] for r in rows if not r["mapped"]],
            "screenshot": screenshot_path,
            "changes_made": False,
        }

    # ------------------------------------------------------------------ #
    # Save (step 30) - gated
    # ------------------------------------------------------------------ #
    async def save_mapping_task(
        self,
        expected_task: str,
        confirm: bool = False,
    ) -> dict:
        """
        Click Save (never Run). Requires confirm=True and a matching title.

        Call only after the user has approved the change (Gate 3). Then
        confirm with Informatica MCP search_objects that updateTime changed.
        """
        if not confirm:
            raise EditorError(
                "save_mapping_task requires confirm=True, given only after the "
                "user has approved the mapping (Gate 3)."
            )
        task = await self._require_editor(expected_task)
        page = self._page()

        save = await self._unique(self.SAVE_BTN, "Save button")
        await save.click()
        # IICS greys out Save once the save completes.
        try:
            await page.wait_for_function(
                """el => el && (el.disabled ||
                       el.getAttribute('aria-disabled') === 'true' ||
                       (el.closest('button') || el).disabled)""",
                arg=await save.element_handle(),
                timeout=30000,
            )
            save_state = "save_button_disabled"
        except Exception:
            save_state = "unconfirmed_check_updateTime"

        return {
            "mapping_task": task,
            "asset_id_in_url": self._asset_id_from_url(),
            "saved": True,
            "save_state": save_state,
            "next": "Confirm via Informatica MCP search_objects that updateTime changed.",
        }
