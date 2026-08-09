"""Tests for currency_audit.matching (F4 background matching job).

Exercises the shared standard-doctype query pattern once (via Sales Invoice — the
same code path covers Purchase Invoice / POS Invoice / Purchase Receipt / Delivery
Note per PRD F4), plus the two doctypes with their own matching logic: Payment Entry
(rate per side) and Journal Entry (rate per child-table row).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import matching
from currency_audit.test_helpers import get_or_create_currency_exchange, make_log

# Note: deliberately no `test_dependencies` here — ERPNext's own recursive dependency
# walk (frappe.test_runner.get_dependencies) hits a "Payment Gateway" doctype that
# isn't installed on this site and aborts test collection entirely. The multi-currency
# fixtures these tests need (_Test Customer USD, _Test Receivable/Bank USD - _TC, etc.)
# are standard ERPNext test records already present on this site.


class TestMatchStandardDoctype(FrappeTestCase):
	"""Covers the shared query pattern for Sales Invoice / Purchase Invoice / POS
	Invoice / Purchase Receipt / Delivery Note."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.rate = 57.5
		cls.invoice = create_sales_invoice(
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=cls.rate,
		)

	def test_matches_submitted_invoice_by_currency_and_rate(self):
		rows = matching._match_standard_doctype("Sales Invoice", "USD", self.rate, "New Rate")
		names = [r["reference_name"] for r in rows]
		self.assertIn(self.invoice.name, names)

		row = next(r for r in rows if r["reference_name"] == self.invoice.name)
		self.assertEqual(row["reference_doctype"], "Sales Invoice")
		self.assertEqual(row["currency"], "USD")
		self.assertEqual(row["amount"], self.invoice.grand_total)
		self.assertEqual(row["rate_type"], "New Rate")

	def test_does_not_match_a_different_rate(self):
		rows = matching._match_standard_doctype("Sales Invoice", "USD", self.rate + 1, "New Rate")
		names = [r["reference_name"] for r in rows]
		self.assertNotIn(self.invoice.name, names)


class TestMatchPaymentEntry(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.rate = 61.25
		invoice = create_sales_invoice(
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=1,
		)
		pe = get_payment_entry("Sales Invoice", invoice.name, bank_account="_Test Bank USD - _TC")
		pe.source_exchange_rate = cls.rate
		pe.target_exchange_rate = cls.rate
		pe.reference_no = "TEST-REF-0001"
		pe.reference_date = frappe.utils.nowdate()
		pe.insert(ignore_permissions=True)
		pe.submit()
		cls.payment_entry = pe

	def test_matches_on_source_side(self):
		rows = matching._match_payment_entry("USD", self.rate, "Old Rate")
		names = [r["reference_name"] for r in rows]
		self.assertIn(self.payment_entry.name, names)


class TestMatchJournalEntry(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry

		cls.rate = 62.75
		jv = make_journal_entry("_Test Bank USD - _TC", "_Test Cash - _TC", 100, exchange_rate=1, save=False)
		jv.accounts[0].exchange_rate = cls.rate
		# The contra leg is in company currency, so its company-currency amount must be
		# rebalanced to match the USD leg's new company-currency equivalent (100 * rate).
		jv.accounts[1].credit_in_account_currency = 100 * cls.rate
		jv.insert()
		jv.submit()
		cls.journal_entry = jv

	def test_matches_on_the_usd_row(self):
		rows = matching._match_journal_entry("USD", self.rate, "New Rate")
		names = [r["reference_name"] for r in rows]
		self.assertIn(self.journal_entry.name, names)


class TestRunMatchingJob(FrappeTestCase):
	"""End-to-end: a log entry's usage table gets populated and matching_status flips
	to Completed after the job runs (F4 success criteria)."""

	def test_populates_usage_and_marks_completed(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		rate = 48.0
		invoice = create_sales_invoice(
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=rate,
		)
		currency_exchange = get_or_create_currency_exchange("USD", "INR", rate)
		log = make_log(currency_exchange, new_rate=rate, old_rate=rate - 1)

		matching.run_matching_job(log.name)

		log.reload()
		self.assertEqual(log.matching_status, "Completed")
		matched_names = [row.reference_name for row in log.usage]
		self.assertIn(invoice.name, matched_names)
