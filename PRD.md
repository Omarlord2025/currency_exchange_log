# PRD: Currency Exchange Log — CNC Leaders ERPNext

## Overview / Problem

CNC Leaders' ERPNext instance uses the built-in Currency Exchange list to record exchange rates. Multiple people can edit an existing rate entry, and today there's no record of what the rate used to be, who changed it, or when. Frappe snapshots the rate onto each transaction (`conversion_rate`) at the moment it's saved, so transactions already "remember" what rate they used — but nothing surfaces that. When a rate looks wrong or gets questioned later, there's no way to answer "was this the rate in effect at the time, and what else used it?" without digging manually.

## Principles

The decisions everything else here follows from:

1. **Immutable trail.** A log entry, once created, is never edited or deleted — it's the historical record, kept indefinitely (no retention/archival policy).
2. **Value match, not date match.** A transaction's own `conversion_rate` is ground truth. Matching is done by exact rate value, not by which date range a Currency Exchange record happened to cover — dates can be backdated or ambiguous; a snapshotted value on the transaction can't be.
3. **Log the edit, not the view.** Only an actual change to the rate creates a log entry. Opening and re-saving with the same value is a no-op.
4. **Cheap by default, thorough on demand.** The expensive part — scanning transaction tables — runs once, in the background, right after a change is detected. It doesn't re-run every time someone opens a log entry.
5. **Audit-only, not enforcement.** This records and surfaces what happened. It doesn't block anyone from editing a rate — that's a separate, later decision if you want it.
6. **Code quality is governed by `CLAUDE.md`**, not this doc — see that file for the DRY/performance checklist every feature here has to pass.

## Features (v1 scope)

**F1 — Change detection**
Hook into the built-in Currency Exchange doctype's update event. Compare old vs. new `exchange_rate`. No change → do nothing. Changed → create a Currency Exchange Log entry.

**F2 — Currency Exchange Log doctype**
Fields: currency pair (from/to), old rate, new rate, changed by, changed on, link back to the source Currency Exchange record.

**F3 — Usage child table**
A child table on the log entry listing every transaction that used the old rate or the new rate: doctype, document name, date, amount, currency.

**F4 — Background matching job (all doctypes)**
Runs via `frappe.enqueue` immediately after a log entry is created, so the save that triggered it isn't blocked. Searches Sales Invoice, Purchase Invoice, POS Invoice, Purchase Receipt, and Delivery Note by `currency` + `conversion_rate` — these five share the exact same field shape, so one query pattern covers all of them. Payment Entry and Journal Entry are matched with their own logic in the same pass: Payment Entry stores the rate per side of the transaction (`source_exchange_rate` / `target_exchange_rate`, since a payment can move money between two different currencies), and Journal Entry stores it per row in a child table (each account line can carry its own currency and rate). Built and shipped together with the rest of v1, not staged as a later phase.

**F5 — Access control**
Visible to System Manager only, for now.

**F6 — Baseline backfill**
Checked against the doctype's actual source: Currency Exchange has `track_changes` turned off, so Frappe never captured a diff history for it — there's no hidden log of past edits sitting anywhere to recover. What's buildable instead: a one-time patch, run at install, that snapshots every currently-active Currency Exchange record as a **baseline log entry** (tagged "baseline," not "change" — no prior value is known), then immediately runs the F4 matching job against it, so day one already shows what's used the current rate. As a second safety net going forward, we'll also flip `track_changes` on for Currency Exchange itself, so even an edit that somehow bypasses the custom hook still leaves a trace in Frappe's own Version history.

**F7 — Lightweight dashboard**
Confirmed wanted. A filterable view of recent rate changes (who, when, old→new rate) with drill-through to each change's matched transactions. Scoped once the underlying log and matching are proven — not a BI build, just a usable window onto the data that already exists.

## Non-goals (v1)

- **Buying/selling rate split** — not used at CNC Leaders, so `for_buying`/`for_selling` aren't tracked separately.
- **API or scheduled-sync triggers** — only manual Desk edits are in scope. If rates ever start syncing automatically, this needs revisiting.
- **Full reconstruction of past edits** — Frappe wasn't tracking changes on this doctype before now, so there's no way to know the exact old→new values, who, or when for edits made before install. Confirmed there's no other historical data source (backups, exports, spreadsheets) to extend it with — F6 stays a baseline snapshot, not a full historical timeline.
- **Blocking or preventing edits** — this is a record-keeper, not a gatekeeper.
- **Alerts/notifications** (e.g. a Telegram ping on a rate change) — not requested. Reporting is covered by F7; proactive alerts are a separate, later conversation.

## Plan

| Phase | What | Depends on |
|---|---|---|
| 1 | Scaffold `currency_audit` app, install on site | — |
| 2 | Build the `Currency Exchange Log` doctype + usage child table | Phase 1 |
| 3 | Write the change-detection hook (F1) | Phase 2 |
| 4 | Write the background matching job covering all seven doctypes (F4) | Phase 2 |
| 5 | Write the one-time baseline backfill patch (F6) | Phases 2–4 |
| 6 | Wire into `hooks.py`, migrate, ship v1 | Phases 3–5 |
| 7 | Test against a real rate edit; spot-check the matches manually, confirm the baseline ran correctly | Phase 6 |
| 8 | Build the lightweight dashboard (F7) | Phase 7 |

## Success criteria

- Every manual rate edit in Desk produces exactly one log entry — no duplicates, no misses.
- No log entry is created for a save that didn't actually change the rate.
- The usage child table for a log entry matches a manual spot-check (pull 5 invoices by hand, confirm they appear).
- The rate edit itself isn't slowed down for the person doing it — matching happens after, in the background.
- Every currently-active Currency Exchange record has a baseline log entry (F6), with its usage matched, shortly after install.

