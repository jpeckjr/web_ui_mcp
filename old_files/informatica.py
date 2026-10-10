
from src.browser.playwright_driver import PlaywrightDriver
from config.settings import INFORMATICA_URL


class InformaticaAdapter:
    def __init__(self, browser: PlaywrightDriver):
        self.browser = browser

    async def open(self):
        await self.browser.goto(INFORMATICA_URL)

        return {
            "url": await self.browser.get_current_url(),
            "title": await self.browser.get_title(),
        }

    async def inspect_page(self):
        return {
            "url": await self.browser.get_current_url(),
            "title": await self.browser.get_title(),
        }












# from src.browser.playwright_driver import PlaywrightDriver
# from config.settings import INFORMATICA_URL


# class InformaticaAdapter:

#     def __init__(self, browser: PlaywrightDriver):
#         self.browser = browser

#     def open(self):

#         self.browser.goto(INFORMATICA_URL)

#         return {
#             "url": self.browser.get_current_url(),
#             "title": self.browser.get_title(),
#         }

#     def inspect_page(self):

#         return {
#             "url": self.browser.get_current_url(),
#             "title": self.browser.get_title(),
#         }