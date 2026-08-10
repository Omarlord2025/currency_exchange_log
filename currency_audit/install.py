"""Install-time setup.

Per the CLAUDE.md performance checklist: "Filters that will run often (e.g. currency +
conversion_rate lookups) hit indexed columns." The F4 matching job runs exactly that
filter shape on every log entry, so composite indexes are added once at install.

F6's baseline backfill is also triggered from here, not just from patches.txt: `bench
install-app` marks every patch in patches.txt as already-applied without running it
(frappe.installer.install_app -> set_all_patches_as_completed), since a fresh app is
assumed to have no old data of its own to migrate. That assumption doesn't hold for
this patch — its job is to backfill against *existing* ERPNext data (Currency Exchange
records that predate the app) — so a fresh `bench install-app` would otherwise silently
skip it and leave the log empty. Calling it here as well covers that path; the
patches.txt entry still matters for sites that already had currency_audit installed
before this feature shipped and pick it up via `bench migrate`. Both call the same
idempotent function, so running it twice is safe.
"""

import frappe

INDEXED_FIELDS = (
	("Sales Invoice", ["currency", "conversion_rate", "company"]),
	("Purchase Invoice", ["currency", "conversion_rate", "company"]),
	("POS Invoice", ["currency", "conversion_rate", "company"]),
	("Purchase Receipt", ["currency", "conversion_rate", "company"]),
	("Delivery Note", ["currency", "conversion_rate", "company"]),
	("Payment Entry", ["paid_from_account_currency", "source_exchange_rate", "company"]),
	("Payment Entry", ["paid_to_account_currency", "target_exchange_rate", "company"]),
	("Journal Entry Account", ["account_currency", "exchange_rate"]),
	("Journal Entry", ["company"]),
)


def after_install():
	"""Add the F4 matching job's DB indexes and run the F6 baseline backfill."""
	add_matching_indexes()

	from currency_audit.patches.baseline_backfill import execute as run_baseline_backfill

	run_baseline_backfill()


def add_matching_indexes():
	"""Idempotently create composite indexes for every doctype the matching job filters on."""
	for doctype, fields in INDEXED_FIELDS:
		if frappe.db.table_exists(doctype):
			frappe.db.add_index(doctype, fields)
