"""
tools/tools.py
Regulatory Intelligence Agent — simplified URL scraping tools

Tools:
  deep_scrape     — scrape a URL + follow links, auto-saves to DB
  read_regulation — read saved content from DB
  list_regulations— list all saved pages
  compare_versions— diff between two saved versions
  check_changes   — view change audit log
"""

import hashlib
import difflib
import requests
from datetime import datetime
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

from config.config import SCRAPE_TIMEOUT, MAX_CONTENT_CHARS, USER_AGENT, SIGNIFICANT_CHANGE_THRESHOLD
from database.database import (
    init_db, save_regulation, get_latest, get_version,
    list_regulations, get_change_log, get_stats
)

init_db()

# ── Approved domains ──────────────────────────────────────────────────────────
ALLOWED_DOMAINS = {
    "fda.gov", "www.fda.gov",
    "federalregister.gov", "www.federalregister.gov",
    "hhs.gov", "www.hhs.gov",
    "cdc.gov", "www.cdc.gov",
    "nih.gov", "www.nih.gov", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov",
    "iso.org", "www.iso.org",
    "ich.org", "www.ich.org",
    "ecfr.gov", "www.ecfr.gov",
    "usda.gov", "www.usda.gov",
    "fsis.usda.gov", "www.fsis.usda.gov",
    "foodsafety.gov", "www.foodsafety.gov",
    "epa.gov", "www.epa.gov",
    "osha.gov", "www.osha.gov",
}

# ── Tool Definitions ──────────────────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "deep_scrape",
        "description": (
            "Scrape a URL and follow its internal links to gather complete content. "
            "AUTO-SAVES the result to the knowledge base — do NOT call save_regulation after this. "
            "Use this whenever a user provides a URL to monitor. "
            "If the page was scraped before, automatically detects and reports changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url":       {"type": "string",  "description": "Full URL to scrape e.g. https://www.fda.gov/food/..."},
                "label":     {"type": "string",  "description": "Short identifier to save as e.g. 'fda_food_labeling'"},
                "max_pages": {"type": "integer", "description": "Max pages to follow (default 10, max 25)"},
            },
            "required": ["url", "label"],
        },
    },
    {
        "name": "read_regulation",
        "description": "Read saved content from the knowledge base by its label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "regulation_id":  {"type": "string",  "description": "The label used when saving e.g. 'fda_food_labeling'"},
                "version_number": {"type": "integer", "description": "Specific version to read (omit for latest)"},
            },
            "required": ["regulation_id"],
        },
    },
    {
        "name": "list_regulations",
        "description": "List all saved pages in the knowledge base with version counts and last-scraped dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional keyword filter"},
            },
        },
    },
    {
        "name": "compare_versions",
        "description": "Compare two saved versions of a page. Shows exactly what lines were added or removed. Run this when deep_scrape reports CHANGED.",
        "input_schema": {
            "type": "object",
            "properties": {
                "regulation_id": {"type": "string",  "description": "Label of the saved page"},
                "version_a":     {"type": "integer", "description": "Older version number"},
                "version_b":     {"type": "integer", "description": "Newer version number (omit for latest)"},
            },
            "required": ["regulation_id", "version_a"],
        },
    },
    {
        "name": "check_changes",
        "description": "View the change audit log — every scrape event showing NEW / CHANGED / UNCHANGED.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter":       {"type": "string",  "description": "Optional label filter"},
                "limit":        {"type": "integer", "description": "Max entries (default 20)"},
                "changed_only": {"type": "boolean", "description": "Only show CHANGED entries"},
            },
        },
    },
]


# ── Tool Router ───────────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    try:
        match name:
            case "deep_scrape":
                return _deep_scrape(
                    inputs["url"],
                    inputs["label"],
                    inputs.get("max_pages", 10),
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


# ── deep_scrape ───────────────────────────────────────────────────────────────
def _deep_scrape(url: str, label: str, max_pages: int = 10) -> str:
    # Domain check
    domain = urlparse(url).netloc.lower().split(":")[0]
    if domain not in ALLOWED_DOMAINS:
        return (
            f"❌ BLOCKED — '{domain}' is not an approved source.\n"
            f"Allowed: {', '.join(sorted(ALLOWED_DOMAINS))}"
        )

    max_pages = min(max_pages or 10, 25)
    MAX_DEPTH = 2

    SKIP_PATTERNS = [
        "/login", "/logout", "/signin", "/search", "/cart", "/account",
        "/careers", "/jobs", "/press", "/sitemap", "/privacy", "/disclaimer",
        "/foia", "/feedback", "/share", "/print", "javascript:", "mailto:", "tel:",
    ]
    CONTENT_PATTERNS = [
        "/guidance", "/regulation", "/rule", "/cfr", "/part-", "/section-",
        "/chapter", "/subpart", "/document", "/docket", "/notice",
        "/compliance", "/enforcement", "/advisory", "/draft", "/final",
        "/food", "/drug", "/device", "/safety", "/labeling", "/inspection",
        "/ucm", "/recall",
    ]

    headers  = {"User-Agent": USER_AGENT}
    visited  = set()
    queue    = [(url, 0)]
    pages    = []
    root_domain = urlparse(url).netloc.lower()
    total_chars = 0
    BUDGET = MAX_CONTENT_CHARS

    def _extract_links(page_url, html):
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            if any(p in href.lower() for p in SKIP_PATTERNS):
                continue
            full = urljoin(page_url, href).split("#")[0].split("?")[0]
            parsed = urlparse(full)
            if parsed.netloc.lower() != root_domain:
                continue
            path = parsed.path.lower()
            anchor = a.get_text(strip=True).lower()
            looks_like_content = any(p in path for p in CONTENT_PATTERNS)
            anchor_looks_like_content = any(
                w in anchor for w in
                ["section", "part", "§", "cfr", "guidance", "regulation",
                 "requirement", "compliance", "chapter", "rule", "notice"]
            )
            if looks_like_content or anchor_looks_like_content:
                links.append(full)
        return links

    while queue and len(pages) < max_pages:
        current_url, depth = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        depth_str = "  " * depth + f"[d{depth}]"
        print(f"  🕷️  {depth_str} [{len(pages)+1}/{max_pages}]: {current_url[:70]}")

        try:
            resp = requests.get(current_url, headers=headers, timeout=SCRAPE_TIMEOUT)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"    ⚠️  Failed: {e}")
            continue

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        main = (
            soup.find("main") or soup.find("article")
            or soup.find("div", {"id": "main-content"})
            or soup.find("div", {"class": "content"})
            or soup.body or soup
        )
        text = "\n".join(
            line for line in main.get_text(separator="\n", strip=True).splitlines()
            if line.strip()
        )

        if len(text) > 100:
            page_cap = BUDGET // max(max_pages, 1)
            if len(text) > page_cap:
                text = text[:page_cap] + f"\n[... truncated at {page_cap:,} chars ...]"
            pages.append((current_url, depth, text))
            total_chars += len(text)

        if depth < MAX_DEPTH and len(pages) < max_pages:
            new_links = _extract_links(current_url, html)
            added = 0
            for lnk in new_links:
                if lnk not in visited:
                    queue.append((lnk, depth + 1))
                    added += 1
            if added:
                print(f"    ↳  {added} links queued at depth {depth+1}")

        if total_chars >= BUDGET:
            print(f"  ⚠️  Budget reached. Stopping.")
            break

    if not pages:
        return (
            f"❌ No content retrieved from {url}\n"
            f"The page may require JavaScript or login."
        )

    # Build full content
    content_parts = [
        f"SOURCE: {url}",
        f"LABEL: {label}",
        f"SCRAPED: {datetime.now().isoformat()}",
        f"PAGES: {len(pages)}",
        f"MAX DEPTH: {max(d for _,d,_ in pages)}",
        f"TOTAL CHARS: {total_chars:,}",
        "=" * 60,
    ]
    for i, (page_url, page_depth, page_text) in enumerate(pages, 1):
        content_parts.append(f"\n--- PAGE {i}/{len(pages)} (depth {page_depth}): {page_url} ---\n")
        content_parts.append(page_text)

    full_content = "\n".join(content_parts)

    # Auto-save to DB
    content_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()[:16]
    result = save_regulation(
        regulation_id=label,
        content=full_content,
        content_hash=content_hash,
        source_url=url,
        version_note=f"deep_scrape {len(pages)} pages",
    )

    status  = result["status"]
    version = result["version_number"]

    if status == "NEW":
        save_msg = f"✅ NEW — saved as version 1"
    elif status == "UNCHANGED":
        save_msg = f"✅ UNCHANGED — identical to version {version} (no changes since last scrape)"
    else:
        save_msg = f"⚠️  CHANGED — new version {version} saved. Run compare_versions to see what changed."

    crawled = "\n".join(f"  [d{d}] {u}" for u,d,_ in pages[:10])
    if len(pages) > 10:
        crawled += f"\n  ... and {len(pages)-10} more"

    return (
        f"{save_msg}\n"
        f"Label   : {label}\n"
        f"Pages   : {len(pages)}\n"
        f"Chars   : {total_chars:,}\n"
        f"Version : {version}\n"
        f"URLs scraped:\n{crawled}"
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
        hint = f"Available: {', '.join(regs)}" if regs else "Nothing saved yet — provide a URL to get started."
        return f"'{regulation_id}' not found ({label}). {hint}"

    header = (
        f"LABEL:   {regulation_id}\n"
        f"VERSION: {row['version_number']}\n"
        f"STATUS:  {row['status']}\n"
        f"SAVED:   {row['saved_at']}\n"
        f"SIZE:    {row['content_length']:,} chars\n"
        f"{'='*60}\n\n"
    )
    # Return header + truncated content so Claude's context doesn't overflow
    content = row["content"]
    if len(content) > 8000:
        content = content[:8000] + f"\n\n[... content truncated for display. Full content is {row['content_length']:,} chars ...]"
    return header + content


# ── list_regulations ──────────────────────────────────────────────────────────
def _list_regulations(filter_str: str = "") -> str:
    rows = list_regulations(filter_str)
    if not rows:
        msg = "Knowledge base is empty."
        if filter_str:
            msg += f" (filter: '{filter_str}')"
        msg += " Ask the user to provide a URL to scrape."
        return msg

    stats = get_stats()
    lines = [
        f"📚 Knowledge Base — {stats['regulations']} page(s) saved | "
        f"{stats['total_versions']} total versions | "
        f"{stats.get('db', stats.get('db_size_kb', 'SQL Server'))}\n"
    ]
    for r in rows:
        size_kb = (r.get("content_length") or 0) / 1024
        lines.append(f"  📋 {r['regulation_id']}")
        lines.append(f"      Versions    : {r.get('total_versions', 0)}")
        lines.append(f"      Latest      : v{r.get('latest_version_number','?')} [{size_kb:.1f} KB] status: {r.get('latest_status','?')}")
        lines.append(f"      Last scraped: {r.get('last_fetched','?')}")
        lines.append(f"      Source      : {r.get('source_url','?')}\n")
    return "\n".join(lines)


# ── compare_versions ──────────────────────────────────────────────────────────
def _compare_versions(regulation_id: str, version_a: int, version_b: int = None) -> str:
    row_a = get_version(regulation_id, version_a)
    if not row_a:
        return f"Version {version_a} of '{regulation_id}' not found."

    if version_b is None:
        row_b = get_latest(regulation_id)
        version_b = row_b["version_number"] if row_b else None
    else:
        row_b = get_version(regulation_id, version_b)

    if not row_b:
        return f"Version {version_b} of '{regulation_id}' not found."

    if version_a == version_b:
        return "✅ Same version — nothing to compare."

    lines_a = row_a["content"].splitlines(keepends=True)
    lines_b = row_b["content"].splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=f"v{version_a} ({row_a['saved_at'][:10]})",
        tofile=f"v{version_b} ({row_b['saved_at'][:10]})",
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
        f"Lines added   : +{added}\n"
        f"Lines removed : -{removed}\n"
        f"Change level  : {level} ({pct:.1f}% of document)\n"
        f"{'='*60}\n\n"
    )
    diff_text = "".join(diff[:300])
    if len(diff) > 300:
        diff_text += f"\n[... {len(diff)-300} more diff lines not shown ...]"

    return header + diff_text


# ── check_changes ─────────────────────────────────────────────────────────────
def _check_changes(filter_str: str = "", limit: int = 20, changed_only: bool = False) -> str:
    entries = get_change_log(filter_str, limit, changed_only)
    if not entries:
        return "No change log entries found" + (f" for '{filter_str}'." if filter_str else ".")

    icons = {"NEW": "🆕", "CHANGED": "⚠️ ", "UNCHANGED": "✅"}
    lines = [f"📋 Change Log — {len(entries)} entries\n"]
    for e in entries:
        icon = icons.get(e["status"], "❓")
        lines.append(
            f"  {icon} [{e['logged_at'][:19]}]  {e['regulation_id']}\n"
            f"      Status: {e['status']}  |  Version: {e.get('version_number','?')}  |  Hash: {e['content_hash']}\n"
            f"      Note  : {e.get('version_note') or '—'}"
        )
    return "\n".join(lines)