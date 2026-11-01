"""
config.py
─────────
Centralized configuration loaded from .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── SSO Credentials ───────────────────────────────────────────────────
SSO_USERNAME = os.getenv("SSO_USERNAME", "")
SSO_PASSWORD = os.getenv("SSO_PASSWORD", "")

# ── URLs ──────────────────────────────────────────────────────────────
SSO_LOGIN_URL = (
    "https://sso.ui.ac.id/cas/login"
    "?service=https%3a%2f%2fremote-lib.ui.ac.id%2flogin"
)
REMOTE_LIB_MENU_URL = "https://remote-lib.ui.ac.id/menu"
IEEE_PROXY_URL = (
    "https://remote-lib.ui.ac.id/login"
    "?url=https://ieeexplore.ieee.org/Xplore/home.jsp"
)
IEEE_DIRECT_URL = "https://ieeexplore.ieee.org/Xplore/home.jsp"

# ── Mode ─────────────────────────────────────────────────────────────
# "proxy"  = login SSO → remote-lib → IEEE (institutional access)
# "direct" = go directly to IEEE Xplore (no proxy, limited PDF access)
MODE = os.getenv("MODE", "proxy")

# ── Search & Filter ───────────────────────────────────────────────────
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "social network analysis")
MAX_PAGES = int(os.getenv("MAX_PAGES", "10"))
# "3" = last 3 years, "5" = last 5 years, "0" / "off" / "all" = disabled
YEAR_FILTER = os.getenv("YEAR_FILTER", "0")

# ── Browser ───────────────────────────────────────────────────────────
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "100"))  # ms between actions
DEFAULT_TIMEOUT = 60_000  # 60 seconds

