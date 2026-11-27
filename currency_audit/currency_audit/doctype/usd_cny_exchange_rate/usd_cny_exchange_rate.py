# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class USDCNYExchangeRate(Document):
	"""One row per day's USD->CNY market rate, fetched from an external API
	(currency_audit.usd_cny_rate_fetcher). Created via the 60-day backfill or the
	daily 9am job; one row per calendar date, enforced at the application level
	(see usd_cny_rate_fetcher.store_rate) since Frappe doesn't support a unique
	constraint on a Date field. No deletion policy for now — kept indefinitely."""
