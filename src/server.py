from mcp.server.fastmcp import FastMCP

from src.browser.playwright_driver import PlaywrightDriver
import re

from src.adapters.informatica import InformaticaAdapter
from src.adapters.mapping_task_editor import MappingTaskEditor


mcp = FastMCP("web-ui-mcp")

browser = PlaywrightDriver()
informatica = InformaticaAdapter(browser)
editor = MappingTaskEditor(browser)

# Buttons the generic click tool must never press. Save goes through
# save_mapping_task (which requires confirm=True); Run/Delete/Publish are
# not exposed at all.
BLOCKED_CLICK_TEXT = re.compile(
    r"^\s*(run|delete|publish|save|save as|deploy)\s*$", re.I
)


async def ensure_browser_started():
    # Relaunch if never started OR if the window was closed since.
    if not browser.is_alive():
        await browser.start()


@mcp.tool()
async def click_element(selector: str) -> dict:
    """
    Click one element. Refuses Run / Delete / Publish / Save buttons;
    use save_mapping_task for saving.
    """
    await ensure_browser_started()

    target = browser.page.locator(selector)
    if await target.count() == 1:
        label = (await target.inner_text()).strip()
        if BLOCKED_CLICK_TEXT.match(label) or BLOCKED_CLICK_TEXT.match(
            re.sub(r'^text="(.*)".*$', r"\1", selector)
        ):
            raise RuntimeError(
                f"click_element refuses to press {label or selector!r}. "
                "Use save_mapping_task to save; Run/Delete are not allowed."
            )

    await browser.click(selector)

    return {
        "success": True,
        "url": await browser.get_current_url(),
        "title": await browser.get_title()
    }

@mcp.tool()
async def fill_input(selector: str, value: str) -> dict:
    await ensure_browser_started()

    await browser.fill(selector, value)

    return {
        "success": True,
        "value": value,
        "url": await browser.get_current_url()
    }

@mcp.tool()
async def open_mapping_task(
    asset_path: str | None = None,
    project: str | None = None,
    folder: str | None = None,
    mapping_task: str | None = None,
    expected_asset_id: str | None = None,
) -> dict:
    """
    Open an IICS mapping task in Edit mode without modifying it.

    WORKFLOW: first call the Informatica MCP tool `search_objects` with
    e.g. query="type=='MTT' and location=='staging/salesforce'", pick the
    object whose path ends in the target task name, then pass its `path`
    as asset_path and its `id` as expected_asset_id.

    Example: asset_path="staging/salesforce/mt_stage_salesforce_contract"

    project / folder / mapping_task may be passed instead of asset_path.
    """
    await ensure_browser_started()
    return await informatica.open_mapping_task(
        asset_path=asset_path,
        project=project,
        folder=folder,
        mapping_task=mapping_task,
        expected_asset_id=expected_asset_id,
    )


@mcp.tool()
async def read_source_query(expected_task: str | None = None) -> dict:
    """
    READ-ONLY. With a mapping task open in Edit mode (open_mapping_task),
    open Sources -> query dialog, return the full query and its columns,
    then Cancel. Changes nothing.
    """
    await ensure_browser_started()
    return await editor.read_source_query(expected_task=expected_task)


@mcp.tool()
async def update_source_query(
    field_names: list[str],
    expected_task: str | None = None,
    dry_run: bool = True,
) -> dict:
    """
    Add fields to the end of the source query (steps 10-17).

    dry_run=True (default): returns before/after and cancels; no change.
    dry_run=False: only after the user approved the new query. Fills it,
    clicks Validate, and presses OK only if IICS reports
    "The custom query is valid."; otherwise cancels. Does NOT save.
    """
    await ensure_browser_started()
    return await editor.update_source_query(
        field_names=field_names, expected_task=expected_task, dry_run=dry_run
    )


@mcp.tool()
async def refresh_target(target_object: str, expected_task: str | None = None) -> dict:
    """
    Steps 18-24: Targets tab -> open the target object picker -> refresh
    metadata (#refreshCacheBtn) -> search target_object -> select the one
    exact match -> OK. Run after the Snowflake column exists. Does NOT save.
    Example: target_object="CONTRACT".
    """
    await ensure_browser_started()
    return await editor.refresh_target(
        target_object=target_object, expected_task=expected_task
    )


@mcp.tool()
async def verify_mapping(
    target_fields: list[str],
    expected_task: str | None = None,
    screenshot_path: str | None = None,
) -> dict:
    """
    READ-ONLY. Steps 25-27: Input Parameters tab; for each target field
    return its type and Expression (mapped source field). IICS automaps
    matching names, so new fields are usually already mapped. Optionally
    saves a screenshot for the Gate 3 review.
    """
    await ensure_browser_started()
    return await editor.verify_mapping(
        target_fields=target_fields,
        expected_task=expected_task,
        screenshot_path=screenshot_path,
    )


@mcp.tool()
async def save_mapping_task(expected_task: str, confirm: bool = False) -> dict:
    """
    Step 30: click Save (never Run). ONLY call with confirm=True after the
    user has explicitly approved the mapping at Gate 3. Refuses if the open
    editor is not expected_task. Afterwards, verify updateTime changed with
    Informatica MCP search_objects.
    """
    await ensure_browser_started()
    return await editor.save_mapping_task(expected_task=expected_task, confirm=confirm)


@mcp.tool()
def generate_ddl(
    object_name: str,
    field_names: list[str],
    source_fields: list | str,
    staging_columns: list | str | dict,
    provisioning_columns: list | str | dict | None = None,
    preview: dict | str | None = None,
    ticket: str = "<ticket>",
    schema: str = "SALESFORCE",
    table: str | None = None,
    staging_database: str = "DEV_STAGING",
    provisioning_database: str = "DEV_PROVISIONING",
) -> dict:
    """
    Phase 4: build staging and provisioning ALTER TABLE DDL. Executes nothing.

    Gather inputs first (MCP servers cannot call each other):
      - source_fields: Informatica dev get_source_fields(connection,
        object_name, by="name"). Passing only the requested fields' entries
        is fine and keeps the call small.
      - staging_columns: Snowflake dev describe_table("DEV_STAGING.<schema>.<table>")
        result text as-is.
      - provisioning_columns: describe_table("DEV_PROVISIONING.<schema>.<table>").
      - preview (optional): preview_source_data, only for the string-to-date rule.

    Returns ddl (team format, SCHEMA.TABLE, for the repo), apply (one
    fully-qualified statement per column for Snowflake add_columns), and a
    Gate 2 review table. Show the review to the user and apply nothing
    until they approve (Gate 2).
    """
    from src.ddl_generator import generate_ddl as _gen

    return _gen(
        object_name=object_name,
        field_names=field_names,
        source_fields=source_fields,
        staging_columns=staging_columns,
        provisioning_columns=provisioning_columns,
        preview=preview,
        ticket=ticket,
        schema=schema,
        table=table,
        staging_database=staging_database,
        provisioning_database=provisioning_database,
    )


@mcp.tool()
def plan_field_addition(
    field_name: str,
    source_fields: list | str,
    current_query: str | None = None,
    target_columns: list[str] | None = None,
    preview: dict | str | None = None,
) -> dict:
    """
    Validate a new source field and build a change plan. Makes NO changes.

    WORKFLOW (Claude orchestrates; MCP servers cannot call each other):
      1. Informatica MCP get_source_fields(connection=<sf conn>,
         object_name=<SF object>, by="name")  -> pass as source_fields
      2. Informatica MCP preview_source_data(same args) -> pass as preview
         (only the candidate column is kept in the result)
      3. Snowflake MCP describe_table("DB.SCHEMA.TABLE") -> column names
         as target_columns
      4. Optionally pass the current source query read from the task.

    Show the returned plan to the user and wait for explicit approval
    before calling any tool that edits or saves the mapping task.
    """
    from src.field_planning import plan_field_addition as _plan

    return _plan(
        field_name=field_name,
        source_fields=source_fields,
        current_query=current_query,
        target_columns=target_columns,
        preview=preview,
    )


@mcp.tool()
async def open_informatica() -> dict:
    """
    Start the browser if necessary and open Informatica.
    """
    await ensure_browser_started()
    return await informatica.open()


@mcp.tool()
async def inspect_informatica_page() -> dict:
    """
    Return information about the current browser page.
    """
    await ensure_browser_started()
    return await informatica.inspect_page()


@mcp.tool()
async def read_element(
    selector: str,
    attribute: str | None = None,
    visible_only: bool = True,
) -> dict:
    """
    READ-ONLY: return the value or text of exactly one element.

    - Text boxes (textarea / input / select) return their full current
      value, even the part scrolled out of view. Use this to read the
      mapping task source query, e.g. selector="textarea" while the
      "Edit Custom Query" dialog is open.
    - Other elements return their visible text.
    - attribute="title" (etc.) returns that attribute instead.

    visible_only=True (default) ignores hidden elements, such as text
    boxes on wizard tabs that are not currently shown. Errors if the
    selector matches 0 or more than 1 element. Never clicks or types.
    """
    await ensure_browser_started()
    return await browser.read_element(
        selector=selector,
        attribute=attribute,
        visible_only=visible_only,
    )


@mcp.tool()
async def take_screenshot(path: str) -> str:
    """
    Take a screenshot of the current browser page.
    """
    await ensure_browser_started()
    await browser.screenshot(path)

    return f"Screenshot saved to {path}"

@mcp.tool()
async def drag_element(source_selector: str, target_selector: str) -> dict:
    await ensure_browser_started()

    await browser.drag_to(source_selector, target_selector)

    return {
        "success": True,
        "source": source_selector,
        "target": target_selector,
        "url": await browser.get_current_url()
    }


@mcp.tool()
async def open_test_site() -> dict:
    """
    Open the browser automation test website.
    """
    await ensure_browser_started()

    await browser.goto(
        "https://the-internet.herokuapp.com/"
    )

    return {
        "url": await browser.get_current_url(),
        "title": await browser.get_title(),
    }


if __name__ == "__main__":
    mcp.run()

# from mcp.server.fastmcp import FastMCP

# from src.browser.playwright_driver import PlaywrightDriver
# from src.adapters.informatica import InformaticaAdapter


# mcp = FastMCP("web-ui-mcp")

# browser = PlaywrightDriver()
# informatica = InformaticaAdapter(browser)


# def ensure_browser_started():
#     if browser.page is None:
#         browser.start()


# @mcp.tool()
# def open_informatica() -> dict:
#     """
#     Start the browser if necessary and open Informatica.
#     """

#     ensure_browser_started()

#     return informatica.open()


# @mcp.tool()
# def inspect_informatica_page() -> dict:
#     """
#     Return information about the current browser page.
#     """

#     ensure_browser_started()

#     return informatica.inspect_page()


# @mcp.tool()
# def take_screenshot(path: str) -> str:
#     """
#     Take a screenshot of the current browser page.
#     """

#     ensure_browser_started()

#     browser.screenshot(path)

#     return f"Screenshot saved to {path}"

# @mcp.tool()
# def open_test_site() -> dict:
#     """
#     Open the browser automation test website.
#     """

#     ensure_browser_started()

#     browser.goto(
#         "https://the-internet.herokuapp.com/"
#     )

#     return {
#         "url": browser.get_current_url(),
#         "title": browser.get_title(),
#     }


# if __name__ == "__main__":
#     mcp.run()










# from mcp.server.fastmcp import FastMCP

# from src.browser.playwright_driver import PlaywrightDriver
# from src.adapters.informatica import InformaticaAdapter


# mcp = FastMCP("web-ui-mcp")

# browser = PlaywrightDriver()
# browser.start()

# informatica = InformaticaAdapter(browser)


# @mcp.tool()
# def open_informatica() -> dict:
#     """
#     Open the Informatica web application
#     using Playwright.
#     """

#     return informatica.open()


# @mcp.tool()
# def inspect_informatica_page() -> dict:
#     """
#     Return basic information about the
#     currently displayed Informatica page.
#     """

#     return informatica.inspect_page()


# @mcp.tool()
# def take_screenshot(path: str) -> str:
#     """
#     Take a screenshot of the current browser page.
#     """

#     browser.screenshot(path)

#     return f"Screenshot saved to {path}"


# if __name__ == "__main__":
#     mcp.run()