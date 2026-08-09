# Copyright (c) 2026, Omar Sabry and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from currency_audit.test_helpers import get_or_create_currency_exchange, make_log


class TestCurrencyExchangeLog(FrappeTestCase):
	"""Covers F2's immutability guarantees: protected fields and deletion are blocked
	after insert; only the background job's usage/matching_status updates are allowed."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.currency_exchange = get_or_create_currency_exchange("USD", "GBP", 0.8)

	def test_protected_field_cannot_be_changed(self):
		log = make_log(self.currency_exchange, new_rate=0.8, old_rate=0.79)
		log.new_rate = 5
		self.assertRaises(frappe.ValidationError, log.save, ignore_permissions=True)

	def test_cannot_be_deleted(self):
		log = make_log(self.currency_exchange, new_rate=0.8, old_rate=0.79)
		self.assertRaises(
			frappe.ValidationError,
			frappe.delete_doc,
			"Currency Exchange Log",
			log.name,
			ignore_permissions=True,
		)

	def test_usage_and_matching_status_can_still_be_updated(self):
		log = make_log(self.currency_exchange, new_rate=0.8, old_rate=0.79)
		log.matching_status = "Completed"
		log.append(
			"usage",
			{
				# "Currency" used as the reference doctype here since this test is about
				# the log's own mutability rules, not real matching output (covered by
				# currency_audit.tests.test_matching against real transaction doctypes).
				"reference_doctype": "Currency",
				"reference_name": "USD",
				"posting_date": nowdate(),
				"currency": "USD",
				"amount": 100,
				"rate_type": "New Rate",
			},
		)
		log.save(ignore_permissions=True)
		log.reload()
		self.assertEqual(log.matching_status, "Completed")
		self.assertEqual(len(log.usage), 1)

	def test_baseline_entry_has_no_prior_rate_semantics(self):
		log = make_log(self.currency_exchange, new_rate=0.8, old_rate=None, entry_type="Baseline")
		log.reload()
		self.assertEqual(log.entry_type, "Baseline")
		self.assertEqual(log.old_rate, 0)
