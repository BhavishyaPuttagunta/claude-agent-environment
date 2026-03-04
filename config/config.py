import os
from dotenv import load_dotenv

load_dotenv()

# ── API ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL             = "claude-opus-4-5"
MAX_TOKENS        = 4096

# ── Storage ───────────────────────────────────────────────────────────────────
# SQLite database — single file, versioned, queryable
# Replaces the old knowledge_base/ flat .txt files
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_base, "fda_knowledge.db")

# ── Scraping ──────────────────────────────────────────────────────────────────
SCRAPE_TIMEOUT    = 30
MAX_CONTENT_CHARS = 150_000   # increased from 50k — covers full CFR parts
USER_AGENT        = "FDA-Regulatory-Intelligence-Bot/1.0 (Research Use)"

# ── eCFR API ──────────────────────────────────────────────────────────────────
ECFR_API_BASE = "https://www.ecfr.gov/api/versioner/v1"

# ── Change Detection ──────────────────────────────────────────────────────────
SIGNIFICANT_CHANGE_THRESHOLD = 0.05  # 5% of lines changed = SIGNIFICANT