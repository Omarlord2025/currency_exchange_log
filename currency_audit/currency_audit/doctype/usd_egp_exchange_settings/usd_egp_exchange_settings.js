// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Pure UI trigger — all the actual work (the API call, parsing, storing) happens
// server-side in currency_audit.usd_egp_rate_fetcher.backfill_last_60_days.
frappe.ui.form.on("USD EGP Exchange Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Backfill Last 60 Days"), () => {
			frappe.call({
				method: "currency_audit.usd_egp_rate_fetcher.backfill_last_60_days",
				freeze: true,
				freeze_message: __("Fetching..."),
				callback: (r) => {
					if (r.message) {
						frappe.msgprint(
							__("Fetched {0} days, stored {1} new.", [r.message.fetched, r.message.stored])
						);
					}
				},
			});
		});
	},
});
