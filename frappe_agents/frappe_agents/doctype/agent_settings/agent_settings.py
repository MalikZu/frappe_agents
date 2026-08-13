# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

from frappe.model.document import Document

from frappe_agents.tools.base import publish_kill_switch


class AgentSettings(Document):
	def on_update(self) -> None:
		# A run already on a worker cannot see this row change: it reads the
		# database inside a transaction that was pinned before the save. So the
		# kill switch is published where every process can read it.
		publish_kill_switch(self.global_enabled)
