# Vendored agent harness

The agent loop, message models and event types come from
[huggingface/tau](https://github.com/huggingface/tau), tag `v0.3.9`
(commit `420ae60089c1adb30d415e4dde99f7323d3f4afb`), MIT licensed —
see `LICENSE.upstream`.

Upstream's `session/` package is not vendored: session state lives in Frappe
doctypes. Everything else is copied as-is, with `tau_agent` imports rewritten
to `frappe_agents.harness`. The package imports pydantic and the standard
library only — never frappe.

Patch policy: this is a pin, not a fork we track. Local edits are marked
`# frappe_agents patch:` so a deliberate cherry-pick from a later tag can find
them. Do not refactor vendored files for style; `ruff.toml` here keeps upstream
formatting.

`tests/` holds upstream's harness tests, import-rewritten. They run on pytest
with pydantic only, which is what keeps this package self-contained. Files are
named `*_test.py` so Frappe's own test discovery skips them.
