"""One-time historical catch-up for F1.6 (external rate detection).

F1.6's on_submit hooks only run for activity from here on — a transaction
submitted before this feature shipped, using a rate that was never recorded as a
Currency Exchange document, would otherwise stay invisible forever. This scans the
distinct (currency, rate, company) combinations already present across the seven
transaction doctypes and checks each once, exactly like external_rate_detection
would have on submit.

Grouped at the SQL level rather than looping per document — the number of distinct
rate combinations a business has ever used is small compared to transaction volume,
and _check_rate's own idempotency (already_recorded / already_flagged) means this
is safe to run more than once.
"""

import frappe

from currency_audit.external_rate_detection import _check_rate
from currency_audit.matching import STANDARD_DOCTYPES


def execute():
	"""Check every distinct historical (currency, rate, company) combination once."""
	for doctype in STANDARD_DOCTYPES:
		_check_distinct_combinations(doctype, "currency", "conversion_rate")

	_check_distinct_combinations("Payment Entry", "paid_from_account_currency", "source_exchange_rate")
	_check_distinct_combinations("Payment Entry", "paid_to_account_currency", "target_exchange_rate")

	_check_journal_entry_combinations()


def _check_distinct_combinations(doctype, currency_field, rate_field):
	"""Run _check_rate once for every distinct currency/rate/company combination a
	submitted doctype has ever used.

	Args:
		doctype (str): the transaction doctype to scan.
		currency_field (str): the doctype's own currency fieldname.
		rate_field (str): the doctype's own conversion/exchange rate fieldname.
	"""
	combinations = frappe.get_all(
		doctype,
		filters={"docstatus": 1},
		fields=[currency_field, rate_field, "company"],
		group_by=f"{currency_field}, {rate_field}, company",
	)
	for row in combinations:
		_check_rate(row.get(currency_field), row.get(rate_field), row.company)


def _check_journal_entry_combinations():
	"""Same as _check_distinct_combinations, but for Journal Entry: the rate lives
	per child-table row, and company is only known via the parent, so this needs
	its own join instead of a single grouped query."""
	combinations = frappe.get_all(
		"Journal Entry Account",
		filters={"docstatus": 1, "parenttype": "Journal Entry"},
		fields=["account_currency", "exchange_rate", "parent"],
		group_by="account_currency, exchange_rate, parent",
	)
	if not combinations:
		return

	companies = {
		p.name: p.company
		for p in frappe.get_all(
			"Journal Entry",
			filters={"name": ["in", list({c.parent for c in combinations})]},
			fields=["name", "company"],
		)
	}

	seen = set()
	for row in combinations:
		company = companies.get(row.parent)
		key = (row.account_currency, row.exchange_rate, company)
		if key in seen:
			continue
		seen.add(key)
		_check_rate(row.account_currency, row.exchange_rate, company)
