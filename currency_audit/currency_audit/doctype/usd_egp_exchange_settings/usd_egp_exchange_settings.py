# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class USDEGPExchangeSettings(Document):
	"""Single doctype holding the external rate API's host and key
	(currency_audit.usd_egp_rate_fetcher reads this). api_key is a Password field,
	encrypted at rest by Frappe."""
