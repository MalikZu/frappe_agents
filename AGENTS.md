# frappe_agents

Open-source agent runtime for Frappe (v16). Agents are records, not deployments.
The permission model is the product.

## Rules

- **Frappe only.** Never import or depend on `erpnext`. ERP-specific behavior
  goes in optional integration modules, off by default.
- **One door to data.** Agent tools use the permission-checked ORM only:
  `frappe.get_list`, `frappe.get_doc` after `has_permission`. Never
  `frappe.db.sql`, `frappe.get_all`, or `ignore_permissions=True` in tool code.
- **Agents draft, humans submit.** No code path may submit, cancel, or delete
  without a human approval record.
- **Use Frappe's own features first** — doctypes, roles, User Permissions,
  Workflow, RQ, realtime — before inventing a parallel mechanism.

## Working on this repo

- Committing? Use the `semantic-commit` skill.
- `internal/` is never committed. No internal plans or notes in commits,
  docs, or code comments.
- Docs are for users: short sentences, plain words, no filler.
- Keep this file small. Details go in `docs/` and get linked, not inlined.
