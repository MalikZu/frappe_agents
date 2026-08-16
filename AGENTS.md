# frappe_agents

Open-source agent runtime for Frappe (v16). Agents are records, not deployments.
The permission model is the product.

## Rules

- **Frappe only.** Never import or depend on `erpnext`. ERP-specific behavior
  goes in optional integration modules, off by default.
- **One door to data.** Agent tools use the permission-checked ORM only:
  `frappe.get_list`, `frappe.get_doc` after `has_permission`. Never
  `frappe.db.sql`, `frappe.get_all`, or `ignore_permissions=True` in tool code.
  One scoped exception: timeline, version, and attachment assembly first checks
  read permission on the focal document, then fetches its timeline rows the way
  frappe core's `get_docinfo` does — `get_all` with explicit limits, marked
  `# focal-doc-gated`. Nowhere else.
- **Agents draft, humans submit.** No code path may submit, cancel, or delete
  without a human approval record.
- **Use Frappe's own features first** — doctypes, roles, User Permissions,
  Workflow, RQ, realtime — before inventing a parallel mechanism.

## Working on this repo

- Committing? Use the `semantic-commit` skill.
- Commit as you go: one small commit per coherent change. Don't batch a
  phase or milestone into one big commit.
- `internal/` is never committed. No internal plans or notes in commits,
  docs, or code comments.
- Frappe's official skills help here. Install locally:
  `npx skills add frappe/skills --skill frappe-app-dev --skill quality-code-review -a claude-code -y`
  They have no upstream license — never commit them.
- Docs are for users: short sentences, plain words, no filler. Procedures follow
  ASD-STE100 principles — one instruction per sentence, active voice, one term
  per concept, no synonyms. Explanatory pages keep their reasoning and their
  metaphors; that is what makes them land.
- **A change a user can see ships its doc in the same PR.** If there is no page
  for the thing you changed, that is the finding — write it or file it. A
  screenshot in `docs/images/` that the change invalidates is part of that.
- Keep this file small. Details go in `docs/` and get linked, not inlined.

## Releases

- Versions are computed (semantic-release); release notes are AUTHORED for the
  person upgrading — never ship the generated commit list as the body.
- Notes template: one sentence on what the release is, then **New** (user
  language, where to find it), **After you upgrade** (what migrate does, what
  to enable, what changed), **Fixed** (human sentences). The generated commit
  list goes inside a collapsed `<details>`. Breaking changes first, bold.
- Full procedure: `.claude/skills/release/SKILL.md`.
- Public-facing text (release notes, docs, skills) never credits individuals
  by name.
