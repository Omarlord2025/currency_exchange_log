# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CurrencyExchangeLogUsage(Document):
	"""Usage child table (F3): one row per transaction matched by the F4 background job
	as having used a Currency Exchange Log entry's old or new rate."""

	def on_trash(self):
		"""Reject deleting a single matched-transaction row on its own.

		frappe.client.delete has no child-table guard, so
		frappe.delete_doc("Currency Exchange Log Usage", <row name>) would otherwise drop
		one row of the evidence without ever loading the parent — meaning the parent's
		immutability check never gets a chance to run.

		The F4 matching job doesn't come through here: Document.update_child_table clears
		replaced rows with a direct DELETE query, which fires no controller hooks.
		"""
		frappe.throw(_("Matched Transactions are part of a permanent audit trail and cannot be deleted."))
