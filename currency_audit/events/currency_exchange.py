"""Hook handlers for the core (ERPNext) Currency Exchange doctype.

Kept thin per CLAUDE.md: this only detects whether the rate actually changed and
delegates the log creation to currency_audit.utils.
"""

from frappe.utils import flt

from currency_audit import utils


def on_update(doc, method=None):
	"""F1: compare old vs. new exchange_rate on save; log only on an actual change.

	Args:
		doc (Document): the Currency Exchange document that was just saved.
		method (str | None): the hook event name, unused (required by Frappe's hook signature).
	"""
	before = doc.get_doc_before_save()
	if not before:
		return

	if flt(before.exchange_rate, doc.precision("exchange_rate")) == flt(doc.exchange_rate, doc.precision("exchange_rate")):
		return

	utils.create_log_entry(
		currency_exchange=doc,
		new_rate=doc.exchange_rate,
		old_rate=before.exchange_rate,
		entry_type="Change",
	)
