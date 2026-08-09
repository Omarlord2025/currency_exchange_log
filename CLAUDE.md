# Engineering Standards — currency_audit

This file is read automatically at the start of every Claude Code session in this repo and is treated as ground truth. Every new function, feature, or script added here — not just the Currency Exchange Log — must satisfy this checklist before it's considered done.

## 1. Where new code belongs

| Need | Use | Why |
|---|---|---|
| Pure UI behavior — show/hide fields, defaults, formatting, dialogs | **Client Script** | Runs in the browser. Fast to iterate, but never trust it for validation or business rules — it's UX sugar, not security. It can be bypassed from the API entirely. |
| One-off, low-stakes automation you don't want to build/migrate an app for | **Server Script** (sparingly) | Runs server-side without deploying code, but it's sandboxed — no external imports, restricted Python — and hard to version-control, test, or review. Fine for a quick permission query; wrong for anything that matters. |
| Business logic, data integrity, anything performance-sensitive, anything reused across doctypes/features | **This app** | Real Python, real imports, real git history, real unit tests, full query-builder access, background jobs, caching. The only path to genuine modularity. |

Rule of thumb: if it touches the database beyond a trivial read, or you'll ever want to reuse it, it belongs in the app — not a Server Script, not a Client Script.

## 2. Design principles

- **DRY, always.** Shared logic goes in `currency_audit/utils.py` (or a purpose-named module) and gets imported. Never duplicate a function across files.
- **Single responsibility.** Doctype controller methods (`validate`, `on_update`, etc.) stay thin — a few lines calling into a utils function, not the logic itself. That's what makes it reusable and testable.
- **Every function gets a docstring** — what it does, its params, what it returns. No exceptions.
- **No business logic in client scripts.** If a client script needs to decide something non-trivial, it calls a whitelisted server method — it doesn't encode the rule in JS.

## 3. Performance checklist — run this on every function that touches the database

- [ ] No `frappe.get_doc()` inside a loop. Use `frappe.get_all()` / `frappe.get_list()` with an explicit `fields=[...]` for bulk reads.
- [ ] Never call `frappe.get_all()` without a `fields` list on a wide doctype — that pulls every column, every row.
- [ ] A single-value lookup uses `frappe.db.get_value()`, not a full document load.
- [ ] Anything that could touch more than ~50–100 records runs as a background job (`frappe.enqueue`) — never inline inside a `validate`/`on_update` hook that blocks the user's save.
- [ ] Filters that will run often (e.g. currency + conversion_rate lookups) hit indexed columns. If a new filter needs one, flag it — add a DB index rather than a full table scan.
- [ ] No repeated identical DB calls inside a loop — fetch once, reuse the result. For expensive, rarely-changing lookups, use `frappe.cache()`.
- [ ] Any external API call is batched, never called once per record in a loop.

## 4. Before calling anything "done"

Re-read the new code against section 3. Note in the commit message which checklist items applied and what changed as a result (e.g. "switched from per-row get_doc to a single get_all — cut this from ~400 queries to 2").

## 5. Testing

Every controller method and utils function gets a `frappe.tests`-based unit test — not just a manual click-through in Desk. Tests live in the matching `test_*.py` file next to the code they cover.
