---
name: version-branches
description: This repo ships TWO branches — main for Frappe v16, version-15 for Frappe v15. Use whenever you fix a bug, merge a PR, cut a release, touch pyproject.toml or install.py, or are asked whether a change needs porting. Read BEFORE writing the fix, not after.
---

# Two branches: main (v16) and version-15 (v15)

**If you fix something on `main`, it is not done until it is on `version-15` too.**

That sentence is the whole skill. Everything below is how.

| Branch | Frappe | Python | Releases as |
|---|---|---|---|
| `main` | v16 | 3.14 | `v0.X.Y` |
| `version-15` | v15 | 3.11 | `v0.X.Y-v15` |

This was a deliberate choice (Malik, 2026-08-16) over a single branch serving
both. The known cost is drift: the two copies grow apart unless every fix is
carried across. **Fighting that drift is your job in every session.**

## The rule

A change on `main` falls into exactly one of three buckets. Decide which
**before** you write it, and say which in the PR:

1. **Port it.** Bug fixes, security fixes, docs corrections, test fixes.
   Anything a v15 user would also suffer. This is the default.
2. **Do not port it.** A feature that depends on something v15 does not have.
   Say why in the commit or PR body so nobody re-litigates it later.
3. **Port it differently.** The bug exists on both but the fix cannot be
   identical, because v15 lacks the API. Write it twice, deliberately.

When unsure, port it. A needless port is cheap; a missed security fix is not.

## Porting a change

```bash
git checkout version-15
git cherry-pick <sha>            # or -n to inspect before committing
```

Then, before you commit:

```bash
python3.11 -m compileall -q -x '(tests|__pycache__)' frappe_agents
```

**That compile is not optional.** It is the cheapest possible check that you
have not dragged v16-only syntax onto the v15 branch, and it takes a second.
Silence means it passed.

If the cherry-pick conflicts, resolve it toward *what v15 can actually do*, not
toward what main says.

## What version-15 may not contain

### Syntax

`version-15` targets **Python 3.11**. Two things main uses freely are syntax
errors there:

| Do not write | Write instead |
|---|---|
| `type X = int \| str` (PEP 695) | `X: TypeAlias = int \| str` |
| `type X = ...` **that references itself** | `TypeAliasType("X", ...)` — see below |
| `except A, B:` (PEP 758) | `except (A, B):` |

**The recursive case is a trap, and it bites silently.** `frappe_agents/harness/
types.py` defines `JSONValue` in terms of itself. Rewriting that one as a plain
`TypeAlias` still *compiles* — and then pydantic expands it eagerly and recurses
until the interpreter dies, at import time. Use the lazy named alias instead:

```python
from typing_extensions import TypeAliasType

JSONValue = TypeAliasType("JSONValue", Union[JSONPrimitive, list["JSONValue"], dict[str, "JSONValue"]])
```

`typing_extensions` is a direct frappe dependency on both v15 and v16, so this
adds nothing to the bench. Use `Union[...]` rather than `|` there: the recursive
arm has to stay resolvable by name when pydantic builds the schema.

Compiling is not enough to catch this. If you touch the harness types, import
them under 3.11 with pydantic present and build a `TypeAdapter` before you push.

`ruff` on that branch is set to `target-version = "py311"` and will catch these,
but the compile above catches them faster.

**`ruff format` on main will undo your fix.** main targets py314, and its
formatter rewrites an inline `except (A, B):` into PEP 758 `except A, B:` — which
version-15 cannot parse. Do not fight it file by file: name the tuple once and
catch the name.

```python
SIGNATURE_ERRORS = (TypeError, ValueError)
...
except SIGNATURE_ERRORS:
```

A name is not a tuple literal, so neither branch's formatter touches it and the
same source works on both.

The vendored harness in `frappe_agents/harness/` is the usual offender —
upstream writes for modern Python, so **every re-vendor reintroduces PEP 695**.
Convert it again each time.

### Framework APIs missing in v15

| API | Status on version-15 |
|---|---|
| `Workspace Sidebar` doctype | **Does not exist.** Guard on `frappe.db.exists("DocType", "Workspace Sidebar")` before creating or renaming one. |
| `meta.get_masked_fields()` | **Does not exist.** Already guarded everywhere — do not add an unguarded call. |

Before using any framework API that feels new, check it against v15:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  https://raw.githubusercontent.com/frappe/frappe/version-15/frappe/<path>
```

404 means v15 does not have it, and your change needs bucket 2 or 3.

### The masking gap is a security difference, not a cosmetic one

v16 feeds `get_masked_fields()` into two protections: what gets `[redacted]` in
an audit row, and the extraction gate's master-value comparison. On v15 that
source is empty, so **a field an admin masked is not automatically protected**.

`docs/admin.md` says so on the v15 branch. If you change anything in that area,
keep that note true.

## Releasing

Two streams, released separately.

- `main` → normal `release` skill, normal version numbers.
- `version-15` → same flow. Its own `.releaserc` names `version-15` as the
  release branch and sets `tagFormat` to `v${version}-v15`, so the two streams
  never collide.

**The first v15 release needs a dry run.** No `v*-v15` tag exists yet, so
semantic-release has no previous release to count from and will pick a version
off the whole history. Dry-run it exactly as the `release` skill describes and
read the version it proposes before dispatching anything.

`.releaserc` is one of the files that legitimately differs between the branches.
Never resolve a merge conflict there by taking main's copy.

**Never release `version-15` with ports outstanding.** Run the drift check
first, below.

## Checking for drift

CI runs this nightly and opens an issue when the two diverge, but run it by hand
any time you are about to release or are asked "are we in sync?":

```bash
git fetch origin
git log --oneline origin/version-15..origin/main -- \
  frappe_agents/ pyproject.toml
```

Empty output means everything is carried across. Anything listed is a change on
`main` that has not been ported — triage each into one of the three buckets.

Docs and workflow files are excluded from that command on purpose: they diverge
legitimately.

## When someone asks "can we support v14 too?"

The answer on record is **no** — v14 is end-of-life under Frappe's
last-two-majors policy. Do not add a third branch without Malik saying so
explicitly.

Last verified: 2026-08-16 (branch created, CI green on both).
