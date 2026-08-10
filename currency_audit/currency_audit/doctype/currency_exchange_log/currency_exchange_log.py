# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

PROTECTED_FIELDS = (
	"entry_type",
	"currency_exchange",
	"from_currency",
	"to_currency",
	"old_rate",
	"new_rate",
	"changed_by",
	"changed_on",
)


class CurrencyExchangeLog(Document):
	"""Immutable audit record of a Currency Exchange rate change or baseline snapshot (F2).

	Created only by currency_audit.utils.create_log_entry, called from the change-detection
	hook (F1) or the baseline backfill patch (F6). Never edited or deleted once inserted,
	except by the background matching job (F4) appending rows to the `usage` child table
	and updating `matching_status`.
	"""

	def validate(self):
		"""Block edits to core fields after insert; only usage/matching_status may still change.

		One narrow, explicit exception: changed_by/changed_on on a Baseline entry may be
		corrected by currency_audit.patches.correct_baseline_attribution, which sets
		self.flags.allow_baseline_attribution_correction. That's a one-time data-quality
		fix for entries mis-attributed by a bug in the original backfill logic (it used
		to default to whoever ran the patch — always Administrator/CLI — instead of the
		source Currency Exchange record's own modified_by/modified). Nothing else may set
		this flag, and Change entries are never eligible: they're captured synchronously
		during the real edit's own request, so they were never at risk of the same bug.
		"""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		correction_allowed = self.entry_type == "Baseline" and self.flags.get(
			"allow_baseline_attribution_correction"
		)
		exempt_fields = {"changed_by", "changed_on"} if correction_allowed else set()

		for fieldname in PROTECTED_FIELDS:
			if fieldname in exempt_fields:
				continue
			if self.get(fieldname) != before.get(fieldname):
				frappe.throw(
					_("Currency Exchange Log entries are immutable; {0} cannot be changed.").format(fieldname)
				)

	def on_trash(self):
		"""Reject deletion outright; the log is a permanent audit trail (Principle 1)."""
		frappe.throw(_("Currency Exchange Log entries cannot be deleted; they are a permanent audit trail."))
