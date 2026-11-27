# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class USDEGPExchangeRate(Document):
	"""One row per day's USD->EGP market rate, fetched from an external API
	(currency_audit.market_rate_fetcher). Created via the 60-day backfill or the
	daily 9am job; one row per calendar date, enforced by the
	deterministic autoname (USD-EGP-<date>), so a concurrent duplicate insert hits
	the primary key (see market_rate_fetcher.store_rate). No deletion policy for now — kept indefinitely."""
