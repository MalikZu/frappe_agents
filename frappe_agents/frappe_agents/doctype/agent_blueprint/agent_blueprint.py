# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the Agents Builder produces: a proposal for an agent, in plain data.

A blueprint governs nothing. It holds a purpose, a suggested model and the rules
an agent would need, and it sits there until a human with Agent Manager presses
Create Agent — which is why this is the one doctype in the app an agent may
write. The materialisation is the human's act, and it is recorded as one.

`create_agent` is deliberately unexciting. It copies the rules into a profile,
makes an Agent that carries it, and stops:

* the Agent is created **disabled**, with the autonomy field's own default —
  Suggest, the narrowest one. Someone reads it, picks a model, decides how much
  rope, and switches it on. Nothing an agent drafted turns itself on.
* every write here goes through the pressing user's own permissions. No
  `ignore_permissions`: a person who may not create an Agent may not create one
  by way of a blueprint either.
* the blueprint is marked Applied and keeps the names it made, so the next
  reader can see that this proposal already became something.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_agents.frappe_agents.doctype.agent_access_rule.agent_access_rule import validate_rules

STATUS_DRAFT = "Draft"
STATUS_APPLIED = "Applied"

MANAGER_ROLE = "Agent Manager"

AGENT = "Agent"
PROFILE = "Agent Access Profile"
MODEL_PROFILE = "LLM Model Profile"

# What the profile made from a blueprint is called. One suffix, so a manager
# reading the profile list can tell where a set of rules came from.
PROFILE_SUFFIX = "Access"

# The record of the human act. `create_agent` writes these three and nothing
# else does — least of all the agent that drafted the proposal.
MATERIALISATION_FIELDS = ("status", "created_agent", "created_profile")

RULE_FIELDS = (
	"target_type",
	"target",
	"can_read",
	"can_create_draft",
	"can_update_draft",
	"can_propose",
	"can_extract",
	"update_any_draft",
	"max_rows_per_call",
)


class AgentBlueprint(Document):
	def validate(self) -> None:
		validate_rules(self, "suggested_rules")
		self.protect_materialisation()

	def protect_materialisation(self) -> None:
		"""Keep the three fields that record the human act out of an agent's reach.

		An agent drafts a blueprint and revises it; the draft tools write the
		document as any other draft, which would let a model set `status` to
		Applied and name an agent it did not create. That is precisely the claim
		the Builder is told never to make, and a prompt is the wrong place to
		enforce it — so a write made inside a run keeps whatever these three
		already said.
		"""
		from frappe_agents.tools.base import current_run

		if current_run() is None:
			return

		stored = (
			frappe.db.get_value(self.doctype, self.name, MATERIALISATION_FIELDS, as_dict=True)
			if not self.is_new()
			else None
		)
		for field in MATERIALISATION_FIELDS:
			self.set(field, stored.get(field) if stored else None)
		self.status = self.status or STATUS_DRAFT

	@frappe.whitelist()
	def create_agent(self) -> dict:
		"""Materialise this blueprint into a disabled Agent and its access profile.

		Only a human ever reaches this: the check below is on the role, not on the
		record, because the question is who is pressing the button.
		"""
		self.check_permission("write")
		if MANAGER_ROLE not in frappe.get_roles():
			frappe.throw(_("Only an Agent Manager can turn a blueprint into an agent."))

		if self.status == STATUS_APPLIED:
			frappe.throw(
				_("This blueprint was already applied. It created {0}.").format(
					self.created_agent or "an agent"
				)
			)

		title = (self.title or "").strip()
		if frappe.db.exists(AGENT, title):
			frappe.throw(
				_("There is already an agent called {0}. Rename the blueprint and try again.").format(title)
			)

		profile = self.build_profile(title)
		agent = self.build_agent(title, profile)

		self.status = STATUS_APPLIED
		self.created_agent = agent.name
		self.created_profile = profile.name if profile else None
		self.save()

		return {
			"agent": agent.name,
			"profile": profile.name if profile else None,
			"enabled": 0,
			"note": _(
				"{0} was created switched off. Give it a model, decide its autonomy, then enable it."
			).format(agent.name),
		}

	def build_profile(self, title: str) -> Document | None:
		"""The blueprint's suggested rules as a profile of their own.

		A blueprint with no rules makes no profile: an empty one would read like a
		grant that was taken away, and the agent is deny-by-default without it.
		"""
		rows = self.get("suggested_rules") or []
		if not rows:
			return None

		name = f"{title} {PROFILE_SUFFIX}"
		if frappe.db.exists(PROFILE, name):
			frappe.throw(
				_(
					"There is already an access profile called {0}. Rename the blueprint and try again."
				).format(name)
			)

		profile = frappe.new_doc(PROFILE)
		profile.profile_name = name
		profile.description = _("Created from the blueprint {0}.").format(self.name)
		for row in rows:
			profile.append("rules", {field: row.get(field) for field in RULE_FIELDS})
		profile.insert()
		return profile

	def build_agent(self, title: str, profile: Document | None) -> Document:
		"""The agent itself: switched off, and carrying what the blueprint asked for."""
		agent = frappe.new_doc(AGENT)
		agent.agent_name = title
		agent.enabled = 0
		agent.run_as = "Session User"
		agent.instructions = (self.purpose or "").strip()

		suggested = (self.suggested_model or "").strip()
		if suggested and frappe.db.exists(MODEL_PROFILE, suggested):
			agent.model_profile = suggested

		for name in self.attachable_profiles(profile):
			agent.append("access_profiles", {"access_profile": name})

		# A model is picked by the person who enables the agent, and until then the
		# record is a draft of an agent rather than one. Mandatory fields are what
		# stops it being saved half-written by hand; this is the one path that is
		# meant to leave it that way.
		agent.flags.ignore_mandatory = True
		agent.insert()
		return agent

	def attachable_profiles(self, created: Document | None) -> list[str]:
		"""The created profile, then every suggested profile that really exists.

		A name the drafter invented is dropped with a message rather than an error:
		the agent is worth having, and the manager can attach the rest by hand.
		"""
		names = [created.name] if created else []
		for line in (self.suggested_profiles or "").splitlines():
			name = line.strip()
			if not name or name in names:
				continue
			if frappe.db.exists(PROFILE, name):
				names.append(name)
			else:
				frappe.msgprint(
					_("There is no access profile called {0}, so it was not attached.").format(name),
					indicator="orange",
				)
		return names
