"""
test_scraping.py — Phase 2 scraping tests
Usage: python test_scraping.py
"""
from tools.tools import execute_tool

print("\n🔧 Phase 2 — Scraping Tests\n" + "="*50)
"""

# Test 2.1 — Single allowed domain
print("\n[2.1] Scraping allowed FDA domain (3 pages)...")
result = execute_tool("deep_scrape", {
    "url": "https://www.fda.gov/food/food-labeling-nutrition",
    "label": "test_fda_labeling",
    "max_pages": 3
})
print(result)

# Test 2.2 — Blocked domain
print("\n[2.2] Testing blocked domain (expect BLOCKED)...")
result = execute_tool("deep_scrape", {
    "url": "https://www.google.com",
    "label": "test_blocked"
})
print(result)
"""
"""
# Test 2.3 — Change detection (scrape same URL twice)
print("\n[2.3] Change detection test...")
result1 = execute_tool("deep_scrape", {
    "url": "https://www.fda.gov/food/food-labeling-nutrition",
    "label": "test_change",
    "max_pages": 2
})
print("First scrape:", result1[:120])

result2 = execute_tool("deep_scrape", {
    "url": "https://www.fda.gov/food/food-labeling-nutrition",
    "label": "test_change",
    "max_pages": 2
})
print("Second scrape:", result2[:120])


# Test 2.4 — List all saved pages
print("\n[2.4] Listing all saved pages...")
result = execute_tool("list_regulations", {})
print(result)
"""
# Test 2.5 — Read saved content
print("\n[2.5] Reading saved content...")
result = execute_tool("read_regulation", {"regulation_id": "test_fda_labeling"})
print(result[:500])

print("\n✅ Phase 2 scraping tests complete!\n")
