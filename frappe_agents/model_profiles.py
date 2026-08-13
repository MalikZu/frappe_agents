"""Which model an agent may be run with, and who may choose it.

One wall, read from two sides. The agent's owner decides WHAT the agent may run:
its default `model_profile` plus whatever it lists in `alternate_profiles`.
The profile itself decides WHO may run it, through the `allowed_roles` table the
extraction pipeline already gates on. A choice has to pass both.

Nothing here writes. It answers two questions — "what may this user pick?" and
"is this pick legal?" — and every door that lets a person name a model asks one
of them: the chat API, the Agent Conversation controller, and the runner just
before it builds a provider.
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint


def agent_profiles(agent: Any) -> list[str]:
	"""Every profile this agent may run, its default first, without duplicates."""
	names = [agent.model_profile] if agent.model_profile else []
	for row in agent.get("alternate_profiles") or []:
		name = row.get("model_profile")
		if name and name not in names:
			names.append(name)
	return names


def profile_choices(agent: Any, roles: set | None = None) -> list[dict]:
	"""The profiles `roles` may pick for this agent, default first.

	A disabled profile is not offered: the provider refuses it anyway, and an
	option that always fails is worse than no option.
	"""
	roles = set(frappe.get_roles()) if roles is None else roles
	choices = []
	for index, name in enumerate(agent_profiles(agent)):
		profile = _profile(name)
		if profile is None or not cint(profile.enabled):
			continue
		if not roles_allow(profile, roles):
			continue
		choices.append(
			{
				"name": profile.name,
				"model_id": profile.model_id,
				"default": 1 if index == 0 else 0,
			}
		)
	return choices


def check_profile(agent: Any, model_profile: str, roles: set | None = None) -> str:
	"""Return the profile if this agent and this user may run it, else refuse.

	Both halves of the wall are checked here, and the refusal says which half
	stopped it — an owner who never listed the profile and a user whose roles fall
	short are different problems with different fixes.
	"""
	roles = set(frappe.get_roles()) if roles is None else roles

	if model_profile not in agent_profiles(agent):
		frappe.throw(
			_("Agent {0} is not set up to run on the model profile {1}.").format(agent.name, model_profile)
		)

	profile = _profile(model_profile)
	if profile is None:
		frappe.throw(_("No such model profile: {0}").format(model_profile))
	if not cint(profile.enabled):
		frappe.throw(_("Model profile {0} is disabled.").format(profile.name))
	if not roles_allow(profile, roles):
		raise frappe.PermissionError(
			_("You are not allowed to use the model profile {0}.").format(profile.name)
		)

	return profile.name


def roles_allow(profile: Any, roles: set) -> bool:
	"""Whether these roles pass the profile's own Allowed Roles. Empty means anyone."""
	allowed = {row.role for row in (profile.get("allowed_roles") or []) if row.role}
	return not allowed or bool(allowed & roles)


def _profile(name: str) -> Any:
	"""The profile document, or None when the link points at nothing.

	Read through the document cache rather than `get_list`: LLM Model Profile is
	readable by managers alone, and this is the framework deciding what to run,
	not a user reading a record.
	"""
	try:
		return frappe.get_cached_doc("LLM Model Profile", name)
	except frappe.DoesNotExistError:
		return None
