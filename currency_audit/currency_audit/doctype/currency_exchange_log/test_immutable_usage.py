"""The matched-transactions table is evidence, and has to be as immutable as the rates.

`usage` and `matching_status` are the two fields that legitimately change after insert,
so PROTECTED_FIELDS can't cover them — the F4 background job is their only writer, and
announces itself with the `allow_matching_update` flag. Everything else is refused.

These tests all run as Administrator, which is the point: Administrator bypasses role
permissions entirely, so a guard that only lived in the doctype's permission rows would
not hold here.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import matching
from currency_audit.test_helpers import get_or_create_currency_exchange, make_log


def _usage_row(reference_name, amount=100):
	"""Build a usage child-table row dict.

	Args:
		reference_name (str): Sales Invoice to reference. Must be a document that really
			exists — `reference_name` is a Dynamic Link, so Frappe rejects a made-up name
			before this app's own guard ever gets a look at the save.
		amount (float): amount to record on the row.

	Returns:
		dict: a Currency Exchange Log Usage row.
	"""
	return {
		"reference_doctype": "Sales Invoice",
		"reference_name": reference_name,
		"posting_date": frappe.utils.nowdate(),
		"rate_type": "New Rate",
		"currency": "USD",
		"amount": amount,
		"match_confidence": "Confirmed",
	}


class TestUsageTableIsImmutable(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.currency_exchange = get_or_create_currency_exchange("USD", "NOK", 10.5)
		with patch("currency_audit.utils.frappe.enqueue"):
			cls.invoice = create_sales_invoice(
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=10.5,
			)
			cls.other_invoice = create_sales_invoice(
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=10.5,
			)

	def make_log_with_usage(self):
		"""Insert a log carrying one usage row, the way the matching job would.

		Returns:
			Document: the saved log, reloaded.
		"""
		with patch("currency_audit.utils.frappe.enqueue"):
			log = make_log(self.currency_exchange, new_rate=10.5)
		log.reload()
		log.append("usage", _usage_row(self.invoice.name))
		log.flags.allow_matching_update = True
		log.save(ignore_permissions=True)
		log.reload()
		return log

	def test_running_as_administrator(self):
		# Guards the premise of every other test in this module.
		self.assertEqual(frappe.session.user, "Administrator")

	def test_cannot_add_a_usage_row(self):
		log = self.make_log_with_usage()
		log.append("usage", _usage_row(self.other_invoice.name))
		with self.assertRaises(frappe.ValidationError):
			log.save(ignore_permissions=True)

	def test_cannot_remove_a_usage_row(self):
		log = self.make_log_with_usage()
		log.set("usage", [])
		with self.assertRaises(frappe.ValidationError):
			log.save(ignore_permissions=True)

	def test_cannot_edit_a_usage_row_in_place(self):
		log = self.make_log_with_usage()
		log.usage[0].amount = 999999
		with self.assertRaises(frappe.ValidationError):
			log.save(ignore_permissions=True)

	def test_cannot_repoint_a_usage_row_at_another_document(self):
		log = self.make_log_with_usage()
		log.usage[0].reference_name = self.other_invoice.name
		with self.assertRaises(frappe.ValidationError):
			log.save(ignore_permissions=True)

	def test_cannot_change_matching_status(self):
		log = self.make_log_with_usage()
		log.matching_status = "Failed"
		with self.assertRaises(frappe.ValidationError):
			log.save(ignore_permissions=True)

	def test_cannot_delete_a_single_usage_row_directly(self):
		# frappe.client.delete has no child-table guard, so this path never loads the
		# parent and would otherwise skip the check above entirely.
		log = self.make_log_with_usage()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Currency Exchange Log Usage", log.usage[0].name)

	def test_child_row_write_through_client_api_is_rejected(self):
		# frappe.client.set_value on a child row loads the parent and saves it, so the
		# parent's guard is what catches this one.
		from frappe.client import set_value

		log = self.make_log_with_usage()
		with self.assertRaises(frappe.ValidationError):
			set_value("Currency Exchange Log Usage", log.usage[0].name, "amount", 4242)

	def test_a_save_that_changes_nothing_is_still_allowed(self):
		# The guard must not turn every unrelated save into an error — correct_baseline_attribution
		# saves the doc without touching usage, and has to keep working.
		log = self.make_log_with_usage()
		log.save(ignore_permissions=True)
		self.assertEqual(len(log.usage), 1)

	def test_usage_field_is_read_only_in_the_ui(self):
		self.assertTrue(frappe.get_meta("Currency Exchange Log").get_field("usage").read_only)

	def test_no_role_has_write_permission(self):
		roles = [p.role for p in frappe.get_meta("Currency Exchange Log").permissions if p.write]
		self.assertEqual(roles, [])


class TestMatchingJobStillWrites(FrappeTestCase):
	"""The guard must not lock out the one writer that is supposed to get through."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.rate = 65.5
		with patch("currency_audit.utils.frappe.enqueue"):
			cls.invoice = create_sales_invoice(
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=cls.rate,
			)
			# USD->INR, not USD->EGP: run_matching_job scopes matches to companies whose
			# base currency is the log's to_currency, and create_sales_invoice's default
			# company (_Test Company) is INR. An EGP log correctly finds nothing here.
			currency_exchange = get_or_create_currency_exchange("USD", "INR", cls.rate)
			cls.log = make_log(currency_exchange, new_rate=cls.rate)

	def test_run_matching_job_can_still_fill_the_usage_table(self):
		matching.run_matching_job(self.log.name)

		self.log.reload()
		self.assertEqual(self.log.matching_status, "Completed")
		self.assertIn(self.invoice.name, [row.reference_name for row in self.log.usage])

	def test_run_matching_job_can_rewrite_an_existing_usage_table(self):
		# The job replaces `usage` wholesale on every run, so it must be able to remove
		# rows as well as add them — the exact operation the guard rejects from anyone else.
		matching.run_matching_job(self.log.name)
		matching.run_matching_job(self.log.name)

		self.log.reload()
		self.assertEqual(self.log.matching_status, "Completed")
		names = [row.reference_name for row in self.log.usage]
		self.assertEqual(len(names), len(set(names)), msg="rows were appended instead of replaced")
