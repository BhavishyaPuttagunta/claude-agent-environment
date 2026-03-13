import os
from dotenv import load_dotenv

load_dotenv()

# ── API ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = "claude-haiku-4-5-20251001"
MAX_TOKENS        = 1024

# ── SQL Server Database ───────────────────────────────────────────────────────
DB_SERVER   = os.getenv("DB_SERVER",   "192.168.2.98,1433")
DB_NAME     = os.getenv("DB_NAME",     "FTR_DEV")
DB_USER     = os.getenv("DB_USER",     "Princeton")
DB_PASSWORD = os.getenv("DB_PASSWORD", "P!T$sql")
DB_DRIVER   = os.getenv("DB_DRIVER",   "ODBC Driver 18 for SQL Server")


# ── Scraping ──────────────────────────────────────────────────────────────────
SCRAPE_TIMEOUT    = 30
MAX_CONTENT_CHARS = 150_000   # increased from 50k — covers full CFR parts
USER_AGENT        = "FDA-Regulatory-Intelligence-Bot/1.0 (Research Use)"

# ── eCFR API ──────────────────────────────────────────────────────────────────
ECFR_API_BASE = "https://www.ecfr.gov/api/versioner/v1"

# ── Change Detection ──────────────────────────────────────────────────────────
SIGNIFICANT_CHANGE_THRESHOLD = 0.05  # 5% of lines changed = SIGNIFICANT