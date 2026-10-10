import pytest

from src.browser.playwright_driver import PlaywrightDriver


@pytest.mark.asyncio
async def test_interactive_site():
    browser = PlaywrightDriver()

    await browser.start()

    await browser.goto(
        "https://the-internet.herokuapp.com/"
    )

    title = await browser.get_title()

    assert "The Internet" in title

    await browser.screenshot(
        "screenshots/the_internet.png"
    )

    await browser.stop()


# from src.browser.playwright_driver import PlaywrightDriver


# def test_browser():

#     browser = PlaywrightDriver()

#     browser.start()

#     browser.goto("https://example.com")

#     title = browser.get_title()

#     assert title == "Example Domain"

#     browser.stop()


# def test_interactive_site():

#     browser = PlaywrightDriver()

#     browser.start()

#     browser.goto("https://the-internet.herokuapp.com/")

#     title = browser.get_title()

#     assert "The Internet" in title

#     browser.screenshot(
#         "screenshots/the_internet.png"
#     )

#     browser.stop()