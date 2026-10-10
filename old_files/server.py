from mcp.server.fastmcp import FastMCP

from src.browser.playwright_driver import PlaywrightDriver
from src.adapters.informatica import InformaticaAdapter


mcp = FastMCP("web-ui-mcp")

browser = PlaywrightDriver()
informatica = InformaticaAdapter(browser)


async def ensure_browser_started():
    if browser.page is None:
        await browser.start()


@mcp.tool()
async def click_element(selector: str) -> dict:
    await ensure_browser_started()

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