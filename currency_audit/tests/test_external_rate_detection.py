"""Tests for currency_audit.external_rate_detection (F1.6).

_Test Company's base currency is INR (established elsewhere in this test suite),
so a USD-denominated document against it always represents a real FX conversion.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import external_rate_detection
from currency_audit.test_helpers import get_or_create_currency_exchange


class TestCheckRate(FrappeTestCase):
	"""Unit-level coverage of the guards in _check_rate, without the overhead of a
	real document submission for each edge case."""

	def test_skips_when_currency_or_rate_missing(self):
		with patch("currency_audit.utils.frappe.enqueue") as mock_enqueue:
			external_rate_detection._check_rate(None, 55.0, "_Test Company")
			external_rate_detection._check_rate("USD", None, "_Test Company")
			external_rate_detection._check_rate("USD", 0, "_Test Company")
		mock_enqueue.assert_not_called()

	def test_skips_same_currency_as_company_base(self):
		# _Test Company's base currency is INR — conversion_rate == 1 is trivial
		# for an INR document, not a real FX rate to check.
		with patch("currency_audit.utils.frappe.enqueue") as mock_enqueue:
			external_rate_detection._check_rate("INR", 5.0, "_Test Company")
		mock_enqueue.assert_not_called()

	def test_skips_when_already_recorded_in_currency_exchange(self):
		# _check_rate's "already recorded" check is intentionally date-agnostic
		# (Principle 2), so a Currency Exchange record dated far in the future
		# (this test helper's usual pattern) still counts.
		get_or_create_currency_exchange("USD", "INR", 71.5)

		with patch("currency_audit.utils.frappe.enqueue") as mock_enqueue:
			external_rate_detection._check_rate("USD", 71.5, "_Test Company")
		mock_enqueue.assert_not_called()

	def test_skips_when_recorded_only_historically_in_our_own_log(self):
		# A rate that was a real Change (54 -> 55) and has since moved on (55 -> 56)
		# is still fully audited — checking only the live Currency Exchange value
		# would wrongly flag a later transaction using 54 or 55 as "unrecorded".
		currency_exchange = get_or_create_currency_exchange("USD", "INR", 56.0)
		from currency_audit.test_helpers import make_log

		with patch("currency_audit.utils.frappe.enqueue"):
			make_log(currency_exchange, old_rate=54.0, new_rate=55.0)

		with patch("currency_audit.utils.frappe.enqueue") as mock_enqueue:
			external_rate_detection._check_rate("USD", 54.0, "_Test Company")
			external_rate_detection._check_rate("USD", 55.0, "_Test Company")
		mock_enqueue.assert_not_called()

	def test_creates_external_entry_for_an_unrecorded_rate(self):
		rate = 91.371
		self.assertFalse(
			frappe.db.exists(
				"Currency Exchange", {"from_currency": "USD", "to_currency": "INR", "exchange_rate": rate}
			)
		)

		with patch("currency_audit.utils.frappe.enqueue"):
			external_rate_detection._check_rate("USD", rate, "_Test Company")

		entry = frappe.get_all(
			"Currency Exchange Log",
			filters={"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			fields=["name", "currency_exchange", "old_rate"],
		)
		self.assertEqual(len(entry), 1)
		self.assertIsNone(entry[0].currency_exchange)
		self.assertEqual(entry[0].old_rate, 0)

	def test_does_not_duplicate_the_external_entry_for_a_repeated_rate(self):
		rate = 91.372
		with patch("currency_audit.utils.frappe.enqueue"):
			external_rate_detection._check_rate("USD", rate, "_Test Company")
			external_rate_detection._check_rate("USD", rate, "_Test Company")

		entries = frappe.get_all(
			"Currency Exchange Log",
			filters={"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
		)
		self.assertEqual(len(entries), 1)


class TestCheckStandardDoctype(FrappeTestCase):
	def test_submitting_an_invoice_with_an_unrecorded_rate_creates_an_external_entry(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		rate = 91.373
		with patch("currency_audit.utils.frappe.enqueue"):
			invoice = create_sales_invoice(
				company="_Test Company",
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=rate,
			)

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			)
		)
		self.assertEqual(invoice.docstatus, 1)


class TestCheckPaymentEntry(FrappeTestCase):
	def test_checks_both_sides_of_the_payment(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		source_rate, target_rate = 91.374, 91.375
		with patch("currency_audit.utils.frappe.enqueue"):
			invoice = create_sales_invoice(
				company="_Test Company",
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=1,
			)
			# Bank account currency deliberately differs from the receivable (EUR vs
			# USD) — Payment Entry's own set_target_exchange_rate() forces
			# target_exchange_rate = source_exchange_rate whenever both sides share
			# a currency, which would silently defeat this test otherwise.
			pe = get_payment_entry("Sales Invoice", invoice.name, bank_account="_Test Bank EUR - _TC")
			pe.source_exchange_rate = source_rate
			pe.target_exchange_rate = target_rate
			pe.reference_no = "TEST-REF-EXT-0001"
			pe.reference_date = frappe.utils.nowdate()
			pe.insert(ignore_permissions=True)
			pe.submit()

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": source_rate},
			)
		)
		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "EUR", "to_currency": "INR", "new_rate": target_rate},
			)
		)


class TestCheckJournalEntry(FrappeTestCase):
	def test_checks_each_account_row(self):
		from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry

		rate = 91.376
		with patch("currency_audit.utils.frappe.enqueue"):
			jv = make_journal_entry(
				"_Test Bank USD - _TC", "_Test Cash - _TC", 100, exchange_rate=1, save=False
			)
			jv.accounts[0].exchange_rate = rate
			jv.accounts[1].credit_in_account_currency = 100 * rate
			jv.insert()
			jv.submit()

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{"entry_type": "External", "from_currency": "USD", "to_currency": "INR", "new_rate": rate},
			)
		)
