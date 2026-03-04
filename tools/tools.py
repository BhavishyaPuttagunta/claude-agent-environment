"""
tools/tools.py
Tool definitions and execution for the FDA Regulatory Intelligence Agent.

Storage backend: SQLite (fda_knowledge.db) via database/database.py
  — Replaces flat .txt files in knowledge_base/
  — Three tables: documents | versions | change_log

Tools:
  fetch_ecfr          — eCFR XML API (full regulation text)
  fetch_ecfr_versions — official amendment history for a CFR part
  scrape_url          — HTML scraping for fda.gov / federalregister.gov only
  save_regulation     — versioned save with SHA-256 change detection → SQLite
  read_regulation     — read latest (or specific) version from SQLite
  list_regulations    — list all saved regulations with version counts
  compare_versions    — unified diff between any two saved versions
  check_changes       — query the change_log audit table
"""

import hashlib
import difflib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup

from config.config import (
    SCRAPE_TIMEOUT, MAX_CONTENT_CHARS, USER_AGENT,
    ECFR_API_BASE, SIGNIFICANT_CHANGE_THRESHOLD
)
from database.database import (
    init_db, save_regulation, get_latest, get_version,
    list_regulations, list_versions, get_change_log, get_stats
)

# Initialise DB on import — creates tables if they don't exist
init_db()


# ── Approved scraping domains ─────────────────────────────────────────────────
ALLOWED_DOMAINS = {
    "fda.gov", "www.fda.gov",
    "federalregister.gov", "www.federalregister.gov",
    "hhs.gov", "www.hhs.gov",
    "cdc.gov", "www.cdc.gov",
    "nih.gov", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
    "iso.org", "www.iso.org",
    "ich.org", "www.ich.org",
    # eCFR allowed for fallback HTML scraping when XML API fails
    # Note: fetch_ecfr() uses the API directly and bypasses this check
    "www.ecfr.gov", "ecfr.gov",
}


def _is_allowed(url: str) -> tuple[bool, str]:
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc.lower().split(":")[0]
        return domain in ALLOWED_DOMAINS, domain
    except Exception:
        return False, "unknown"


# ── Tool Schemas (sent to Claude) ─────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "fetch_ecfr",
        "description": (
            "Fetch the FULL TEXT of any CFR part from the official eCFR XML API. "
            "ALWAYS use this for 21 CFR content — returns complete regulation text, not a TOC. "
            "Works for: Part 820 (QSR/QMSR), Part 11 (Electronic Records), "
            "Part 210/211 (cGMP), Part 803 (MDR), Part 806 (Recalls), Part 814 (PMA)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "integer", "description": "CFR title (21 = Food & Drugs)"},
                "part":  {"type": "integer", "description": "CFR part number e.g. 820, 11, 210"},
            },
            "required": ["title", "part"],
        },
    },
    {
        "name": "fetch_ecfr_versions",
        "description": "Check the official amendment history for a CFR part — all dates it was formally changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "integer", "description": "CFR title number"},
                "part":  {"type": "integer", "description": "CFR part number"},
            },
            "required": ["title", "part"],
        },
    },
    {
        "name": "scrape_url",
        "description": (
            "Scrape a web page for FDA content. "
            "Use ONLY for fda.gov guidance docs, federalregister.gov notices, or non-CFR FDA pages. "
            "Do NOT use for CFR regulations — use fetch_ecfr instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url":   {"type": "string", "description": "Full URL to scrape"},
                "label": {"type": "string", "description": "Short identifier e.g. 'FDA_UDI_guidance'"},
            },
            "required": ["url", "label"],
        },
    },
    {
        "name": "save_regulation",
        "description": (
            "Save fetched content to the SQLite knowledge base with full versioning. "
            "CRITICAL: The 'content' parameter MUST be the COMPLETE TEXT returned by fetch_ecfr or scrape_url — "
            "copy the entire return value verbatim into the content field. Do NOT summarize or truncate it. "
            "Returns NEW / CHANGED / UNCHANGED based on SHA-256 hash comparison."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regulation_id": {"type": "string",  "description": "Identifier e.g. '21CFR820'"},
                "content":       {"type": "string",  "description": "The COMPLETE return value from fetch_ecfr or scrape_url — must be the full text, not a summary"},
                "source_url":    {"type": "string",  "description": "URL it was fetched from"},
                "version_note":  {"type": "string",  "description": "Brief note e.g. 'eCFR XML API 2026-02-25'"},
                "title":         {"type": "integer", "description": "CFR title number e.g. 21"},
                "part":          {"type": "integer", "description": "CFR part number e.g. 820"},
            },
            "required": ["regulation_id", "content"],
        },
    },
    {
        "name": "read_regulation",
        "description": (
            "Read a saved regulation from the knowledge base. "
            "Returns the latest version by default, or a specific version number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regulation_id":  {"type": "string",  "description": "e.g. '21CFR820'"},
                "version_number": {"type": "integer", "description": "Specific version to read (omit for latest)"},
            },
            "required": ["regulation_id"],
        },
    },
    {
        "name": "list_regulations",
        "description": "List all regulations in the knowledge base with version counts, sizes, and last-fetched dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional filter e.g. 'CFR820' or 'FDA'"},
            },
        },
    },
    {
        "name": "compare_versions",
        "description": (
            "Compare two saved versions of a regulation using a proper unified diff. "
            "Shows exactly which lines were added (+) or removed (-) with context. "
            "Always run this when save_regulation returns CHANGED."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regulation_id": {"type": "string",  "description": "e.g. '21CFR820'"},
                "version_a":     {"type": "integer", "description": "Older version number"},
                "version_b":     {"type": "integer", "description": "Newer version number (omit for latest)"},
            },
            "required": ["regulation_id", "version_a"],
        },
    },
    {
        "name": "check_changes",
        "description": (
            "Query the change audit log — every save event with NEW/CHANGED/UNCHANGED status, "
            "timestamps, and hashes. Use to monitor what has changed over time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter":       {"type": "string",  "description": "Optional regulation ID filter"},
                "limit":        {"type": "integer", "description": "Max entries to return (default 20)"},
                "changed_only": {"type": "boolean", "description": "If true, only show CHANGED entries"},
            },
        },
    },
]


# ── Tool Router ───────────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    try:
        match name:
            case "fetch_ecfr":
                return _fetch_ecfr(inputs["title"], inputs["part"])
            case "fetch_ecfr_versions":
                return _fetch_ecfr_versions(inputs["title"], inputs["part"])
            case "scrape_url":
                return _scrape_url(inputs["url"], inputs["label"])
            case "save_regulation":
                return _save_regulation(
                    inputs["regulation_id"],
                    inputs["content"],
                    inputs.get("source_url", ""),
                    inputs.get("version_note", ""),
                    inputs.get("title"),
                    inputs.get("part"),
                )
            case "read_regulation":
                return _read_regulation(inputs["regulation_id"], inputs.get("version_number"))
            case "list_regulations":
                return _list_regulations(inputs.get("filter", ""))
            case "compare_versions":
                return _compare_versions(
                    inputs["regulation_id"],
                    inputs["version_a"],
                    inputs.get("version_b"),
                )
            case "check_changes":
                return _check_changes(
                    inputs.get("filter", ""),
                    inputs.get("limit", 20),
                    inputs.get("changed_only", False),
                )
            case _:
                return f"ERROR: Unknown tool '{name}'"
    except Exception as e:
        return f"ERROR in {name}: {type(e).__name__}: {str(e)}"


# ── fetch_ecfr ────────────────────────────────────────────────────────────────
def _fetch_ecfr(title: int, part: int) -> str:
    """
    Fetch full regulation text from the eCFR XML API.

    Tries multiple date fallbacks because:
    - Some parts have gaps (e.g. Part 820 was restructured Feb 2026)
    - The API returns 404 if the part had no content on that exact date
    Strategy: try today, then walk back week by week up to 1 year
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml, */*"}
    from datetime import timedelta

    # Build a list of dates to try: today, then weekly fallbacks up to 52 weeks
    base_date = datetime.now()
    dates_to_try = [base_date - timedelta(weeks=w) for w in range(0, 53, 1)]

    last_error = None
    for attempt_date in dates_to_try:
        date_str = attempt_date.strftime("%Y-%m-%d")
        url = f"{ECFR_API_BASE}/full/{date_str}/title-{title}.xml"
        try:
            response = requests.get(
                url, headers=headers, params={"part": part},
                timeout=SCRAPE_TIMEOUT
            )
            if response.status_code == 404:
                last_error = f"404 on {date_str}"
                continue
            response.raise_for_status()

            # Parse XML → clean text
            try:
                root = ET.fromstring(response.content)
                text = _xml_to_text(root).strip()
            except ET.ParseError:
                text = response.text.strip()

            # Validate we got real content, not just a shell
            if len(text) < 200:
                last_error = f"Only {len(text)} chars on {date_str} — skipping"
                continue

            if len(text) > MAX_CONTENT_CHARS:
                text = text[:MAX_CONTENT_CHARS] + f"\n\n[... truncated at {MAX_CONTENT_CHARS:,} chars ...]"

            note = f" (fetched from archive date {date_str})" if date_str != base_date.strftime("%Y-%m-%d") else ""
            return (
                f"SOURCE: eCFR Official XML API — Title {title}, Part {part}{note}\n"
                f"URL: {url}?part={part}\n"
                f"FETCHED: {datetime.now().isoformat()}\n"
                f"ARCHIVE DATE USED: {date_str}\n"
                f"{'='*60}\n\n"
                f"{text}"
            )
        except requests.HTTPError as e:
            last_error = str(e)
            continue
        except Exception as e:
            return f"ERROR in fetch_ecfr: {type(e).__name__}: {e}"

    # ── All API dates failed — fall back to scraping the eCFR HTML page ─────
    print(f"  ⚠️  XML API exhausted ({len(dates_to_try)} dates tried). Falling back to HTML scrape...")
    fallback_url = f"https://www.ecfr.gov/current/title-{title}/part-{part}"
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(fallback_url, headers=headers, timeout=SCRAPE_TIMEOUT)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()

        main = (
            soup.find("main") or soup.find("article")
            or soup.find("div", {"id": "main-content"})
            or soup.body or soup
        )
        text = "\n".join(
            line for line in main.get_text(separator="\n", strip=True).splitlines()
            if line.strip()
        )

        if len(text) < 200:
            raise ValueError(f"HTML fallback returned only {len(text)} chars — likely JS-gated")

        if len(text) > MAX_CONTENT_CHARS:
            text = text[:MAX_CONTENT_CHARS] + f"\n\n[... truncated at {MAX_CONTENT_CHARS:,} chars ...]"

        return (
            f"SOURCE: eCFR HTML fallback (XML API unavailable) — Title {title}, Part {part}\n"
            f"URL: {fallback_url}\n"
            f"FETCHED: {datetime.now().isoformat()}\n"
            f"NOTE: XML API returned 404 for all dates tried. Using HTML scrape — content may be incomplete.\n"
            f"{'='*60}\n\n"
            f"{text}"
        )
    except Exception as fallback_err:
        return (
            f"ERROR: Could not fetch Title {title} Part {part} via XML API or HTML fallback.\n"
            f"XML API: tried {len(dates_to_try)} dates, last error: {last_error}\n"
            f"HTML fallback ({fallback_url}): {fallback_err}\n\n"
            f"The eCFR site uses JavaScript rendering — HTML scraping may return nav text only.\n"
            f"Suggested alternatives:\n"
            f"  1. Use scrape_url on https://www.fda.gov to find guidance on this topic\n"
            f"  2. Use scrape_url on https://www.federalregister.gov to find the final rule\n"
            f"  3. Paste the regulation text directly and use save_regulation to store it"
        )


def _xml_to_text(root: ET.Element, depth: int = 0) -> str:
    lines = []
    indent = "  " * depth
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag in ("HEAD", "SUBJECT"):
        text = (root.text or "").strip()
        if text:
            if depth <= 2:
                lines.append(f"\n{'='*60}\n{text}\n{'='*60}")
            elif depth <= 4:
                lines.append(f"\n{indent}{'─'*40}\n{indent}{text}")
            else:
                lines.append(f"\n{indent}▸ {text}")

    elif tag == "SECTNO":
        text = (root.text or "").strip()
        if text:
            lines.append(f"\n{indent}{text}")

    elif tag in ("P", "FP", "PSPACE", "FP-1", "FP-2"):
        parts = []
        if root.text:
            parts.append(root.text.strip())
        for child in root:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag in ("E", "I", "B", "SU", "SUB", "SUP"):
                if child.text:
                    parts.append(child.text.strip())
                if child.tail:
                    parts.append(child.tail.strip())
        text = " ".join(p for p in parts if p)
        if text:
            lines.append(f"{indent}  {text}")

    if tag not in ("P", "FP", "PSPACE", "FP-1", "FP-2", "HEAD", "SUBJECT", "SECTNO"):
        for child in root:
            child_text = _xml_to_text(child, depth + 1)
            if child_text:
                lines.append(child_text)

    return "\n".join(l for l in lines if l)


# ── fetch_ecfr_versions ───────────────────────────────────────────────────────
def _fetch_ecfr_versions(title: int, part: int) -> str:
    url = f"{ECFR_API_BASE}/versions/title-{title}/part-{part}.json"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=SCRAPE_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    versions = data.get("content_versions", [])
    if not versions:
        return f"No version history found for Title {title} Part {part}."

    lines = [f"📅 Amendment History — Title {title}, Part {part} ({len(versions)} entries)\n"]
    for v in versions[:30]:
        lines.append(f"  • {v.get('date','?')}  {v.get('identifier','')}  {v.get('name','')}".rstrip())
    return "\n".join(lines)


# ── scrape_url ────────────────────────────────────────────────────────────────
def _scrape_url(url: str, label: str) -> str:
    allowed, domain = _is_allowed(url)
    if not allowed:
        return (
            f"❌ BLOCKED — '{domain}' is not an approved FDA/regulatory source.\n"
            f"Allowed domains: {', '.join(sorted(ALLOWED_DOMAINS))}\n"
            f"To add a domain, update ALLOWED_DOMAINS in tools/tools.py."
        )

    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=SCRAPE_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    main = (
        soup.find("main") or soup.find("article")
        or soup.find("div", {"id": "main-content"})
        or soup.body or soup
    )
    text = "\n".join(
        line for line in main.get_text(separator="\n", strip=True).splitlines() if line.strip()
    )

    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS] + f"\n\n[... truncated at {MAX_CONTENT_CHARS:,} chars ...]"

    return (
        f"SOURCE: {url}\nLABEL: {label}\nSCRAPED: {datetime.now().isoformat()}\n{'='*60}\n\n{text}"
    )


# ── save_regulation ───────────────────────────────────────────────────────────
def _save_regulation(
    regulation_id: str,
    content: str,
    source_url: str = "",
    version_note: str = "",
    title: int = None,
    part: int = None,
) -> str:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    result = save_regulation(
        regulation_id=regulation_id,
        content=content,
        content_hash=content_hash,
        source_url=source_url,
        version_note=version_note,
        title=title,
        part=part,
    )

    status  = result["status"]
    version = result["version_number"]

    if status == "UNCHANGED":
        return (
            f"✅ UNCHANGED — identical to version {version} (hash: {content_hash}).\n"
            f"   Regulation has not changed since last fetch. No new version stored."
        )
    elif status == "NEW":
        return (
            f"✅ NEW — '{regulation_id}' saved as version 1 (hash: {content_hash}).\n"
            f"   Stored in SQLite: fda_knowledge.db → versions table."
        )
    else:
        return (
            f"⚠️  CHANGED — new version {version} saved for '{regulation_id}'.\n"
            f"   Previous hash: {result['prev_hash']}  →  New hash: {content_hash}\n"
            f"   Stored in SQLite: fda_knowledge.db → versions table.\n"
            f"   → Run compare_versions to see exactly what changed."
        )


# ── read_regulation ───────────────────────────────────────────────────────────
def _read_regulation(regulation_id: str, version_number: int = None) -> str:
    if version_number:
        row = get_version(regulation_id, version_number)
        label = f"version {version_number}"
    else:
        row = get_latest(regulation_id)
        label = "latest version"

    if not row:
        regs = [r["regulation_id"] for r in list_regulations()]
        hint = f"Available: {', '.join(regs)}" if regs else "No regulations saved yet."
        return f"ERROR: '{regulation_id}' not found ({label}). {hint}"

    header = (
        f"REGULATION: {regulation_id}\n"
        f"VERSION:    {row['version_number']}\n"
        f"STATUS:     {row['status']}\n"
        f"HASH:       {row['content_hash']}\n"
        f"SAVED:      {row['saved_at']}\n"
        f"SIZE:       {row['content_length']:,} chars\n"
        f"{'='*60}\n\n"
    )
    return header + row["content"]


# ── list_regulations ──────────────────────────────────────────────────────────
def _list_regulations(filter_str: str = "") -> str:
    rows = list_regulations(filter_str)
    if not rows:
        return "Knowledge base is empty." + (f" (filter: '{filter_str}')" if filter_str else "")

    stats = get_stats()
    lines = [
        f"📚 Knowledge Base — {stats['regulations']} regulation(s) | "
        f"{stats['total_versions']} total versions | "
        f"{stats['db_size_kb']} KB (fda_knowledge.db)\n"
    ]

    for r in rows:
        size_kb = (r.get("content_length") or 0) / 1024
        lines.append(
            f"\n  📋 {r['regulation_id']}"
            f"  (Title {r.get('title') or '?'}, Part {r.get('part') or '?'})"
        )
        lines.append(f"      Versions:     {r.get('total_versions', 0)}")
        lines.append(f"      Latest:       v{r.get('latest_version_number','?')}  [{size_kb:.1f} KB]  status: {r.get('latest_status','?')}")
        lines.append(f"      Last fetched: {r.get('last_fetched','?')}")
        lines.append(f"      Source:       {r.get('source_url') or 'eCFR API'}")

    return "\n".join(lines)


# ── compare_versions ──────────────────────────────────────────────────────────
def _compare_versions(regulation_id: str, version_a: int, version_b: int = None) -> str:
    row_a = get_version(regulation_id, version_a)
    if not row_a:
        return f"ERROR: Version {version_a} of '{regulation_id}' not found."

    if version_b is None:
        row_b = get_latest(regulation_id)
        version_b = row_b["version_number"] if row_b else None
    else:
        row_b = get_version(regulation_id, version_b)

    if not row_b:
        return f"ERROR: Version {version_b} of '{regulation_id}' not found."

    if version_a == version_b:
        return "✅ Same version — nothing to compare."

    lines_a = row_a["content"].splitlines(keepends=True)
    lines_b = row_b["content"].splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=f"{regulation_id} v{version_a} ({row_a['saved_at'][:10]})",
        tofile=f"{regulation_id} v{version_b} ({row_b['saved_at'][:10]})",
        n=3,
    ))

    if not diff:
        return "✅ Content is IDENTICAL between these two versions."

    added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    pct     = (added + removed) / max(len(lines_a), 1) * 100
    level   = "🔴 SIGNIFICANT" if pct >= SIGNIFICANT_CHANGE_THRESHOLD * 100 else "🟡 MINOR"

    header = (
        f"DIFF: {regulation_id}  v{version_a} → v{version_b}\n{'='*60}\n"
        f"Lines added:   +{added}\n"
        f"Lines removed: -{removed}\n"
        f"Change level:  {level} ({pct:.1f}% of document)\n"
        f"{'='*60}\n\n"
    )

    diff_text = "".join(diff[:400])
    if len(diff) > 400:
        diff_text += f"\n[... {len(diff)-400} more diff lines not shown ...]"

    return header + diff_text


# ── check_changes ─────────────────────────────────────────────────────────────
def _check_changes(filter_str: str = "", limit: int = 20, changed_only: bool = False) -> str:
    entries = get_change_log(filter_str, limit, changed_only)

    if not entries:
        return "No change log entries found" + (f" for '{filter_str}'." if filter_str else ".")

    icons = {"NEW": "🆕", "CHANGED": "⚠️ ", "UNCHANGED": "✅"}
    lines = [f"📋 Change Log — {len(entries)} entries (most recent first)\n"]
    for e in entries:
        icon = icons.get(e["status"], "❓")
        lines.append(
            f"  {icon} [{e['logged_at'][:19]}]  {e['regulation_id']}\n"
            f"      Status:  {e['status']}  |  Version: {e.get('version_number','?')}"
            f"  |  Hash: {e['content_hash']}\n"
            f"      Note:    {e.get('version_note') or '—'}"
        )

    return "\n".join(lines)