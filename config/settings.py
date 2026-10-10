


import os

from dotenv import load_dotenv

load_dotenv()


INFORMATICA_URL = os.getenv(
    "INFORMATICA_URL",
    ""
)

INFORMATICA_USERNAME = os.getenv(
    "INFORMATICA_USERNAME",
    ""
)

INFORMATICA_PASSWORD = os.getenv(
    "INFORMATICA_PASSWORD",
    ""
)

PLAYWRIGHT_HEADLESS = (
    os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
)

PLAYWRIGHT_TIMEOUT = int(
    os.getenv("PLAYWRIGHT_TIMEOUT", "30000")
)