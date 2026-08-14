# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

from frappe_agents.default_catalog import seed_default_catalog


def execute():
	# Existing sites get the same catalog new installs get from after_install.
	# Insert-if-missing, so a site where any of these rows already exist —
	# or were edited — is left alone.
	seed_default_catalog()
