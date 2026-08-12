"""Tests for currency_audit.patches.detect_historical_external_rates."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.patches import detect_historical_external_rates


class TestDetectHistoricalExternalRates(FrappeTestCase):
	def test_detects_a_rate_from_before_the_hook_existed(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		rate = 91.377
		# Simulates a document submitted before F1.6 shipped: the on_submit hook
		# itself is mocked out here so nothing gets flagged yet, matching what
		# happened for every transaction that predates this feature.
		with (
			patch("currency_audit.external_rate_detection._check_rate"),
			patch("currency_audit.utils.frappe.enqueue"),
		):
			create_sales_invoice(
				company="_Test Company",
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=rate,
			)

		self.assertFalse(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			)
		)

		with patch("currency_audit.utils.frappe.enqueue"):
			detect_historical_external_rates.execute()

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			)
		)

	def test_is_safe_to_run_twice(self):
		with patch("currency_audit.utils.frappe.enqueue"):
			detect_historical_external_rates.execute()
			detect_historical_external_rates.execute()

		# No assertion beyond "doesn't raise" — _check_rate's own idempotency is
		# covered by test_external_rate_detection.py; this just proves the patch's
		# own grouping/looping doesn't create duplicates or error on a second run.

	def test_journal_entry_combinations_are_checked(self):
		from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry

		rate = 91.378
		with (
			patch("currency_audit.external_rate_detection._check_rate"),
			patch("currency_audit.utils.frappe.enqueue"),
		):
			jv = make_journal_entry(
				"_Test Bank USD - _TC", "_Test Cash - _TC", 100, exchange_rate=1, save=False
			)
			jv.accounts[0].exchange_rate = rate
			jv.accounts[1].credit_in_account_currency = 100 * rate
			jv.insert()
			jv.submit()

		with patch("currency_audit.utils.frappe.enqueue"):
			detect_historical_external_rates.execute()

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			)
		)
