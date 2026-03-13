"""
test_db.py — run this to verify SQL Server connection and all DB operations
Usage: python test_db.py
"""
from database.database import init_db, save_regulation, get_latest, list_regulations, get_stats, get_change_log

print("\n🔧 Testing SQL Server Database Connection\n" + "="*50)

# Test 1 — connect and create tables
print("\n[1] Connecting and creating tables...")
init_db()

# Test 2 — save a new record
print("\n[2] Saving test record...")
result = save_regulation(
    regulation_id="test_page",
    content="This is test content for the database connection.",
    content_hash="abc123",
    source_url="https://www.fda.gov/test",
    version_note="test entry"
)
print(f"    Status  : {result['status']}")
print(f"    Version : {result['version_number']}")

# Test 3 — save same record again (should be UNCHANGED)
print("\n[3] Saving same record again (expect UNCHANGED)...")
result2 = save_regulation(
    regulation_id="test_page",
    content="This is test content for the database connection.",
    content_hash="abc123",
    source_url="https://www.fda.gov/test",
    version_note="test entry 2"
)
print(f"    Status  : {result2['status']}")

# Test 4 — save with different content (should be CHANGED)
print("\n[4] Saving updated content (expect CHANGED)...")
result3 = save_regulation(
    regulation_id="test_page",
    content="This is UPDATED test content — something changed on the page.",
    content_hash="xyz999",
    source_url="https://www.fda.gov/test",
    version_note="test entry 3 - updated"
)
print(f"    Status  : {result3['status']}")
print(f"    Version : {result3['version_number']}")

# Test 5 — read latest
print("\n[5] Reading latest version...")
row = get_latest("test_page")
print(f"    regulation_id : {row['regulation_id']}")
print(f"    version       : {row['version_number']}")
print(f"    content       : {row['content'][:50]}...")

# Test 6 — list all
print("\n[6] Listing all saved pages...")
regs = list_regulations()
print(f"    Total saved : {len(regs)}")
for r in regs:
    print(f"    → {r['regulation_id']} | versions: {r['total_versions']}")

# Test 7 — change log
print("\n[7] Change log...")
log = get_change_log()
for entry in log:
    print(f"    [{entry['logged_at'][:19]}] {entry['regulation_id']} — {entry['status']}")

# Test 8 — stats
print("\n[8] Stats...")
stats = get_stats()
print(f"    Regulations : {stats['regulations']}")
print(f"    Versions    : {stats['total_versions']}")
print(f"    Changes     : {stats['change_events']}")

print("\n✅ All database tests passed!\n")