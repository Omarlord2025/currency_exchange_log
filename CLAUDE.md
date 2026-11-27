# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`currency_audit` is a Frappe v15 / ERPNext app installed on the bench at `/home/frappe/frappe-bench`.
It is an **audit-only** layer over ERPNext's built-in Currency Exchange doctype: it records rate
changes and matches them against the transactions that used each rate. It never blocks an edit and
never changes what rate a transaction uses. `PRD.md` holds the original requirements (F1–F7) that
the code's docstrings refer to by number.

## Commands

Run everything from the bench root (`/home/frappe/frappe-bench`); the site is `erpnext`.

```bash
# Tests (allow_tests is already true in sites/erpnext/site_config.json)
bench --site erpnext run-tests --app currency_audit
bench --site erpnext run-tests --module currency_audit.tests.test_matching
bench --site erpnext run-tests --module currency_audit.tests.test_matching --test test_matches_submitted_invoice_by_currency_and_rate
bench --site erpnext run-tests --doctype "Currency Exchange Log"
bench --site erpnext run-tests --app currency_audit --failfast

# Apply doctype/schema changes and run patches
bench --site erpnext migrate

# Rebuild client assets after touching a .js file
bench build --app currency_audit

# Interactive shell against the site (fastest way to reproduce a bug)
bench --site erpnext console

# Watch background-job behaviour (matching runs on the "short" queue)
tail -f logs/worker.error.log

# Lint / format — NOT installed in this bench's env; install first if you need them
# (.pre-commit-config.yaml pins ruff v0.8.1)
pip install ruff pre-commit && ruff check currency_audit && ruff format currency_audit
```

Formatting is ruff with **tab indentation** and double quotes (`pyproject.toml`), line length 110.
Match that by hand — `E501`/`W191` are intentionally ignored, so don't reflow existing code to
satisfy them. `python3 -m py_compile <file>` is the quick syntax check when ruff isn't available.

## Architecture

### The pipeline

Everything converges on one doctype, **Currency Exchange Log** (`CEL-.#####`), plus its
`Currency Exchange Log Usage` child table. Four independent sources create log entries, all through
the single chokepoint `utils.create_log_entry()`, which inserts the log and enqueues the matching
job:

| Source | Trigger | `entry_type` |
|---|---|---|
| `events/currency_exchange.py` (F1) | `on_update` on Currency Exchange, rate actually changed | `Change` |
| `patches/baseline_backfill.py` (F6) | install / migrate, snapshots existing records | `Baseline` |
| `reconciliation.py` | daily 3am sweep of Frappe `Version` history for edits the hook missed | `Recovered` |
| `external_rate_detection.py` (F1.6) | `on_submit` of the seven transaction doctypes | `External` |

`matching.run_matching_job()` (F4) then runs in the background and fills the `usage` child table by
searching for transactions whose stored rate equals the log's `old_rate` or `new_rate`.

### Invariants — break these and the audit trail is wrong

- **Value match, not date match** (PRD Principle 2). A transaction's own `conversion_rate` is ground
  truth. Never filter matches by date range — dates only produce the `match_confidence`
  (`Confirmed`/`Possible`) label via `_rate_windows()`/`_confidence()`. An ambiguous match is still
  shown, just flagged.
- **Logs are immutable.** `CurrencyExchangeLog.validate` rejects changes to any field in
  `PROTECTED_FIELDS` and `on_trash` rejects deletion outright. `usage` and `matching_status` may
  change after insert, but only through `run_matching_job`, which sets `flags.allow_matching_update`
  before saving — any other write (Administrator, REST API, console) is rejected by
  `_reject_matching_result_edits`, and `CurrencyExchangeLogUsage.on_trash` blocks deleting a single row.
  Tests that write `usage` by hand must set the same flag; note `reload()` does not clear flags.
  The other exemption is gated on the `allow_baseline_attribution_correction` flag, set solely by
  `patches/correct_baseline_attribution.py`.
- **A transaction never states its `to_currency`** — only its company, and therefore that company's
  base currency. `_companies_with_currency()` scopes matches by that; without it a USD→EGP and a
  USD→INR log with the same numeric rate would both claim the same documents. An empty company list
  means "couldn't determine", so the filter is *skipped*, not applied as an exclusion.
- **Create logs only via `utils.create_log_entry()`.** It handles the pre-enqueue `frappe.db.commit()`
  (so a fast worker can't race the insert) and the deduplicated `job_id`. Tests bypass it deliberately
  via `test_helpers.make_log()`.
- **The audit trail must never block the documents it audits** (PRD Principle 5). The `usage` child
  table's Dynamic Link would otherwise make every matched transaction undeletable;
  `hooks.ignore_links_on_delete` suppresses that and `tests/test_link_blocking.py` guards it. Any new
  field that links out to a transaction has to keep that property. This is separate from the log's
  *own* immutability, which stays enforced by `CurrencyExchangeLog.on_trash`.

### Gotchas found the hard way

- A freshly inserted log's in-memory doc has `old_rate=None` where the stored row reads back `0.0`,
  so saving that same object again trips the immutability check. `run_matching_job` re-fetches the
  doc, which is why it never hits this; do the same (`log.reload()`) before appending usage rows.
- Deleting a ledger-bearing transaction also needs ERPNext's Accounts Settings →
  *"Delete Accounting and Stock Ledger Entries on deletion of Transaction"*. `delete_doc` runs
  `on_trash` (where ERPNext clears GL/stock entries) **before** the link checks, so with that setting
  off, GL Entry blocks the delete regardless of anything this app does.

### The seven transaction doctypes

Three distinct rate shapes, and every new feature has to cover all three:

1. **Sales Invoice, Purchase Invoice, POS Invoice, Purchase Receipt, Delivery Note** — identical
   `currency` + `conversion_rate` + `posting_date`/`posting_time` shape, so one query pattern covers
   all five (`matching.STANDARD_DOCTYPES`).
2. **Payment Entry** — a rate per side: `paid_from_account_currency`/`source_exchange_rate` and
   `paid_to_account_currency`/`target_exchange_rate`. Both sides must be checked.
3. **Journal Entry** — a rate per row of the `accounts` child table; `company` lives on the parent,
   so child rows are matched first and then filtered by their parent.

Payment Entry and Journal Entry have **no `posting_time`** in ERPNext, so `_posting_datetime()` falls
back to midnight for them — day-level confidence is the ceiling there, not a bug to "fix".

### Reliability mechanisms

- `matching.retry_stuck_matches()` — every 15 min, re-queues any log stuck on `Pending` past
  `STUCK_THRESHOLD_MINUTES`. Deduplicated by `matching_job_id()`.
- `run_matching_job` retries `MAX_SAVE_ATTEMPTS` times on `TimestampMismatchError`, since the sweep
  can re-queue a log while the original job is still saving.
- `install.after_install` deliberately re-runs `baseline_backfill`, `reconcile_version_history`, and
  `detect_historical_external_rates` directly, because `bench install-app` marks every `patches.txt`
  entry as already-applied without running it. All three are idempotent — keep them that way.

### Separate, self-contained feature

`market_rate_fetcher.py` + `USD EGP Exchange Rate` + `USD CNY Exchange Rate` + the shared
`USD EGP Exchange Settings` (name predates CNY; it holds the one host + key for both) are a standalone
reference table of daily USD→EGP and USD→CNY (RMB) market rates. Both currencies come from a single
`/live` (9am job) or `/timeframe` (on-demand 60-day backfill) request, because the provider's free plan
is ~100 requests/month. `retry_if_missing` (9:15) re-calls the API only if a currency has no row today.
To add a currency: a new rate doctype plus one entry in `RATE_DOCTYPES`. It **never touches Currency
Exchange** and is not part of the audit pipeline. The provider's date-range endpoint is `/timeframe`, not
`/timeseries` (the latter 404s), errors arrive as `{"success": false}` bodies (code 104 = quota used up),
and `host` must be a bare base URL — `USDEGPExchangeSettings.validate` enforces that. One row per date is
enforced by the rate doctypes' `format:USD-<CUR>-{date}` autoname.

### Tests

`FrappeTestCase` (`frappe.tests.utils`), rollback-isolated — which is why `create_log_entry` and
`store_rate` skip their `frappe.db.commit()` under `frappe.flags.in_test`. Fixtures come from
`currency_audit/test_helpers.py`; `get_or_create_currency_exchange` dates its records `2099-12-31`
so a test can never overwrite a live site's real rate for the same currency pair. Test modules
deliberately declare **no `test_dependencies`** — ERPNext's recursive dependency walk hits an
uninstalled "Payment Gateway" doctype and aborts collection.

## Engineering standards

Every new function, feature, or script added here must satisfy this checklist before it's done.

### 1. Where new code belongs

| Need | Use | Why |
|---|---|---|
| Pure UI behavior — show/hide fields, defaults, formatting, dialogs | **Client Script** | Runs in the browser. Fast to iterate, but never trust it for validation or business rules — it's UX sugar, not security. It can be bypassed from the API entirely. |
| One-off, low-stakes automation you don't want to build/migrate an app for | **Server Script** (sparingly) | Runs server-side without deploying code, but it's sandboxed — no external imports, restricted Python — and hard to version-control, test, or review. Fine for a quick permission query; wrong for anything that matters. |
| Business logic, data integrity, anything performance-sensitive, anything reused across doctypes/features | **This app** | Real Python, real imports, real git history, real unit tests, full query-builder access, background jobs, caching. The only path to genuine modularity. |

Rule of thumb: if it touches the database beyond a trivial read, or you'll ever want to reuse it, it belongs in the app — not a Server Script, not a Client Script.

### 2. Design principles

- **DRY, always.** Shared logic goes in `currency_audit/utils.py` (or a purpose-named module) and gets imported. Never duplicate a function across files.
- **Single responsibility.** Doctype controller methods (`validate`, `on_update`, etc.) stay thin — a few lines calling into a utils function, not the logic itself. That's what makes it reusable and testable.
- **Every function gets a docstring** — what it does, its params, what it returns. No exceptions.
- **No business logic in client scripts.** If a client script needs to decide something non-trivial, it calls a whitelisted server method — it doesn't encode the rule in JS.
- **Comments explain *why*, not *what*.** The existing code documents the reasoning behind non-obvious choices (why a commit is skipped in tests, why an empty company list skips the filter). Preserve that when editing — a rewrite that drops the reasoning loses more than it gains.

### 3. Performance checklist — run this on every function that touches the database

- [ ] No `frappe.get_doc()` inside a loop. Use `frappe.get_all()` / `frappe.get_list()` with an explicit `fields=[...]` for bulk reads.
- [ ] Never call `frappe.get_all()` without a `fields` list on a wide doctype — that pulls every column, every row.
- [ ] A single-value lookup uses `frappe.db.get_value()`, not a full document load.
- [ ] Anything that could touch more than ~50–100 records runs as a background job (`frappe.enqueue`) — never inline inside a `validate`/`on_update` hook that blocks the user's save.
- [ ] Filters that will run often (e.g. currency + conversion_rate lookups) hit indexed columns. New recurring filter shapes get an index added to `install.INDEXED_FIELDS` rather than a full table scan.
- [ ] No repeated identical DB calls inside a loop — fetch once, reuse the result. For expensive, rarely-changing lookups, use `frappe.cache()`.
- [ ] Any external API call is batched, never called once per record in a loop.

### 4. Before calling anything "done"

Re-read the new code against section 3. Note in the commit message which checklist items applied and what changed as a result (e.g. "switched from per-row get_doc to a single get_all — cut this from ~400 queries to 2").

### 5. Testing

Every controller method and utils function gets a `frappe.tests`-based unit test — not just a manual click-through in Desk. Tests live in `currency_audit/tests/test_*.py` for module-level code, or in the matching `test_*.py` next to the doctype/patch they cover.
