// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

frappe.query_reports["Currency Exchange Rate Changes"] = {
	"filters": [
		{
			"fieldname": "from_date",
			"label": __("From Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			"fieldname": "to_date",
			"label": __("To Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today(),
		},
		{
			"fieldname": "from_currency",
			"label": __("From Currency"),
			"fieldtype": "Link",
			"options": "Currency",
		},
		{
			"fieldname": "to_currency",
			"label": __("To Currency"),
			"fieldtype": "Link",
			"options": "Currency",
		},
		{
			"fieldname": "changed_by",
			"label": __("Changed By"),
			"fieldtype": "Link",
			"options": "User",
		},
		{
			"fieldname": "entry_type",
			"label": __("Entry Type"),
			"fieldtype": "Select",
			"options": ["", "Change", "Baseline", "Recovered"],
		},
		{
			"fieldname": "matching_status",
			"label": __("Matching Status"),
			"fieldtype": "Select",
			"options": ["", "Pending", "Completed", "Failed"],
		},
	]
};
