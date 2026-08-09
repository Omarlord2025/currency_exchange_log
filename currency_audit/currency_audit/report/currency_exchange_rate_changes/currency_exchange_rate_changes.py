# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""F7: lightweight dashboard over Currency Exchange Log — who/when/old->new rate,
filterable, with each row linking back to its log entry for drill-through to the
matched transactions already recorded there by the F4 background job.
"""

import frappe
from frappe import _


def execute(filters=None):
	"""Report entry point required by the Script Report framework.

	Args:
		filters (dict | None): report filters from currency_exchange_rate_changes.js.

	Returns:
		tuple[list[dict], list[dict]]: (columns, data) for the report grid.
	"""
	columns = get_columns()
	data = get_data(filters or {})
	return columns, data


def get_columns():
	"""Define the report's column layout."""
	return [
		{
			"label": _("Log"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Currency Exchange Log",
			"width": 110,
		},
		{"label": _("Changed On"), "fieldname": "changed_on", "fieldtype": "Datetime", "width": 160},
		{"label": _("Entry Type"), "fieldname": "entry_type", "fieldtype": "Data", "width": 90},
		{"label": _("From"), "fieldname": "from_currency", "fieldtype": "Link", "options": "Currency", "width": 80},
		{"label": _("To"), "fieldname": "to_currency", "fieldtype": "Link", "options": "Currency", "width": 80},
		{"label": _("Old Rate"), "fieldname": "old_rate", "fieldtype": "Float", "precision": 9, "width": 110},
		{"label": _("New Rate"), "fieldname": "new_rate", "fieldtype": "Float", "precision": 9, "width": 110},
		{"label": _("Changed By"), "fieldname": "changed_by", "fieldtype": "Link", "options": "User", "width": 150},
		{"label": _("Matching Status"), "fieldname": "matching_status", "fieldtype": "Data", "width": 110},
		{"label": _("Matched Transactions"), "fieldname": "usage_count", "fieldtype": "Int", "width": 140},
	]


def get_data(filters):
	"""Fetch log rows with a matched-transaction count per log, applying report filters.

	Args:
		filters (dict): from_currency, to_currency, changed_by, entry_type, matching_status,
			and an optional changed_on date range (from_date/to_date).

	Returns:
		list[dict]: one row per Currency Exchange Log, newest first.
	"""
	query_filters = {}
	for fieldname in ("from_currency", "to_currency", "changed_by", "entry_type", "matching_status"):
		if filters.get(fieldname):
			query_filters[fieldname] = filters[fieldname]

	if filters.get("from_date") and filters.get("to_date"):
		query_filters["changed_on"] = ["between", [filters["from_date"], filters["to_date"]]]

	logs = frappe.get_all(
		"Currency Exchange Log",
		filters=query_filters,
		fields=[
			"name",
			"changed_on",
			"entry_type",
			"from_currency",
			"to_currency",
			"old_rate",
			"new_rate",
			"changed_by",
			"matching_status",
		],
		order_by="changed_on desc",
	)
	if not logs:
		return []

	usage_counts = dict(
		frappe.get_all(
			"Currency Exchange Log Usage",
			filters={"parent": ["in", [log.name for log in logs]]},
			group_by="parent",
			fields=["parent", "count(name) as count"],
			as_list=True,
		)
	)

	for log in logs:
		log["usage_count"] = usage_counts.get(log.name, 0)

	return logs
