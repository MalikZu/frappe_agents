# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The access profiles a site starts with, and the discipline for seeding them.

Two profiles, both built out of frappe's own doctypes so they mean the same
thing on every site: **Personal Organizer**, which keeps one person's day in
order, and **Site Reader**, which reads the shared address book and writes
nothing. They are a starting point a manager attaches and then narrows, not a
policy the app imposes: a profile grants nothing until an agent carries it.

Seeding follows the catalog discipline, and all of it matters:

* **Insert if missing.** A profile the site already has is left exactly as it
  is — rules, description and all. Someone edited it for a reason, and a
  migration that re-asserted the shipped rows would silently widen or narrow a
  live agent's access.
* **Idempotent.** Running it twice changes nothing the second time, which is
  what lets `after_install` and a patch both call it.
* **Skip what the site does not have.** A rule naming a doctype that is not
  installed would refuse at validation and take the whole seed down with it.

Seeded profiles are inert by construction, so this writes with
`ignore_permissions`: no human is on the other end of an install.
"""

import frappe

from frappe_agents.access.exclusions import is_excluded

PROFILE_DOCTYPE = "Agent Access Profile"

PERSONAL_ORGANIZER = "Personal Organizer"
SITE_READER = "Site Reader"

# `update_any_draft` is left at 0 everywhere on purpose: the organizer edits the
# drafts its own user made. Widening that is a decision a manager takes per
# agent, with the reason in front of them.
DEFAULT_PROFILES = (
	{
		"profile_name": PERSONAL_ORGANIZER,
		"description": (
			"Keeps one person's day in order: reads todos, notes, events and contacts, "
			"drafts new ones, and edits the todo and note drafts its own user created."
		),
		"rules": (
			{"target": "ToDo", "can_read": 1, "can_create_draft": 1, "can_update_draft": 1},
			{"target": "Note", "can_read": 1, "can_create_draft": 1, "can_update_draft": 1},
			{"target": "Event", "can_read": 1, "can_create_draft": 1},
			{"target": "Contact", "can_read": 1, "can_create_draft": 1},
		),
	},
	{
		"profile_name": SITE_READER,
		"description": "Reads todos, notes and the shared address book. Writes nothing at all.",
		"rules": (
			{"target": "ToDo", "can_read": 1},
			{"target": "Note", "can_read": 1},
			{"target": "Contact", "can_read": 1},
			{"target": "Address", "can_read": 1},
		),
	},
)


def seed_default_profiles() -> list[str]:
	"""Create the shipped profiles that are missing. Returns what it created."""
	created = []
	for spec in DEFAULT_PROFILES:
		name = spec["profile_name"]
		if frappe.db.exists(PROFILE_DOCTYPE, name):
			continue

		rules = seedable_rules(spec["rules"])
		if not rules:
			# Nothing this site can carry. An empty profile reads as a grant that
			# was taken away, so it is better not to ship one at all.
			continue

		profile = frappe.new_doc(PROFILE_DOCTYPE)
		profile.profile_name = name
		profile.description = spec["description"]
		for row in rules:
			profile.append("rules", row)
		profile.flags.ignore_permissions = True
		profile.insert(ignore_permissions=True)
		created.append(name)

	return created


def seedable_rules(rules: tuple | list) -> list[dict]:
	"""The shipped rows this site can actually carry, as rule rows."""
	rows = []
	for row in rules:
		target = row["target"]
		if not frappe.db.exists("DocType", target) or is_excluded(target):
			continue
		rows.append({"target_type": "DocType", **row})
	return rows
