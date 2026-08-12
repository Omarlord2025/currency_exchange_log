"""F1.6 — detects transactions whose conversion rate was never recorded as a
Currency Exchange document at all.

Revisits the PRD's "API or scheduled-sync triggers" non-goal, which explicitly
flagged this for later: "if rates ever start syncing automatically, this needs
revisiting." Traced the actual mechanism in erpnext.setup.utils.get_exchange_rate —
when a transaction needs a rate and no matching Currency Exchange record exists,
ERPNext calls the external API configured in Currency Exchange Settings and stamps
that value straight onto the transaction being saved. No Currency Exchange document
is ever created for it — the rate is cached in Redis for 6 hours, not persisted to
the database. That makes it invisible to F1 (nothing to watch — no record was ever
created) and to F4 (nothing to match against — no Log entry carries that value).

Hooks into on_submit of the same seven doctypes F4 already reads. The first time a
submitted document's rate doesn't match any existing Currency Exchange record for
its currency -> company's base currency, an "External" Log entry is created for it
— from that point on, the normal F4 matching pipeline picks up every other
transaction that used the same rate, exactly as it would for a Change entry.
"""

import frappe
from frappe.utils import flt

from currency_audit import utils


def check_standard_doctype(doc, method=None):
	"""on_submit hook for Sales Invoice, Purchase Invoice, POS Invoice, Purchase
	Receipt, and Delivery Note: flag conversion_rate if never recorded.

	Args:
		doc (Document): the submitted document.
		method (str | None): the hook event name, unused (required by Frappe's hook signature).
	"""
	_check_rate(doc.currency, doc.conversion_rate, doc.company)


def check_payment_entry(doc, method=None):
	"""on_submit hook for Payment Entry: checks both sides of the transaction,
	since a payment can move money between two different currencies.

	Args:
		doc (Document): the submitted Payment Entry.
		method (str | None): the hook event name, unused.
	"""
	_check_rate(doc.paid_from_account_currency, doc.source_exchange_rate, doc.company)
	_check_rate(doc.paid_to_account_currency, doc.target_exchange_rate, doc.company)


def check_journal_entry(doc, method=None):
	"""on_submit hook for Journal Entry: checks each account row's own rate.

	Args:
		doc (Document): the submitted Journal Entry.
		method (str | None): the hook event name, unused.
	"""
	for row in doc.accounts:
		_check_rate(row.account_currency, row.exchange_rate, doc.company)


def _check_rate(currency, rate, company):
	"""Create an External Log entry the first time a rate is seen that was never
	recorded as a Currency Exchange document for this currency -> the company's
	base currency.

	Args:
		currency (str): the document/row's own currency.
		rate (float): the conversion/exchange rate actually used.
		company (str): the document's company, to derive the destination currency —
			a transaction never states its to_currency directly, only which company
			(and therefore base currency) it converts into (see Point C).
	"""
	if not currency or not rate:
		return

	base_currency = frappe.get_cached_value("Company", company, "default_currency")
	if not base_currency or currency == base_currency:
		# Same-currency transactions have conversion_rate == 1 trivially — no real
		# FX involved, nothing to check.
		return

	rate = flt(rate, 9)

	if _rate_already_recorded(currency, base_currency, rate):
		return

	already_flagged = frappe.db.exists(
		"Currency Exchange Log",
		{
			"entry_type": "External",
			"from_currency": currency,
			"to_currency": base_currency,
			"new_rate": rate,
		},
	)
	if already_flagged:
		return

	# No source Currency Exchange document exists — a frappe._dict stands in for
	# one, since create_log_entry only ever reads .name/.from_currency/.to_currency
	# off whatever it's given (see F6's baseline patch, which does the same for
	# frappe.get_all() rows).
	utils.create_log_entry(
		currency_exchange=frappe._dict(name=None, from_currency=currency, to_currency=base_currency),
		new_rate=rate,
		old_rate=None,
		entry_type="External",
	)


def _rate_already_recorded(currency, base_currency, rate):
	"""Whether this exact rate is already accounted for somewhere in the audit
	trail — either currently live in Currency Exchange, or historically in our own
	Log's old_rate/new_rate. Checking only the live Currency Exchange value isn't
	enough: a rate that was recorded as a real Change (e.g. 54 -> 55) and has since
	moved on (55 -> 56) is still a fully audited value — a later transaction using
	54 or 55 shouldn't be flagged "External" just because the live record moved.

	Args:
		currency (str): the document's own currency.
		base_currency (str): the company's base currency.
		rate (float): the rate to check for.

	Returns:
		bool: True if this rate is already represented in the audit trail somewhere.
	"""
	if frappe.db.exists(
		"Currency Exchange", {"from_currency": currency, "to_currency": base_currency, "exchange_rate": rate}
	):
		return True

	base_filters = {"from_currency": currency, "to_currency": base_currency, "entry_type": ["!=", "External"]}
	if frappe.db.exists("Currency Exchange Log", {**base_filters, "old_rate": rate}):
		return True
	if frappe.db.exists("Currency Exchange Log", {**base_filters, "new_rate": rate}):
		return True

	return False
