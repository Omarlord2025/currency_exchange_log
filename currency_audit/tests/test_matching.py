"""Tests for currency_audit.matching (F4 background matching job).

Exercises the shared standard-doctype query pattern once (via Sales Invoice — the
same code path covers Purchase Invoice / POS Invoice / Purchase Receipt / Delivery
Note per PRD F4), plus the two doctypes with their own matching logic: Payment Entry
(rate per side) and Journal Entry (rate per child-table row).
"""

from unittest.mock import patch

import frappe
from frappe.model.document import Document
from frappe.tests.utils import FrappeTestCase

from currency_audit import matching
from currency_audit.test_helpers import get_or_create_currency_exchange, make_log

# Note: deliberately no `test_dependencies` here — ERPNext's own recursive dependency
# walk (frappe.test_runner.get_dependencies) hits a "Payment Gateway" doctype that
# isn't installed on this site and aborts test collection entirely. The multi-currency
# fixtures these tests need (_Test Customer USD, _Test Receivable/Bank USD - _TC, etc.)
# are standard ERPNext test records already present on this site.

# companies=[] disables the company/to_currency scoping (see TestCompanyScoping), and
# (None, None) disables the date-window confidence scoring (see TestRateWindows /
# TestConfidence) — so these tests can focus purely on the currency+rate matching
# mechanics without either extra dimension in play.
NO_COMPANY_SCOPE = []
OPEN_WINDOW = (None, None)


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
		rows = matching._match_standard_doctype(
			"Sales Invoice", "USD", self.rate, "New Rate", NO_COMPANY_SCOPE, OPEN_WINDOW
		)
		names = [r["reference_name"] for r in rows]
		self.assertIn(self.invoice.name, names)

		row = next(r for r in rows if r["reference_name"] == self.invoice.name)
		self.assertEqual(row["reference_doctype"], "Sales Invoice")
		self.assertEqual(row["currency"], "USD")
		self.assertEqual(row["amount"], self.invoice.grand_total)
		self.assertEqual(row["rate_type"], "New Rate")

	def test_does_not_match_a_different_rate(self):
		rows = matching._match_standard_doctype(
			"Sales Invoice", "USD", self.rate + 1, "New Rate", NO_COMPANY_SCOPE, OPEN_WINDOW
		)
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
		rows = matching._match_payment_entry("USD", self.rate, "Old Rate", NO_COMPANY_SCOPE, OPEN_WINDOW)
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
		rows = matching._match_journal_entry("USD", self.rate, "New Rate", NO_COMPANY_SCOPE, OPEN_WINDOW)
		names = [r["reference_name"] for r in rows]
		self.assertIn(self.journal_entry.name, names)


class TestCompanyScoping(FrappeTestCase):
	"""Point C: a document's conversion_rate never states its to_currency — only which
	company (and therefore base currency) it converts into. Without scoping by that,
	a USD→EGP and a USD→INR log sharing a rate value both falsely claim any USD
	document. _Test Company's base currency is INR, _Test Company 2's is EUR."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.rate = 73.25
		cls.invoice = create_sales_invoice(
			company="_Test Company",
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=cls.rate,
		)

	def test_matches_when_company_scope_includes_the_invoice_company(self):
		rows = matching._match_standard_doctype(
			"Sales Invoice", "USD", self.rate, "New Rate", ["_Test Company"], OPEN_WINDOW
		)
		self.assertIn(self.invoice.name, [r["reference_name"] for r in rows])

	def test_does_not_match_when_company_scope_excludes_the_invoice_company(self):
		# Same currency, same rate value — only the target currency (and therefore
		# company) differs. This is exactly the false-positive Point C described.
		rows = matching._match_standard_doctype(
			"Sales Invoice", "USD", self.rate, "New Rate", ["_Test Company 2"], OPEN_WINDOW
		)
		self.assertNotIn(self.invoice.name, [r["reference_name"] for r in rows])

	def test_empty_company_scope_falls_back_to_unrestricted(self):
		# companies=[] means "couldn't determine any" (e.g. an unrecognised currency),
		# not "match nothing" — the filter must be skipped, not applied as empty IN().
		rows = matching._match_standard_doctype("Sales Invoice", "USD", self.rate, "New Rate", [], OPEN_WINDOW)
		self.assertIn(self.invoice.name, [r["reference_name"] for r in rows])


class TestCompaniesWithCurrency(FrappeTestCase):
	def test_returns_companies_matching_the_currency(self):
		companies = matching._companies_with_currency("INR")
		self.assertIn("_Test Company", companies)

	def test_returns_empty_list_for_an_unused_currency(self):
		companies = matching._companies_with_currency("XTS")
		self.assertEqual(companies, [])


class TestConfidence(FrappeTestCase):
	def test_confirmed_when_inside_the_window(self):
		window = (frappe.utils.getdate("2024-01-10"), frappe.utils.getdate("2024-02-10"))
		self.assertEqual(matching._confidence(frappe.utils.getdate("2024-01-20"), window), "Confirmed")

	def test_confirmed_on_window_boundaries_inclusive(self):
		# changed_on only has day precision here, so the transition day itself can't
		# be split between the old and new rate — both ends are inclusive.
		start, end = frappe.utils.getdate("2024-01-10"), frappe.utils.getdate("2024-02-10")
		self.assertEqual(matching._confidence(start, (start, end)), "Confirmed")
		self.assertEqual(matching._confidence(end, (start, end)), "Confirmed")

	def test_possible_when_outside_the_window(self):
		window = (frappe.utils.getdate("2024-01-10"), frappe.utils.getdate("2024-02-10"))
		self.assertEqual(matching._confidence(frappe.utils.getdate("2024-03-01"), window), "Possible")
		self.assertEqual(matching._confidence(frappe.utils.getdate("2023-12-01"), window), "Possible")

	def test_possible_when_posting_date_is_missing(self):
		self.assertEqual(matching._confidence(None, (None, None)), "Possible")

	def test_confirmed_with_an_open_ended_window(self):
		# No prior/next sibling entry — an unbounded side of the window imposes no
		# constraint on that side.
		self.assertEqual(matching._confidence(frappe.utils.getdate("2020-01-01"), (None, None)), "Confirmed")


class TestRateWindows(FrappeTestCase):
	"""Point 10/13: a rate value can recur (50 -> 52 -> 50 -> 52) — these windows are
	what let match_confidence tell those occurrences apart by when they were active."""

	def test_windows_for_a_cycling_rate(self):
		currency_exchange = get_or_create_currency_exchange("USD", "BHD", 52.0)
		t1, t2, t3 = (
			frappe.utils.get_datetime("2024-01-01 00:00:00"),
			frappe.utils.get_datetime("2024-02-01 00:00:00"),
			frappe.utils.get_datetime("2024-03-01 00:00:00"),
		)
		with patch("currency_audit.utils.frappe.enqueue"):
			log_a = make_log(currency_exchange, old_rate=50.0, new_rate=52.0)
			frappe.db.set_value("Currency Exchange Log", log_a.name, "changed_on", t1, update_modified=False)
			log_b = make_log(currency_exchange, old_rate=52.0, new_rate=50.0)
			frappe.db.set_value("Currency Exchange Log", log_b.name, "changed_on", t2, update_modified=False)
			log_c = make_log(currency_exchange, old_rate=50.0, new_rate=52.0)
			frappe.db.set_value("Currency Exchange Log", log_c.name, "changed_on", t3, update_modified=False)

		log_a.reload()
		log_b.reload()
		log_c.reload()

		windows_a = matching._rate_windows(log_a)
		self.assertEqual(windows_a["New Rate"], (frappe.utils.getdate(t1), frappe.utils.getdate(t2)))

		windows_b = matching._rate_windows(log_b)
		self.assertEqual(windows_b["Old Rate"], (frappe.utils.getdate(t1), frappe.utils.getdate(t2)))
		self.assertEqual(windows_b["New Rate"], (frappe.utils.getdate(t2), frappe.utils.getdate(t3)))

		windows_c = matching._rate_windows(log_c)
		self.assertEqual(windows_c["Old Rate"], (frappe.utils.getdate(t2), frappe.utils.getdate(t3)))
		# Latest entry for this record — open-ended on the right.
		self.assertEqual(windows_c["New Rate"], (frappe.utils.getdate(t3), None))

	def test_first_entry_has_no_prior_window(self):
		currency_exchange = get_or_create_currency_exchange("USD", "OMR", 0.385)
		with patch("currency_audit.utils.frappe.enqueue"):
			log = make_log(currency_exchange, old_rate=0.38, new_rate=0.385)

		windows = matching._rate_windows(log)
		self.assertIsNone(windows["Old Rate"][0])


class TestRunMatchingJobConfidence(FrappeTestCase):
	"""End-to-end: the 50 -> 52 -> 50 -> 52 scenario, matched against real invoices."""

	def test_tags_confirmed_and_possible_correctly(self):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		currency_exchange = get_or_create_currency_exchange("USD", "KWD", 52.0)
		t1, t2, t3 = (
			frappe.utils.get_datetime("2024-01-01 00:00:00"),
			frappe.utils.get_datetime("2024-02-01 00:00:00"),
			frappe.utils.get_datetime("2024-03-01 00:00:00"),
		)
		with patch("currency_audit.utils.frappe.enqueue"):
			log_a = make_log(currency_exchange, old_rate=50.0, new_rate=52.0)
			frappe.db.set_value("Currency Exchange Log", log_a.name, "changed_on", t1, update_modified=False)
			log_b = make_log(currency_exchange, old_rate=52.0, new_rate=50.0)
			frappe.db.set_value("Currency Exchange Log", log_b.name, "changed_on", t2, update_modified=False)
			log_c = make_log(currency_exchange, old_rate=50.0, new_rate=52.0)
			frappe.db.set_value("Currency Exchange Log", log_c.name, "changed_on", t3, update_modified=False)

		# Genuinely used 52 during log_a's window (Jan) — should be Confirmed there.
		clean_invoice = create_sales_invoice(
			company="_Test Company",
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=52.0,
			posting_date="2024-01-15",
		)
		# Claims 52 but is dated inside log_b's window (Feb, when the rate was
		# actually 50) — a genuine inconsistency, should be Possible under log_a.
		odd_invoice = create_sales_invoice(
			company="_Test Company",
			customer="_Test Customer USD",
			debit_to="_Test Receivable USD - _TC",
			currency="USD",
			conversion_rate=52.0,
			posting_date="2024-02-15",
		)

		matching.run_matching_job(log_a.name)
		log_a.reload()
		usage_by_name = {row.reference_name: row for row in log_a.usage}

		self.assertEqual(usage_by_name[clean_invoice.name].match_confidence, "Confirmed")
		self.assertEqual(usage_by_name[odd_invoice.name].match_confidence, "Possible")


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
		# _Test Company's base currency is INR, matching this log's to_currency.
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

	def test_retries_on_a_save_conflict_then_succeeds(self):
		# Reproduces the real production failure: two workers processing the same log
		# concurrently (e.g. the retry sweep re-queuing a job that's still mid-save on
		# a large real transaction table) — the loser's save() hits
		# Document.check_if_latest()'s optimistic-lock check and raises
		# TimestampMismatchError. The job must retry and recover, not just fail.
		currency_exchange = get_or_create_currency_exchange("USD", "AUD", 1.55)
		with patch("currency_audit.utils.frappe.enqueue"):
			log = make_log(currency_exchange, new_rate=1.55, old_rate=1.5)

		original_save = Document.save
		failures_left = 2

		def flaky_save(self, *args, **kwargs):
			nonlocal failures_left
			if self.doctype == "Currency Exchange Log" and failures_left > 0:
				failures_left -= 1
				raise frappe.TimestampMismatchError
			return original_save(self, *args, **kwargs)

		with patch("currency_audit.matching.time.sleep"), patch.object(Document, "save", flaky_save):
			matching.run_matching_job(log.name)

		log.reload()
		self.assertEqual(log.matching_status, "Completed")
		self.assertEqual(failures_left, 0)

	def test_gives_up_and_marks_failed_after_max_attempts(self):
		currency_exchange = get_or_create_currency_exchange("USD", "CAD", 1.35)
		with patch("currency_audit.utils.frappe.enqueue"):
			log = make_log(currency_exchange, new_rate=1.35, old_rate=1.3)

		with (
			patch("currency_audit.matching.time.sleep"),
			patch.object(Document, "save", side_effect=frappe.TimestampMismatchError),
			patch("currency_audit.matching.frappe.log_error") as mock_log_error,
		):
			matching.run_matching_job(log.name)

		mock_log_error.assert_called_once()
		self.assertEqual(frappe.db.get_value("Currency Exchange Log", log.name, "matching_status"), "Failed")


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

	def test_requeue_is_deduplicated_by_job_id(self):
		currency_exchange = get_or_create_currency_exchange("USD", "SGD", 1.4)
		with patch("currency_audit.utils.frappe.enqueue"):
			stuck_log = make_log(currency_exchange, new_rate=1.4, old_rate=1.35)
		frappe.db.set_value(
			"Currency Exchange Log",
			stuck_log.name,
			"creation",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-30),
		)

		with patch("currency_audit.matching.frappe.enqueue") as mock_enqueue:
			matching.retry_stuck_matches()

		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs.get("job_id"), matching.matching_job_id(stuck_log.name))
		self.assertTrue(kwargs.get("deduplicate"))
