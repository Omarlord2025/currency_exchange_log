"""Install-time setup.

Per the CLAUDE.md performance checklist: "Filters that will run often (e.g. currency +
conversion_rate lookups) hit indexed columns." The F4 matching job runs exactly that
filter shape on every log entry, so composite indexes are added once at install.
"""

import frappe

INDEXED_FIELDS = (
	("Sales Invoice", ["currency", "conversion_rate"]),
	("Purchase Invoice", ["currency", "conversion_rate"]),
	("POS Invoice", ["currency", "conversion_rate"]),
	("Purchase Receipt", ["currency", "conversion_rate"]),
	("Delivery Note", ["currency", "conversion_rate"]),
	("Payment Entry", ["paid_from_account_currency", "source_exchange_rate"]),
	("Payment Entry", ["paid_to_account_currency", "target_exchange_rate"]),
	("Journal Entry Account", ["account_currency", "exchange_rate"]),
)


def after_install():
	"""Add the composite DB indexes the F4 matching job's filters rely on."""
	add_matching_indexes()


def add_matching_indexes():
	"""Idempotently create composite indexes for every doctype the matching job filters on."""
	for doctype, fields in INDEXED_FIELDS:
		if frappe.db.table_exists(doctype):
			frappe.db.add_index(doctype, fields)
