# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestDocumentExtraction(IntegrationTestCase):
	def test_no_role_may_edit_an_extraction(self):
		for perm in frappe.get_meta("Document Extraction").permissions:
			self.assertFalse(perm.write, perm.role)
			self.assertFalse(perm.delete, perm.role)

	def test_agent_user_sees_only_own_extractions(self):
		perms = [p for p in frappe.get_meta("Document Extraction").permissions if p.role == "Agent User"]
		self.assertEqual(len(perms), 1)
		self.assertTrue(perms[0].if_owner)
		self.assertTrue(perms[0].create)

	def test_auditor_reads_every_extraction(self):
		roles = {p.role: p for p in frappe.get_meta("Document Extraction").permissions}
		self.assertTrue(roles["Agent Auditor"].read)
		self.assertFalse(roles["Agent Auditor"].if_owner)

	def test_starts_pending(self):
		self.assertEqual(frappe.get_meta("Document Extraction").get_field("status").default, "Pending")

	def test_reviews_are_tracked(self):
		self.assertTrue(frappe.get_meta("Document Extraction").track_changes)
