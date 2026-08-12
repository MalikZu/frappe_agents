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
- Docs are for users: short sentences, plain words, no filler.
- Keep this file small. Details go in `docs/` and get linked, not inlined.
