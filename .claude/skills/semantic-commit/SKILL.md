---
name: semantic-commit
description: Commit message format for this repo. Use every time you commit.
---

# Semantic commits

Title: `<type>: <what happened>` — imperative, ≤ 72 chars.
Types: `feat` `fix` `docs` `chore` `refactor` `test` `ci` `perf`.

Body: one bullet per thing done. Each bullet starts with a verb.

```
feat: add permission-checked document search tool

- add search_documents wrapping frappe.get_list
- filter results through the effective user's permissions
- log every call to Agent Tool Call
```

Rules:

- Title says what happened. Bullets say what was done. Nothing else.
- No plan text, no internal references, no issue-tracker prose.
- No Co-Authored-By trailers.
- Commit only what belongs in the open-source repo. `internal/` never.
- Stage files by name: `git add <file> <file>`. Never `git add -A`,
  `git add .`, or `git add --all` — you commit what you can list.
