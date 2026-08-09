# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CurrencyExchangeLogUsage(Document):
	"""Usage child table (F3): one row per transaction matched by the F4 background job
	as having used a Currency Exchange Log entry's old or new rate."""
