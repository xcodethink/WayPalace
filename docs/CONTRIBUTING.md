# Contributing

Thank you for considering a contribution to WayPalace.

## Project status

**Alpha (v0.1.0)**. Expect breaking changes between minor versions. This is
not production-ready software with an SLA.

## Maintainer time

This is a side project. Issues and PRs are reviewed when the maintainer has
bandwidth, typically within 1-2 weeks. Urgent bug fixes get faster attention
if they include a clear reproduction.

## How to file an issue

Useful issues include:

- A precise reproduction (commands run, expected vs actual)
- Environment details (OS, Python version, hardware class)
- Relevant log output from `$WAYPALACE_DATA/logs/`
- Output of `mp-health`

Less useful: "doesn't work" / "memory layer is slow" without specifics.

## Pull requests

Welcome but please open an issue first to discuss the change. PRs that come
out of nowhere with significant architecture changes will likely be rejected
even if technically sound — design alignment matters.

### Smaller PRs we're happy to merge

- Bug fixes with regression tests
- Documentation improvements
- New language support for embedding (the bge-m3 dependency is opinionated)
- Linux systemd equivalents of the launchd plist templates
- Examples for new use cases

### Things to coordinate with the maintainer first

- New retrieval modes
- Changes to the wing lifecycle classifier
- New sanitization rules in `_governance/sanitize-rules.yaml` (this is the
  maintainer's private leak-prevention layer, not a contribution surface)
- Changes to the MCP server schema

## Running tests

```bash
cd opensource/WayPalace
python -m pytest tests/ -q
```

All tests must pass. We don't run CI yet — manual runs only.

## Code style

- Python 3.11+ syntax features are fine (`match`, generics, etc.)
- We use `ruff` for linting (`pip install waypalace[dev]`)
- Type hints encouraged but not required
- Docstrings: one-line summary + (optional) longer explanation
- Comments: explain why, not what

## Commit message format

Free-form is OK. Prefer:

```
<component>: <one-line summary>

<optional longer explanation>
<optional reference to issue # or ADR>
```

Example:

```
search: handle empty query in hybrid path

When --hybrid is set but query is whitespace-only, we were
returning a 500. Now we short-circuit to empty results.

Closes #42
```

## Security disclosures

If you find a security issue (private data leak, RCE, etc.), please **do not**
open a public issue. Email the maintainer privately (see the repo's
GitHub profile for contact).

## License

By contributing, you agree your contributions are licensed under the MIT
license (same as the project).

## Areas where help is especially welcome

1. **Linux systemd integration** — none of the launchd plists work on Linux,
   we need equivalents
2. **Non-Mac performance benchmarks** — we only have numbers from one
   machine class
3. **Internationalization** — the bge-m3 model handles Chinese well; we'd
   love to see benchmarks for Japanese, Korean, Arabic, etc.
4. **Alternative embedding backends** — the codebase assumes bge-m3 in places.
   A clean interface would let users swap in other models.
5. **Web UI** — a simple read-only dashboard showing wings + metrics would
   be welcome (FastAPI + Vue / React, anything goes)
