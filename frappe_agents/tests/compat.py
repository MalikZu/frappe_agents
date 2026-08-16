"""The test base class, wherever this framework keeps it.

v16 exports `IntegrationTestCase` from `frappe.tests`. v15 has no such name —
its equivalent is `FrappeTestCase` in `frappe.tests.utils`. Every test module
imports from here rather than from frappe directly, so the two version branches
of this app can hold the identical file.

Import site, not behaviour: this shim chooses a base class and nothing else.
"""

try:  # Frappe v16
	from frappe_agents.tests.compat import IntegrationTestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

__all__ = ["IntegrationTestCase"]
