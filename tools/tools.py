"""
Tool definitions and execution for the FDA Agent.
Tools: scrape_url, save_file, read_file, list_files, compare_files
"""

import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from config import KB_DIR

# ── Tool Schemas (sent to Claude) ────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "scrape_url",
        "description": (
            "Scrape text content from an FDA or eCFR URL. Use for fda.gov, ecfr.gov, "
            "federalregister.gov. Returns cleaned page text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to scrape"},
                "label": {"type": "string", "description": "Short label for this doc e.g. '21CFR820'"},
            },
            "required": ["url", "label"],
        },
    },
    {
        "name": "save_file",
        "description": (
            "Save content to the knowledge base. Automatically versions files with timestamps "
            "so older versions are preserved for auditing. Returns the saved filename."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Base filename e.g. '21CFR820'"},
                "content": {"type": "string", "description": "Text content to save"},
                "version_note": {"type": "string", "description": "Short note about this version e.g. 'scraped 2025-03'"},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the knowledge base by filename. Use list_files first if unsure of the name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename to read (with or without path)"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "list_files",
        "description": "List all files in the knowledge base, grouped by regulation ID with version history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional filter string to narrow results"},
            },
        },
    },
    {
        "name": "compare_files",
        "description": "Compare two versions of a regulation file and return a summary of differences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_a": {"type": "string", "description": "First filename (older version)"},
                "file_b": {"type": "string", "description": "Second filename (newer version)"},
            },
            "required": ["file_a", "file_b"],
        },
    },
]


# ── Tool Execution ────────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "scrape_url":
            return _scrape_url(inputs["url"], inputs["label"])
        elif name == "save_file":
            return _save_file(inputs["filename"], inputs["content"], inputs.get("version_note", ""))
        elif name == "read_file":
            return _read_file(inputs["filename"])
        elif name == "list_files":
            return _list_files(inputs.get("filter", ""))
        elif name == "compare_files":
            return _compare_files(inputs["file_a"], inputs["file_b"])
        else:
            return f"ERROR: Unknown tool '{name}'"
    except Exception as e:
        return f"ERROR executing {name}: {str(e)}"


# ── Individual Tool Functions ─────────────────────────────────────────────────
def _scrape_url(url: str, label: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (FDA Regulatory Research Bot)"}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Remove nav/footer/script noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to get main content area
    main = soup.find("main") or soup.find("article") or soup.find("div", class_="content") or soup
    text = main.get_text(separator="\n", strip=True)

    # Trim to reasonable size
    text = "\n".join(line for line in text.splitlines() if line.strip())
    if len(text) > 15000:
        text = text[:15000] + "\n\n[... content truncated ...]"

    metadata = f"SOURCE: {url}\nLABEL: {label}\nSCRAPED: {datetime.now().isoformat()}\n{'='*60}\n\n"
    return metadata + text


def _save_file(filename: str, content: str, version_note: str = "") -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = filename.replace(" ", "_").replace("/", "-")
    versioned_name = f"{safe_name}__{timestamp}.txt"
    filepath = os.path.join(KB_DIR, versioned_name)

    # Also save/overwrite the "latest" pointer
    latest_path = os.path.join(KB_DIR, f"{safe_name}__LATEST.txt")

    header = f"VERSION NOTE: {version_note}\nSAVED: {datetime.now().isoformat()}\n{'='*60}\n\n"
    full_content = header + content

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_content)
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return f"Saved: {versioned_name} (also updated LATEST pointer)"


def _read_file(filename: str) -> str:
    # Try exact path first, then KB_DIR
    candidates = [
        filename,
        os.path.join(KB_DIR, filename),
        os.path.join(KB_DIR, f"{filename}__LATEST.txt"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    return f"ERROR: File not found. Try list_files to see available files."


def _list_files(filter_str: str = "") -> str:
    if not os.path.exists(KB_DIR):
        return "Knowledge base is empty. Scrape some FDA documents first."

    files = sorted(os.listdir(KB_DIR))
    if filter_str:
        files = [f for f in files if filter_str.lower() in f.lower()]

    if not files:
        return "No files found matching filter."

    # Group by base regulation ID
    groups = {}
    for f in files:
        base = f.split("__")[0]
        groups.setdefault(base, []).append(f)

    lines = [f"📚 Knowledge Base ({len(files)} files)\n"]
    for base, versions in sorted(groups.items()):
        lines.append(f"\n  📋 {base}  ({len(versions)} version(s))")
        for v in sorted(versions):
            size = os.path.getsize(os.path.join(KB_DIR, v))
            lines.append(f"      • {v}  [{size/1024:.1f} KB]")

    return "\n".join(lines)


def _compare_files(file_a: str, file_b: str) -> str:
    content_a = _read_file(file_a)
    content_b = _read_file(file_b)

    if content_a.startswith("ERROR") or content_b.startswith("ERROR"):
        return f"Could not compare:\nA: {content_a}\nB: {content_b}"

    lines_a = set(content_a.splitlines())
    lines_b = set(content_b.splitlines())

    added = lines_b - lines_a
    removed = lines_a - lines_b

    result = f"COMPARISON: {file_a}  vs  {file_b}\n{'='*60}\n"
    result += f"Lines only in A (possibly removed): {len(removed)}\n"
    result += f"Lines only in B (possibly added):   {len(added)}\n\n"

    if added:
        result += "SAMPLE NEW CONTENT (first 20 lines):\n"
        result += "\n".join(list(added)[:20]) + "\n\n"
    if removed:
        result += "SAMPLE REMOVED CONTENT (first 20 lines):\n"
        result += "\n".join(list(removed)[:20])

    return result