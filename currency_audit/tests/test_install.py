"""Tests for currency_audit.install (composite DB indexes the F4 matching job relies on)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.install import INDEXED_FIELDS, add_matching_indexes


class TestAddMatchingIndexes(FrappeTestCase):
	def test_creates_expected_indexes_idempotently(self):
		# Runs twice to prove it's safe to call repeatedly (e.g. re-running after_install).
		add_matching_indexes()
		add_matching_indexes()

		for doctype, fields in INDEXED_FIELDS:
			table = f"tab{doctype}"
			index_name = frappe.db.get_index_name(fields)
			self.assertTrue(
				frappe.db.has_index(table, index_name),
				msg=f"expected index {index_name} on {table}",
			)
