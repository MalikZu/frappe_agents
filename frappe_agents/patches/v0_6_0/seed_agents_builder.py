# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Give existing sites the Agents Builder a fresh install now ships.

Same insert-if-missing seed `after_install` runs, so a site that already has a
profile called Builder or an agent called Agents Builder keeps its own. The
agent arrives disabled and without a model either way: this patch switches
nothing on.
"""

from frappe_agents.access.builder import seed_agents_builder


def execute() -> None:
	for name in seed_agents_builder():
		print(f"frappe_agents: seeded {name}")
