"""multi_namespace.py — Demonstrate namespace isolation.

Two project namespaces 'project_alpha' and 'project_beta' each hold
distinct content. Searches respect the namespace boundary by default.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.environ.get("WAYPALACE_HOME", os.path.expanduser("~/.waypalace")) + "/waypalace")

import memory_mine  # noqa: E402
import memory_search  # noqa: E402


def _write_temp(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f.write(content)
    f.close()
    return f.name


# Project alpha: a frontend project's deployment notes
alpha_file = _write_temp(
    "# project_alpha\n\nFrontend deploys to your-app.example.com via Cloudflare Pages.\n"
)
memory_mine.mine_file(alpha_file, wing="project_alpha", verbose=False)

# Project beta: a backend service
beta_file = _write_temp(
    "# project_beta\n\nBackend API runs on Cloud Run, requires NEXTAUTH_SECRET env var.\n"
)
memory_mine.mine_file(beta_file, wing="project_beta", verbose=False)

# Search alpha — should only see alpha + global, not beta
result = memory_search.search_isolated(
    query="deployment",
    current_wing="project_alpha",
    n_results=5,
    threshold=0.3,
    detail_level="summary",
)
print("Searching from project_alpha:")
for r in result["results"]:
    print(f"  [{r['wing']}] {r.get('source_name')}: {r.get('summary', '')[:80]}")

# Cross-namespace search (use search_all when explicitly needed)
result_all = memory_search.search_all(
    query="deployment",
    n_results=10,
    threshold=0.3,
    detail_level="summary",
)
print(f"\nCross-namespace results ({result_all['total_returned']} total):")
for wing, items in result_all["results_by_wing"].items():
    print(f"  --- {wing} ---")
    for r in items:
        print(f"    {r.get('source_name')}: {r.get('summary', '')[:80]}")

# Cleanup
os.unlink(alpha_file)
os.unlink(beta_file)
