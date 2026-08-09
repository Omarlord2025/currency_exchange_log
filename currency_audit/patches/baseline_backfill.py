"""F6: one-time baseline backfill.

Currency Exchange has track_changes off, so Frappe never captured a diff history for
it — there's no hidden log of past edits sitting anywhere to recover. What's buildable
instead: snapshot every currently-active Currency Exchange record as a baseline log
entry (tagged "Baseline", not "Change" — no prior value is known), run the F4 matching
job against each one, and flip track_changes on for Currency Exchange going forward as
a second safety net alongside the custom hook (F1).
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from currency_audit import utils


def execute():
	"""Enable track_changes on Currency Exchange and create baseline logs for existing records."""
	_enable_track_changes()
	_create_baseline_logs()


def _enable_track_changes():
	"""Turn on track_changes for Currency Exchange as a safety net alongside the custom hook."""
	# track_changes is applied via a Property Setter, which overrides the doctype's
	# effective meta rather than the stored DocType row — check the meta, not the row.
	if frappe.get_meta("Currency Exchange", cached=False).track_changes:
		return

	make_property_setter("Currency Exchange", None, "track_changes", "1", "Check", for_doctype=True)


def _create_baseline_logs():
	"""Create a baseline Currency Exchange Log for every existing Currency Exchange record."""
	existing = frappe.get_all(
		"Currency Exchange",
		fields=["name", "from_currency", "to_currency", "exchange_rate"],
	)
	if not existing:
		return

	already_logged = set(
		frappe.get_all(
			"Currency Exchange Log",
			filters={"entry_type": "Baseline"},
			pluck="currency_exchange",
		)
	)

	for row in existing:
		if row.name in already_logged:
			continue
		utils.create_log_entry(
			currency_exchange=row,
			new_rate=row.exchange_rate,
			old_rate=None,
			entry_type="Baseline",
		)
