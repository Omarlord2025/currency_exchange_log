"""Tests for currency_audit.matching (F4 background matching job).

Exercises the shared standard-doctype query pattern once (via Sales Invoice — the
same code path covers Purchase Invoice / POS Invoice / Purchase Receipt / Delivery
Note per PRD F4), plus the two doctypes with their own matching logic: Payment Entry
(rate per side) and Journal Entry (rate per child-table row).
"""

from unittest.mock import patch

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

	def test_a_failure_is_caught_instead_of_leaving_the_job_unhandled(self):
		# frappe.get_doc used to sit outside the try/except: any failure there (e.g. a
		# race between insert and a worker picking the job up) crashed the RQ job
		# unhandled, leaving matching_status stuck on "Pending" forever with no trace.
		# It must now be caught and logged like any other failure in the job.
		with patch("currency_audit.matching.frappe.log_error") as mock_log_error:
			matching.run_matching_job("CEL-DOES-NOT-EXIST")

		mock_log_error.assert_called_once()


class TestRetryStuckMatches(FrappeTestCase):
	"""Scheduled safety net: re-queues logs that have been Pending too long, covering
	cases where the original enqueue was lost (e.g. no worker was running yet)."""

	def test_requeues_old_pending_logs_but_not_fresh_ones(self):
		currency_exchange = get_or_create_currency_exchange("USD", "ZAR", 19.0)

		with patch("currency_audit.utils.frappe.enqueue"):
			stuck_log = make_log(currency_exchange, new_rate=19.0, old_rate=18.5)
			fresh_log = make_log(currency_exchange, new_rate=19.5, old_rate=19.0)

		frappe.db.set_value(
			"Currency Exchange Log",
			stuck_log.name,
			"creation",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-30),
		)

		with patch("currency_audit.matching.frappe.enqueue") as mock_enqueue:
			matching.retry_stuck_matches()

		requeued_names = [call.kwargs["log_name"] for call in mock_enqueue.call_args_list]
		self.assertIn(stuck_log.name, requeued_names)
		self.assertNotIn(fresh_log.name, requeued_names)
