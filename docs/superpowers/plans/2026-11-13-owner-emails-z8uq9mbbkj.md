# G4b Owner emails: suspension, restore, service-end export notice (plan only)

ClickUp z8uq9mbbkj (parent G4 z8uq9mb383, BILLING Supervisor z8uq9m8h46). Plan-only builder, 2026-11-13, from `origin/main` 4edf9a2. Nothing here is built, migrated or switched. The wording in section 9 is a customer promise and waits for Coach Ed's approval.

## 0. The short version

- The suspension email **already exists on main** (Billing Build B2a-E, PR #142, `apps/billing/notices.py`, wording approved by Coach Ed 2026-11-13). So does the "trial ended" email and, inside the "payment approved" email, the "Your site is active again" restore sentence. The task text and the Manager's re-cut ("under bank transfer a suspended Subscriber gets no email today") are out of date. What stops these emails reaching Live4More today is only the guard `plan.collection_live()` (bank transfer switch, receiving account and proof store all set), not missing code.
- **Missing, and the real work:** the service-end export notice for `cancelled` and `cancelled_no_payment_method`. One new email.
- **Restore:** a restore path exists under bank transfer (an operator confirming a proof reactivates a suspended, cancelled or no-method site). It is already announced in the approval email. A separate restore email would send the owner two emails for one approval, so the plan drops it, with an optional one-sentence improvement (section 9, C).
- **No new table, no migration.** `billing.BillingNotice` (migration 0001, unique `idempotency_key`) is the once-only record and already serves every billing email.
- **One switch**, `OWNER_NOTICES_ENABLED`, default off, gates the new export notice (and the optional restore sentence).
- **One deviation from the re-cut to flag:** the emails are sent by the existing 5-minute scan, not from an `on_commit` callback (section 5, choice 1).

## 1. Sources read

- ClickUp: task z8uq9mbbkj (description, its one comment); rulings log z8uq9med6x (all 25 comments read, newest first). Nothing in the log changes this task; 509745 (staging holds live data) and 512065 (manager flips ruling-approved staging switches) matter for the release order.
- Code on main: `apps/billing/notices.py`, `tasks.py`, `services.py` (`_due`, `catch_up`, `cancel`, `reactivated`, `_settle_transfer`, `_end_service`, `_dates_after`, `payment_options`), `models.py` (`BillingNotice`, `SubscriptionTransition`), `plan.py` (`GRACE`, `DOWNLOAD_WINDOW`, `DELETION_AFTER_PAID`, `DELETION_NEVER_PAID`, `collection_live`), `methods.py`, `apps/notifications/services.py` (`send_email`, `EmailNotSent`), `apps/payments/retention.py`, `config/settings.py` (billing switches, beat schedule `billing-notices`), tests `apps/billing/tests/test_notices.py` and `base.py`.
- Docs: `docs/runbooks/billing.md` (emails paragraph), `docs/runbooks/support-and-exit.md` (sections 4 and 5), `docs/runbooks/retention.md`.
- Frontend `negosyoonline-frontend`: `apps/dash/public/assets/js/billing.js` on `origin/main` (d583aac).
- Live staging, read only: https://www-staging.negosyonline.com/terms, /privacy, /data-deletion.
- Open backend PRs: none (checked with `gh pr list`), so no file overlap.

## 2. What exists and what is missing

| Need | On main | Missing |
|---|---|---|
| Suspension email (T6 `suspended_after_attention`, T6n `suspended_no_outcome`) | `notices._transition_emails` -> `_paused`, code `service_paused`, key `suspended:{transition.pk}`. Subject "Your NegosyoOnline site is paused". Skipped if the site is no longer suspended. Tests in `test_notices.py` (`test_a_suspension_gives_one_paused_email...`, `..._skipped_when_the_site_is_no_longer_suspended`). | Nothing. Only `collection_live()` and the 48-hour window decide whether it is sent. |
| Trial ended, no payment method (`trial_ended_no_method`) | Same function, code `trial_ended`, key `trial_ended:{transition.pk}`, subject "Your NegosyoOnline trial has ended". | Nothing. It does not mention the export or deletion (a test asserts the paused email has no "delet"). |
| Restore | `confirm_transfer` -> `_settle_transfer` -> `reactivated()` for SUSPENDED, CANCELLED (after service end) and CANCELLED_NO_PAYMENT_METHOD -> state ACTIVE, transition `reactivated` with `detail.cycle`. `_payment_approved` adds "Your site is active again." when that transition exists. Test `test_approving_a_paused_site_says_it_is_active_again`. | A sentence saying what is available again (optional, section 9 C). No separate restore email. |
| Export notice at service end | Nothing. `cancelled` sends no email at all. | The whole email. |
| Sent record | `BillingNotice` (subscription FK, `code` max 40, `idempotency_key` unique max 120, `sent_at`). Written by `_send_once` under the subscription row lock, after a successful send. | Nothing. |
| Send path | `notifications.services.send_email` (Django `EmailMultiAlternatives`/`send_mail`, Resend over SMTP, an `EmailMessage` audit row, refuses a disposed address). | Nothing. |
| Retry with bound | A failed send writes no row; the next 5-minute scan tries again; an event is looked at for `WINDOW = 48h` only. | Nothing; the new email reuses it. |

Not found on main: `apps/payments/notices.py` and `send_attention_email` (the task text cites them); any `PROOF_EXPORT_READY_FROM` setting; any Xendit code beyond a hidden flag in `apps/payments/services.py`. So the Xendit-key line is dropped (as ruled) and proof images are not mentioned (the setting does not exist, so "while it is None" is always true today).

**Premise check, to the Supervisor:** the only reason Live4More would get no suspension or trial-ended email is that `collection_live()` is false on staging today (`BILLING_BANK_TRANSFER_ENABLED`, `BILLING_TRANSFER_ACCOUNTS` set by Coach Ed himself, and the private proof store token). Once those are set, the existing emails go out within 5 minutes of the transition. The Supervisor should confirm that, because it is the launch-critical item for the trial end around 2026-11-25.

## 3. Triggers, per email

All three trigger from stored state read by the scan `billing.notices` (every 5 minutes, `apps/billing/tasks.py`, `notices.run`). None runs inside a state change.

1. **Suspension (existing, unchanged).** Transition rows `suspended_after_attention` (T6) and `suspended_no_outcome` (T6n), written by `services.catch_up`. Sent when `effective_at` is within the last 48 hours, the subscription is still `SUSPENDED`, not exempt, site not deleted, account not closed, address not disposed. Code location: `notices._transition_emails`.
2. **Service-end export notice (new).** For a subscription in state `CANCELLED` with `service_ended_at <= now`, or `CANCELLED_NO_PAYMENT_METHOD`:
   - Both have `service_ended_at` set by `services._end_service` (in `cancel()` and in the `trial_ended_no_method` branch of `catch_up`).
   - Dates: `download_until` (service end + 30 days) and `deletion_due_at` (service end + 90 days, or + 30 days for a site that never had a paid cycle: `plan.DELETION_NEVER_PAID`). Both are `None` until payment collection is live (R-4a); the scan skips a row with no dates and sends when the clocks start.
   - "Became announceable at" = `max(download_until - plan.DOWNLOAD_WINDOW, effective_at of the latest transition cancelled / trial_ended_no_method)`. The first term is the service end (or the clocks-start time); the second makes a cancel-while-suspended count from the cancellation, not from the old suspension. The email is due while `announceable_at <= now < announceable_at + 48h`.
   - Key `export_notice:{subscription.pk}:{service_ended_at epoch seconds}`, code `export_notice`. A reactivation followed by a new cancellation gets a new `service_ended_at`, so a new key and a new email.
   - Not sent: billing-exempt, site deleted, account closed, disposed address, `OWNER_NOTICES_ENABLED` off.
   - Code location: new `_export_notice_emails(now)` in `apps/billing/notices.py`, added to the scan tuple in `run()`, using the existing `_send_once`.
3. **Restore.** No new trigger. The existing "payment approved" email already fires for the confirmed payment and already carries the sentence when the `reactivated` transition exists (`_payment_approved`).

## 4. Sent record and migration

- **A new table is not needed.** `BillingNotice` already gives a durable once-only record keyed by a unique string, written under the row lock the lifecycle's own writes take, so two scans cannot both send. The export notice only adds one `code` value (`export_notice`, 13 characters, limit 40) and a longer key (about 45 characters, limit 120).
- **No migration is written.** This removes the need for Coach Ed's separate migration approval that the task text anticipates.
- The one failure mode the design keeps: the mail goes out and the `BillingNotice` insert then fails (database error). The next scan would send once more. It is the same as every existing billing email and is accepted; the window of 48 hours bounds it.

## 5. Choices made (what, why, source)

1. **Scan, not `on_commit`.** The re-cut and the task text say on_commit. The merged email family is a scan, and it is stronger on the point that matters: the email is never inside the state change at all, so no send failure can block or reverse a suspension, a cancellation or a restore. An `on_commit` callback is lost if the process dies after the commit; the scan retries from stored state. The latency is at most 5 minutes after a transition the sweep wrote. Source: `notices.py` docstring ("Nothing here runs inside a payment action"), `docs/runbooks/billing.md`. Reversible; flagged to the Supervisor.
2. **No separate restore email.** Source: code, section 2. A second email for one approval is noise, and the approval email already states the site is active again.
3. **The switch gates only what is new.** The existing suspension and trial-ended emails stay governed by `collection_live()`. Putting them behind a default-off switch would silence emails that are meant to work the day collection goes live. Source: `notices.run`.
4. **Separate email for the export notice, also for `cancelled_no_payment_method`,** even though that state already gets the "trial has ended" email in the same scan. Reason: the trial-ended email is approved and live, carries no data promise, and a test asserts it never mentions deletion; the export notice is a separate promise with its own approval. Open decision (section 10, 2).
5. **One template for both deletion periods.** Dates are read from the subscription, so a never-paid site (export and deletion dates equal, 30 days) reads correctly without a variant.
6. **Dates are exact instants in Manila time** (`_day_time`, "25 December 2026 at 10:00 AM"), because the last day to ask for the export and the last day to pay are instants in code (`download_until`, `deletion_due_at`).
7. **"After {date}", not "on {date}", for deletion.** The Terms say NegosyoOnline "deletes or anonymises that website's store data" after the period. No scheduled job on main deletes a site's data at `deletion_due_at`; account deletion is by request and runs `export_account`/disposal by hand (`docs/runbooks/retention.md`, `apps/payments/retention.py` blanks receiving accounts only in an account disposal). Wording the date as "after" is true whenever the deletion happens, and does not promise a day. Flagged in section 10, 3.
8. **Export is requested by email to support from the account email,** the way the Terms, the Data Deletion page and the support runbook (section 4) say. No delivery time is promised: the published pages give none for an export (only 1 business day to reply to a deletion request).
9. **No proof-image line, no Xendit line, no marketing, no unsubscribe.** Source: Manager re-cut; no `PROOF_EXPORT_READY_FROM` on main.
10. **Wording avoids the words "tax" and "BIR"** (an existing test, `test_no_body_carries_an_account_number_a_key_or_a_tax_claim`, bans them in billing emails). "Billing records" is used.

## 6. The switch

- `OWNER_NOTICES_ENABLED` in `config/settings.py`, `os.environ.get("OWNER_NOTICES_ENABLED", "").strip() == "1"`, default off, next to `BILLING_STAFF_ALERT_EMAIL`. Add a row to the variable table in `docs/runbooks/billing.md`.
- Off: `_export_notice_emails` returns at once and reads no table; `_payment_approved` keeps today's exact sentence. On: the export notice is sent (still only while `BILLING_ENABLED` and `collection_live()` hold, because `run()` returns first).
- Setting it on staging is a staging switch: Coach Ed's approval of the wording (section 9) is what the Manager acts on. Production is never covered.

## 7. Retry and failure rules

- A failed send (`EmailNotSent`) logs one warning with no address and writes no row; the next scan (5 minutes) tries again. The bound is the 48-hour window from `announceable_at`; after that the notice is given up and never sent late.
- A send failure never blocks or reverses a state change: the scan runs after the transition is committed and each email is its own transaction. A scan error is caught per scan function (`run()`), so a failure in the export scan never stops the other emails.
- Once-only: `BillingNotice.idempotency_key` unique plus the row lock; replays and a second worker send nothing.
- Tenant isolation: the address is always `subscription.site.account.email` of the row being processed; no recipient is read from anything else. A deleted site, closed account or disposed address is skipped.
- Switching `OWNER_NOTICES_ENABLED` on later never mails a backlog: only a service end announceable within the last 48 hours is sent.

## 8. Tests (names), all in `apps/billing/tests/test_owner_notices.py` unless noted

Export notice:
- `test_a_cancelled_site_after_its_service_end_gets_one_export_notice`
- `test_a_cancel_inside_a_paid_period_sends_nothing_until_the_service_end`
- `test_a_trial_that_ended_unpaid_gets_one_export_notice`
- `test_the_notice_names_the_export_date_the_deletion_date_and_support`
- `test_a_never_paid_site_gets_the_thirty_day_dates_in_the_same_text`
- `test_a_paid_site_gets_the_ninety_day_deletion_date`
- `test_the_dates_are_manila_time`
- `test_no_notice_while_the_dates_are_not_set_and_one_when_the_clocks_start`
- `test_the_notice_is_sent_once_and_a_replay_sends_nothing` (two scans; `BillingNotice` count 1)
- `test_a_new_service_end_after_a_reactivation_gets_a_new_notice`
- `test_a_cancel_while_suspended_counts_from_the_cancellation`
- `test_a_failed_send_writes_no_row_is_retried_and_given_up_after_48_hours`
- `test_a_send_failure_leaves_the_cancellation_and_the_state_unchanged`
- `test_nothing_is_sent_or_read_while_the_switch_is_off`
- `test_nothing_is_sent_while_collection_is_off_or_billing_is_off`
- `test_an_exempt_site_a_deleted_site_a_closed_account_and_a_disposed_address_get_nothing`
- `test_one_sites_notice_goes_only_to_its_own_owner` (two subscriptions, two owners)
- `test_the_text_never_promises_an_unconditional_deletion_and_names_the_hold`
- `test_the_text_has_no_proof_image_no_xendit_no_tax_claim_and_no_account_number`
- `test_no_backlog_when_the_switch_is_turned_on_after_the_window`
- `test_the_task_runs_the_export_scan_in_process`

Suspension and restore (existing behaviour, new proof for the re-cut; in `test_notices.py`):
- `test_a_suspension_by_t6_and_by_t6n_each_give_one_paused_email` (adds the T6n case if only T6 is covered now)
- `test_a_restore_after_a_suspension_sends_the_approval_email_once_with_the_active_again_sentence`
- `test_the_restore_sentence_lists_what_is_available_again_only_when_the_switch_is_on` (only if section 9 C is approved)

## 9. Wording for approval

Dates are Manila time. Placeholders are in {braces}. Link and support values are the code's: `{billing_link}` is `{DASHBOARD_URL}/settings/billing/` (staging: https://dash-staging.negosyonline.com/settings/billing/); support is support@negosyoonline.com. Date placeholders print as "25 December 2026 at 10:00 AM".

### A. Suspension email (EXISTS on main, approved 2026-11-13; shown so the Manager sees what Coach Ed would receive; no change proposed)

Subject: `Your NegosyoOnline site is paused`

```
Hello {owner_name}, your site {site_name} is paused because we have no approved payment for it. Your storefront, checkout and the dashboard pages for running your store are not available. You can still sign in and open your Billing page. To bring your site back, pay by bank transfer and send the proof: {billing_link}. Payment can be accepted until {deletion_due_date}. Questions: support@negosyoonline.com.

NegosyoOnline
```

(`{deletion_due_date}` prints as a day, "25 December 2026". "Hello," alone when the owner has no name. The "Payment can be accepted until" sentence is left out when the date is not yet set.) The trial-ended variant is the same text with the first sentence "the free trial for {site_name} has ended and your site is paused." under the subject `Your NegosyoOnline trial has ended`.

### B. Service-end export notice (NEW)

Subject: `Your NegosyoOnline store data: how to ask for your export`

```
Hello {owner_name},

{reason_line}

Your store data
You can ask for an export of your store data for 30 days after your service ended. To ask, email support@negosyoonline.com from the email address on your account. We will send the export to that address. Please ask by {download_until_at}.

What happens after
After {deletion_due_at}, as our Terms say, we delete or anonymise the store data of {site_name}. This includes the receiving-account details you saved for your store (account name, account number and QR code). We do not delete what the law requires us to keep, or what we must hold for a dispute or a fraud check. Billing records are kept as required.

Bringing your site back
Until {deletion_due_at} you can still bring your site back: pay by bank transfer and send the proof on your Billing page: {billing_link}. You can still sign in and open your Billing page.

Questions: support@negosyoonline.com.

All dates are Manila time.

NegosyoOnline
```

`{reason_line}` is one of:
- cancelled: `Your subscription for {site_name} ended on {service_end_at}.`
- cancelled, no payment method (trial): `The free trial for {site_name} ended on {service_end_at}, and no payment was approved.`

Where each statement comes from: the 30-day export and email-to-support request (Terms "Your data after the service ends", Data Deletion page, support runbook section 4); the deletion after 90 days, or 30 days for a site that never had a successful payment (Terms, same section); the holds sentence (Terms: "subject to legal, billing, tax, security, fraud-prevention, dispute-resolution, and backup-retention requirements"); paying until `deletion_due_at` (`reactivated` refuses a payment at or after it; `payment_options` reason `past_deletion_date`); sign-in and Billing remain (`billing_only`). It says nothing about proof images, Xendit, delivery time of the export, or the contents of the export beyond "your store data". If the export and deletion dates are the same day (a site that never paid), the text still reads correctly.

### C. Restore sentence (OPTIONAL, only if Coach Ed wants it; shown only when `OWNER_NOTICES_ENABLED` is on)

In the existing "Your payment is approved" email, for a site that was paused or ended, the sentence now reads ` Your site is active again.` Proposed:

```
Your site is active again. Your storefront, checkout and the dashboard pages for running your store are available again.
```

If not approved, nothing changes and there is no restore email.

## 10. Open decisions for Coach Ed (with a recommendation)

1. **Approve the wording in section 9 (B, and C if wanted).** B is a customer promise. Recommended: approve B; skip C unless he wants the longer sentence.
2. **One email or two for a trial that ended unpaid?** `cancelled_no_payment_method` already receives "Your NegosyoOnline trial has ended" in the same scan, then the export notice within minutes. Recommended: two emails (different promises, existing one approved and live). Alternative: add the export paragraph to the trial-ended email and send the separate notice only for `cancelled`.
3. **The 90-day deletion has no scheduled job on main.** The email says "after {date}, as our Terms say, we delete or anonymise". A never-paid site's date is 30 days after its service end, so for Live4More the first such date is about a month after the trial ends. Someone should decide who performs the deletion and by what means before that date; this plan does not build it. Recommended: leave the wording as "after"; raise a task under GATES (retention) for the job or a runbook step.
4. **Suspended sites also have the 30-day download window** (the Billing screen shows "You can download your data until {date}" for them) but the ruling (L70) and this re-cut cover only the two cancelled states, and the paused email deliberately mentions no deletion. Recommended: no change now.
5. **Scan instead of `on_commit`** (section 5, choice 1). Recommended: scan. A routine reversible detail; listed so it is seen.

## 11. PR count and release order

- **One backend PR** (through the release train; no frontend change): `apps/billing/notices.py` (new scan and text), `config/settings.py` (switch), `docs/runbooks/billing.md` (variable row and emails paragraph), `apps/billing/tests/test_owner_notices.py` and the small additions to `test_notices.py`. No migration, no model change.
- Release order: (1) the Manager confirms `BILLING_BANK_TRANSFER_ENABLED`, `BILLING_TRANSFER_ACCOUNTS` and the proof store are set on staging so the existing suspension and trial-ended emails can send (this is independent of this PR and is what Live4More needs); (2) the PR merges and deploys; (3) Coach Ed approves the wording; (4) the Manager sets `OWNER_NOTICES_ENABLED=1` on staging `worker` (the scan runs there) and checks with a clearly named test account, since staging holds real billing records (rulings log 509745); (5) production is never touched. The export notice is wanted before the first cancellation or trial end of a real Subscriber; a trial ending 2026-11-25 with no proof ends at the trial end.
- Local checks before the branch is reported ready: the full billing tests on SQLite and PostgreSQL, `manage.py check`, `makemigrations --check` (must report no change).

## 12. Not read or not available

- Parent task z8uq9mb383, G4a z8uq9mbbkh, the P6.1 contract z8uq9mb22f (r24) and the export task z8uq9mbu0p were not opened; their content reached this plan only through the quotes in the G4b task text.
- The G4 "data-request paragraph" is not in `billing.js` on frontend `origin/main`; the only text there is "You can download your data until {date}" and "Your data is kept until {date}". A superseded worktree copy says "export your data for 30 days" with a mailto to support@negosyoonline.com. The email agrees with both.
- `/privacy` on staging gives no period for receiving-account details or proof images; the email states neither beyond "store data".
- The wording was not sent to any mailbox; no staging action was taken.
