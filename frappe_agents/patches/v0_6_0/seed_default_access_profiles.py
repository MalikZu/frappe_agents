# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Give sites installed before the matrix the profiles a fresh install ships.

`after_install` seeds them for new sites; this does the same for the ones that
were already here. Both call the same insert-if-missing function, so a profile
the site already has — shipped or hand-written — is left untouched.
"""

from frappe_agents.access.default_profiles import seed_default_profiles


def execute() -> None:
	for name in seed_default_profiles():
		print(f"frappe_agents: seeded access profile {name}")
