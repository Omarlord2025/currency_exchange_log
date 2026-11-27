"""The audit trail must never block the documents it audits (PRD Principle 5).

The `usage` child table points at each matched transaction with a Dynamic Link, which
is enough for Frappe's link checker to treat that transaction as "still linked" and
refuse to delete it. hooks.py's `ignore_links_on_delete` is what suppresses that; these
tests pin the behaviour down so it can't silently regress.

Each "no longer blocks" assertion is paired with a control that reproduces the pre-fix
behaviour by hiding this app's entries from the hook — otherwise the test would pass
just as happily on a site where the dynamic link map never contained the doctype under
test, proving nothing.
"""

from unittest.mock import patch

import frappe
from frappe.model.delete_doc import get_dynamic_linked_docs, get_linked_docs
from frappe.model.dynamic_links import invalidate_distinct_link_doctypes
from frappe.tests.utils import FrappeTestCase, change_settings

from currency_audit.test_helpers import get_or_create_currency_exchange, make_log

AUDIT_DOCTYPES = ("Currency Exchange Log", "Currency Exchange Log Usage")


def _hooks_hiding_audit_doctypes():
	"""Build a frappe.get_hooks stand-in that drops this app's ignore_links_on_delete entries.

	Every other hook is delegated to the real implementation untouched, so patching
	frappe.get_hooks with this changes only the one answer under test.

	Returns:
		callable: drop-in `side_effect` for unittest.mock.patch on frappe.get_hooks.
	"""
	real_get_hooks = frappe.get_hooks

	def fake_get_hooks(hook=None, *args, **kwargs):
		value = real_get_hooks(hook, *args, **kwargs)
		if hook == "ignore_links_on_delete":
			return [doctype for doctype in value if doctype not in AUDIT_DOCTYPES]
		return value

	return fake_get_hooks


def _audit_blockers(links):
	"""Filter a get_linked_docs/get_dynamic_linked_docs result down to this app's entries.

	Args:
		links (list[dict]): rows as returned by frappe.model.delete_doc's link lookups.

	Returns:
		list[dict]: only those whose reference_doctype belongs to currency_audit.
	"""
	return [link for link in links if link["reference_doctype"] in AUDIT_DOCTYPES]


def add_usage_row(log, doc, currency, amount):
	"""Record `doc` in a log's usage table the way the F4 matching job would.

	Args:
		log (Document): the Currency Exchange Log to append to.
		doc (Document): the transaction to reference.
		currency (str): currency to record on the row.
		amount (float): amount to record on the row.

	Returns:
		Document: the reloaded, saved log.
	"""
	# Re-fetch before appending, exactly as run_matching_job does. A freshly inserted
	# log's in-memory doc still carries old_rate=None where the stored row reads back
	# 0.0, and the immutability check (F2) compares the two literally.
	log.reload()
	log.append(
		"usage",
		{
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"posting_date": doc.posting_date,
			"rate_type": "New Rate",
			"currency": currency,
			"amount": amount,
			"match_confidence": "Confirmed",
		},
	)
	# Same flag the matching job sets — `usage` is otherwise rejected as a hand-edit
	# (see CurrencyExchangeLog._reject_matching_result_edits).
	log.flags.allow_matching_update = True
	log.save(ignore_permissions=True)

	# get_dynamic_link_map only lists a doctype the child table has actually been seen
	# pointing at, and that lookup is cached in Redis for 12h. Without this the control
	# assertions could miss a link that genuinely exists in this transaction.
	invalidate_distinct_link_doctypes("Currency Exchange Log Usage", "reference_doctype", doc.doctype)
	return log


class TestUsageTableDoesNotBlockDeletion(FrappeTestCase):
	"""A transaction recorded in a log's `usage` table stays freely deletable. Sales
	Invoice stands in for all seven matched doctypes — they all reach the link checker
	through the same single Dynamic Link field on the child table."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice

		cls.rate = 63.75
		with patch("currency_audit.utils.frappe.enqueue"):
			cls.invoice = create_sales_invoice(
				customer="_Test Customer USD",
				debit_to="_Test Receivable USD - _TC",
				currency="USD",
				conversion_rate=cls.rate,
			)
			currency_exchange = get_or_create_currency_exchange("USD", "EGP", cls.rate)
			log = make_log(currency_exchange, new_rate=cls.rate)

		cls.log = add_usage_row(log, cls.invoice, "USD", cls.invoice.grand_total)

	def test_control_usage_row_would_block_delete_without_the_hook(self):
		with patch("frappe.get_hooks", side_effect=_hooks_hiding_audit_doctypes()):
			blockers = _audit_blockers(get_dynamic_linked_docs(self.invoice, "Delete"))

		self.assertTrue(
			blockers,
			msg="control failed: the usage row isn't reaching the link checker at all, "
			"so the assertion below would pass vacuously",
		)
		self.assertEqual(blockers[0]["reference_docname"], self.log.name)

	def test_usage_row_does_not_block_delete(self):
		self.assertEqual(_audit_blockers(get_dynamic_linked_docs(self.invoice, "Delete")), [])

	def test_usage_row_never_blocked_cancel_even_without_the_hook(self):
		"""Cancel was never affected, before or after the fix — worth pinning down so
		nobody "fixes" it twice.

		Frappe only counts a *submitted* linked row when checking Cancel
		(get_dynamic_linked_docs: `method == "Cancel" and DocStatus(...).is_submitted()`).
		Currency Exchange Log isn't submittable, so its usage rows sit at docstatus 0 and
		never qualify. The Desk message that surfaced this bug reads "Cannot delete or
		cancel because ..." for both operations, which is what made delete-only look like
		delete-and-cancel.
		"""
		self.assertFalse(frappe.get_meta("Currency Exchange Log").is_submittable)

		with patch("frappe.get_hooks", side_effect=_hooks_hiding_audit_doctypes()):
			self.assertEqual(_audit_blockers(get_dynamic_linked_docs(self.invoice, "Cancel")), [])


class TestCancelledJournalEntryCanBeDeleted(FrappeTestCase):
	"""End-to-end reproduction of the reported failure: a matched Journal Entry is
	cancelled, then deleted from the list view."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from erpnext.accounts.doctype.journal_entry.test_journal_entry import make_journal_entry

		cls.rate = 64.25
		with patch("currency_audit.utils.frappe.enqueue"):
			jv = make_journal_entry("_Test Bank USD - _TC", "_Test Cash - _TC", 100, exchange_rate=1, save=False)
			jv.accounts[0].exchange_rate = cls.rate
			# The contra leg is in company currency, so its company-currency amount must
			# be rebalanced against the USD leg's new equivalent (100 * rate).
			jv.accounts[1].credit_in_account_currency = 100 * cls.rate
			jv.insert()
			jv.submit()

			currency_exchange = get_or_create_currency_exchange("USD", "EGP", cls.rate)
			log = make_log(currency_exchange, new_rate=cls.rate)

		cls.journal_entry = jv
		cls.log = add_usage_row(log, jv, "USD", 100)

	def test_control_would_block_delete_without_the_hook(self):
		with patch("frappe.get_hooks", side_effect=_hooks_hiding_audit_doctypes()):
			blockers = _audit_blockers(get_dynamic_linked_docs(self.journal_entry, "Delete"))

		self.assertTrue(blockers, msg="control failed: the usage row isn't reaching the link checker")
		self.assertEqual(blockers[0]["reference_doctype"], "Currency Exchange Log")

	@change_settings("Accounts Settings", {"delete_linked_ledger_entries": 1})
	def test_cancel_then_delete_succeeds(self):
		"""The setting is ERPNext's own precondition for deleting any ledger-bearing
		transaction, nothing to do with this app: frappe.model.delete_doc runs the
		document's `on_trash` (where ERPNext clears the GL entries) before it runs the
		link checks, so without it GL Entry blocks the delete no matter what this app
		does. Turning it on here is what lets the test reach the part that is ours —
		that the Currency Exchange Log usage row no longer stands in the way. ERPNext's
		own tests set it the same way.
		"""
		self.journal_entry.reload()
		self.journal_entry.cancel()
		self.assertEqual(self.journal_entry.docstatus, 2)

		frappe.delete_doc("Journal Entry", self.journal_entry.name)
		self.assertFalse(frappe.db.exists("Journal Entry", self.journal_entry.name))

		# The audit trail itself outlives the transaction it was auditing (Principle 1).
		self.assertTrue(frappe.db.exists("Currency Exchange Log", self.log.name))


class TestLogDoesNotBlockCurrencyExchangeDeletion(FrappeTestCase):
	"""The log's own static `currency_exchange` Link must not lock the source record
	either — same hook, the static half of it (get_linked_docs)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.currency_exchange = get_or_create_currency_exchange("USD", "SGD", 1.35)
		with patch("currency_audit.utils.frappe.enqueue"):
			cls.log = make_log(cls.currency_exchange, new_rate=1.35)

	def test_control_static_link_would_block_delete_without_the_hook(self):
		with patch("frappe.get_hooks", side_effect=_hooks_hiding_audit_doctypes()):
			blockers = _audit_blockers(get_linked_docs(self.currency_exchange, "Delete"))

		self.assertTrue(
			blockers,
			msg="control failed: the log's currency_exchange Link isn't reaching the link "
			"checker, so the assertion below would pass vacuously",
		)

	def test_static_link_does_not_block_delete(self):
		self.assertEqual(_audit_blockers(get_linked_docs(self.currency_exchange, "Delete")), [])


class TestLogItselfStaysImmutable(FrappeTestCase):
	"""Unblocking the audited documents must not unblock the audit record itself —
	Currency Exchange Log deletion is still refused by its own on_trash (F2)."""

	def test_log_still_cannot_be_deleted(self):
		currency_exchange = get_or_create_currency_exchange("USD", "CHF", 0.88)
		with patch("currency_audit.utils.frappe.enqueue"):
			log = make_log(currency_exchange, new_rate=0.88)

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Currency Exchange Log", log.name)
