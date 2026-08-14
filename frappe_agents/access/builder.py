# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The Agents Builder: the agent whose job is designing other agents.

Writing an agent by hand means knowing which doctypes a job touches, what the
verbs mean, and which of them the job actually needs. That is an interview, and
an interview is what a model is good at. So the app ships one: an agent that
asks a manager what the work is, looks the site's own doctypes up, and writes an
**Agent Blueprint** — a proposal on paper.

It creates nothing. A blueprint grants no access to anybody; a human with Agent
Manager presses Create Agent, and even then the agent that appears is switched
off. The Builder's whole authority is the right to draft one doctype.

Seeded the way the default profiles are — insert if missing, idempotent, never
clobbered — and seeded **disabled and without a model**, on the same principle
that makes a profile inert: shipping something is not switching it on. Enabling
it is two deliberate edits, a model profile and the checkbox, and whoever makes
them has read what the thing does.
"""

import frappe

from frappe_agents.access.exclusions import BLUEPRINT

PROFILE_DOCTYPE = "Agent Access Profile"
AGENT_DOCTYPE = "Agent"

BUILDER_PROFILE = "Builder"
BUILDER_AGENT = "Agents Builder"

BUILDER_DESCRIPTION = (
	"Lets an agent draft Agent Blueprints and read the site's doctypes and reports "
	"by name. It reads no documents and creates no agents."
)

# One target, three verbs. Drafting a blueprint is also what opens the builder's
# meta tools — see `access.grants.BUILDER_TOOLS` — so this row is the whole grant.
# `update_any_draft` stays 0: the Builder revises the blueprints of the person it
# is talking to, not somebody else's half-finished design.
BUILDER_RULES = (
	{
		"target_type": "DocType",
		"target": BLUEPRINT,
		"can_read": 1,
		"can_create_draft": 1,
		"can_update_draft": 1,
		"update_any_draft": 0,
	},
)

BUILDER_INSTRUCTIONS = """You help a manager design an agent for this site. Your one output is an Agent Blueprint: a written proposal. You never create an agent, and you say so plainly.

Interview them first, one question at a time, in their own words.

* What job should the agent do? What does somebody do by hand today?
* Who will use it — one person, a team, everybody?
* Which records does that job touch? Use list_site_doctypes and describe_site_doctype to find the real doctype names on this site, and read the site's own labels back to them instead of guessing. Use list_site_reports when the job is about numbers somebody already reports on.
* Per record: does the agent only need to read it, or also draft new ones, edit drafts, or ask a person to submit or cancel? Does it need to pull fields out of an attached document?
* How much rope: should it only answer questions, or write drafts a person then checks?

Explain access in plain words, because most managers have not seen this model before:

* An agent is not given tools. It is given a list of records and, per record, what it may do: read, draft new, edit drafts, propose a submit or cancel, extract from a document. The tools follow from that list. A record that is not on the list does not exist as far as the agent is concerned.
* An agent never exceeds the person using it. If they cannot see something, neither can the agent, whatever the rules say. The rules only narrow.
* Agents draft; people submit. Nothing an agent does commits the business on its own.
* Editing other people's drafts is off unless they ask for it, and a row cap is worth setting when a job is about a handful of records at a time.

Then propose the narrowest set of rules that does the job, and say why each row is there. If they ask for something the framework refuses — users, roles, permissions, or anything belonging to the agent framework itself — tell them it is refused and why: an agent is never handed the keys to what governs it.

When they agree, write the blueprint with create_draft on Agent Blueprint: a title, the purpose in their words, the suggested rules, and the names of any existing access profiles worth attaching. Read it back to them, and use update_draft to fix it as they change their mind.

Finish by telling them where to go: open the blueprint and press Create Agent. That button is the only thing that makes an agent, only somebody with the Agent Manager role can press it, and the agent it makes arrives switched off with no model until a person finishes it. Never say that you created an agent, and never imply that anything you wrote has granted anybody access.
"""


def seed_agents_builder() -> list[str]:
	"""Create the Builder profile and the Agents Builder agent if they are missing.

	Returns what it created, and creates nothing on a second run — the same
	discipline the default profiles keep, and for the same reason: a site that
	edited or deleted either of these meant to.
	"""
	created = []

	if not frappe.db.exists(PROFILE_DOCTYPE, BUILDER_PROFILE):
		profile = frappe.new_doc(PROFILE_DOCTYPE)
		profile.profile_name = BUILDER_PROFILE
		profile.description = BUILDER_DESCRIPTION
		for row in BUILDER_RULES:
			profile.append("rules", dict(row))
		profile.flags.ignore_permissions = True
		profile.insert(ignore_permissions=True)
		created.append(BUILDER_PROFILE)

	if not frappe.db.exists(AGENT_DOCTYPE, BUILDER_AGENT):
		agent = frappe.new_doc(AGENT_DOCTYPE)
		agent.update(
			{
				"agent_name": BUILDER_AGENT,
				# Off, and no model profile. A shipped agent that could answer the
				# moment it was installed would be a decision the site never took.
				"enabled": 0,
				"run_as": "Session User",
				# It writes drafts of one doctype. Nothing it holds can submit.
				"autonomy": "Draft",
				"instructions": BUILDER_INSTRUCTIONS,
				"may_read_files": 0,
				"max_steps": 20,
			}
		)
		agent.append("access_profiles", {"access_profile": BUILDER_PROFILE})
		# Model Profile is mandatory on the form, and deliberately empty here:
		# picking one is half of switching this agent on.
		agent.flags.ignore_mandatory = True
		agent.flags.ignore_permissions = True
		agent.insert(ignore_permissions=True)
		created.append(BUILDER_AGENT)

	return created
