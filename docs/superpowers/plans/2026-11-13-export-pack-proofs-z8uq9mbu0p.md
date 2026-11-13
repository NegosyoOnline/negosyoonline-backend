# Store-data export extension: Pack orders and proof images (plan)

Status: **plan only, for the BILLING Supervisor's review and Coach Ed's approval where marked.** No code, migration, switch, variable, test or staging action is in this branch. ClickUp z8uq9mbu0p stays `Open` (gated).

ClickUp task: z8uq9mbu0p, "Store-data export extension: Pack orders and proof images (FA1-04, L67)". Parent: Billing z8uq9m8h46. Read against `origin/main` at `2957eb3`, and against the not-yet-merged `origin/feat/z8uq9mb22v-deletion-executor-pr2` (head `691988b`).

## 1. Sources read

The task description and its one comment (Manager, 2026-11-13: not launch work, left open; "no service-end text may promise proof images in the export" until this ships). The rulings log (z8uq9med6x), notably 1100430000509745 (staging is live from the first real Santé member), 1100430000509378 and 1100430000512065 (routine decisions), 1100430000509990 and 510008 (one staff account), 1100430000509929 and 509948 (release train: branches, no PRs from builders). Code: `apps/accounts/retention.py`, `apps/accounts/management/commands/export_account.py`, `apps/pack/retention.py`, `apps/pack/purge.py`, `apps/pack/models.py`, `apps/common/private_files.py`, `apps/billing/services.py` (`end_dates`, `deletion_point`), `apps/billing/plan.py`, `apps/operators/audit.py`, `apps/billing/notices.py` (G4b email), `docs/runbooks/retention.md`, `support-and-exit.md`, and the executor plan `2026-11-13-site-deletion-executor-z8uq9mb22v.md` (section 3a, 3b, D14, L67 notes) with its PR2 branch.

## 2. What the old description assumed, and what is obsolete

The description was written against the TWCinc-era code. Mapped onto this repository:

| Old description | Here | Verdict |
|---|---|---|
| `app/negosyoonline/management/commands/negosyoonline_export_store_data.py`, one JSON file for one billing Site | `manage.py export_account` (`apps/accounts/management/commands/export_account.py`), one JSON for one **account** and all its sites, `--out-dir` or `--stdout`, audit action `account_exported` | Obsolete path; same idea. The export is **support-run** (a staff user over `railway ssh`, after the runbook identity check). It is not Subscriber self-serve and stays so (no ruling changes it; L70 says the Subscriber is told how to *request* it) |
| Window check `service_end_at <= now <= download_window_ends_at`, states `cancelled`, `cancelled_no_payment_method` | **`export_account` has no window check at all** (the Manager's audit is right). The window exists only as a stored date (`Subscription.download_until`, `plan.DOWNLOAD_WINDOW` = 30 days) and in the G4b email | Missing; this plan adds it, for the Pack files only (section 4.4) |
| `EX` `LifecycleTransition` row per request, "an existing EX row is by definition a complete export" | No such row. The audit record is one `OperatorAuditEntry`, action `account_exported` (`audit.record`, `KEPT`). `apps/lifecycle` is the new executor app (ErasureBatch, ErasureHold) and has no export row | Obsolete. The purge never looks for an export row (L71 says it must not wait for one); nothing needs it |
| Xendit key and webhook token never exported (L69) | Xendit was disapproved (launch payment is bank transfer) and is not in this repository | Obsolete as written. The equivalent rule is kept: no provider credential, no token, no file key, no row number in the export (section 4.2, test T7) |
| Billing-Site to platform-Site link (E1-CA1-02), "G1's link" | `Subscription.site` is the same `sites.Site` the Pack rows use (`PackOrder.site`, `PackProofSubmission.site`). There is one Site model | Obsolete: no link to build |
| `download_window_ends_at` "read from the purge's `PackPurgeRun`, which stores it because T8 clears it" | There is no `PackPurgeRun` table. `purge.files_deletable(service_end)` reads the end of service live from `billing.end_dates` and compares the Manila date to `PROOF_EXPORT_READY_FROM` (`apps/pack/purge.py:54`) | Obsolete. The current test is stricter and simpler: the constant must be on or before the **day the service ended**, i.e. the export existed for the whole window |
| Finance alert `proof_export_not_ready` per site and run | Not in the code | Not built; see decision D6 |
| Sequencing (R21-01): `PROOF_EXPORT_READY_FROM` is set before any non-test Pack sale; the Pack sale gate (`place_pack_order`, checkout entry, the switch's `clean()`) refuses a non-test Pack sale while it is None; `BUYER_PORTAL_READY` | Pack sales are **live on staging with real members** (ruling 1100430000509745) and `PROOF_EXPORT_READY_FROM` is `None`. There is no `BUYER_PORTAL_READY` or `buyer_portal_enabled`. A sale gate added now would shut live sales | **Obsolete, and not to be built.** The premise "no site can have a window that ended before the export existed" is false in staging. What replaces it is L72 plus the existing fail-closed purge: a site whose service ended before the constant is set keeps its proof images (Finance-only, no route), no automatic deletion (decision D5) |
| "Image-deletion part of the Pack purge must not ship or run before this extension" | Already satisfied the other way round: the purge shipped (BP-3, PR #152) with the constant `None` and `files_deletable` fails closed; no proof file is deleted by the purge | Satisfied; this plan only turns it on (section 5) |
| Buyer portal task "waits on" this task | The portal and the purge are already on `main` | Obsolete dependency |
| Waits on G1 | Nothing to wait for | Obsolete |
| Acceptance: the export refuses outside the window, as today | It does not today | Re-stated as new work |
| Acceptance: a replay writes nothing twice | Not in the code today | Re-stated for files and audit (section 4.6) |

Still valid and kept: L65, L67, L70, L71, L72 as written in the task; fail closed; the export is read-only apart from its output and one audit entry; images are Subscriber store data and are handed to the Subscriber only; no deletion waits for the Subscriber.

## 3. What exists, what is missing

Exists on `main`:

- `export_account` (JSON, account level). Its Pack part, `pack_retention.export_packs_of(account)` (`apps/pack/retention.py:88`), holds `sante_registrations`, `pack_orders`, `pack_proof_submissions` as plain values: no file key, no token, no row number, **no image**, **no file name or checksum**, no decisions, no agreement or enrollment records. It is switched by `PACK_ENABLED` (answers `{}` while off).
- A private store (`apps/common/private_files.py`): key only, no URL, `read(key)` returns bytes. Proof images live under `sante/proofs/site-<pk>/<32 hex>.<ext>`, at most 4 MiB, JPEG, PNG or WebP (`apps/pack/constants.py`). The owner dashboard reads one with `private_files.read` after its own check (`apps/pack/api_owner.py:191`).
- The day-90 purge (`apps/pack/purge.py`) with `PROOF_EXPORT_READY_FROM: date | None = None` and `files_deletable(service_end)`. `PACK_PURGE_ENABLED` is off everywhere.
- Account disposal (`apps/pack/retention.py:blank_packs`) deletes every proof file **unconditionally** for a person's own request. PR2 adds `keep_files` / `keep_pack_files` and makes the executor's account step call `purge.files_deletable` for every site of the account (`_pack_files_stay`, `apps/lifecycle/erase.py:88-97`). So PR2 already honours the constant; nothing in this task needs to edit the executor.
- Dates: `billing.end_dates(site)` returns `(service_end, deletion_due)`; the G4b email promises 30 days "to ask" and tells the owner to write to support (`apps/billing/notices.py:212`). It deliberately says nothing about images.

Missing:

1. The proof images and a checksum manifest in any export.
2. The rest of the Pack evidence (decisions, agreement and enrollment records).
3. A window check, so a Pack export is "available for the whole 30 days" and not after.
4. Setting `PROOF_EXPORT_READY_FROM` (never, until the export is accepted).
5. Runbook text and tests for all of the above.

## 4. Design

### 4.1 Shape: one new support command, the existing export untouched in kind

`manage.py export_pack_files ACCOUNT --site SITE --actor USERNAME --request-ref REF (--out-dir DIR | --stdout-base64)`

A new command, not a flag on `export_account`, for four reasons. `export_account` is JSON for a whole account with no window; this export is binary, per site and window-gated. Mixing them would put a window refusal into a command DPO also uses for privacy access requests. The runbook already tells the operator to run `export_account` first; a second named step is clear. And the audit meaning differs.

Files (all new unless named):

- `apps/pack/export.py`: `build_site_archive(site, now, destination)`, pure functions, no I/O except the private store read and the destination file.
- `apps/pack/management/__init__.py`, `apps/pack/management/commands/__init__.py`, `apps/pack/management/commands/export_pack_files.py`: arguments, staff check, window check, audit, output. It reuses `apps.accounts.management.commands._shared` (`add_arguments`, `staff_user`, `account_for`) so the actor and request-reference rules are the same as `export_account` (an active staff user; the reference is a number, never an address).
- `apps/pack/retention.py`: `export_packs_of(account)` becomes a thin call to a new `export_packs(sites)` that the archive also uses, so the JSON export and the archive cannot drift. Same output as today for `export_account` (its tests stay green); the file name and checksum fields are added only in the archive.
- `apps/operators/audit.py`: one new `KEPT` action, `pack_files_exported` (the audit test requires every declared name to be used in code, so it ships with its use).

### 4.2 What is in the archive, and what never is

One ZIP (stored, no compression for images, deflate for JSON), fixed internal timestamps, entries in sorted order:

```
pack-export-<site public id>/manifest.json
pack-export-<site public id>/pack.json
pack-export-<site public id>/proofs/<order number>-<seq>.<jpg|png|webp>
```

`pack.json` (the Subscriber's own site rows only, plain values):

- `orders`: as `export_packs_of` today (public id, number, member public id, buyer name, delivery address, price, currency, status, dates, policy version) plus nothing internal.
- `proofs`: as today, plus `file` (the entry name, or `null`), `sha256`, and for a proof with no file the reason. Sender name, sender bank, sender account number, reference, amount, transfer time, the paid-to account snapshot (provider, name, **last four only**), submitted time, content type, size. **Not** `ip_advisory`.
- `decisions` (`PackProofDecision`): proof public id, decision, reason code, other text, actor type, decided at. Not `actor_ref` or `actor_label`.
- `order_events` (`PackOrderEvent`): kind, actor type, time. The `old`/`new` JSON is left out (it may hold an address or an internal field; routine detail, reversible).
- `agreement_acceptances` and `enrollment_authorizations`: the retained records the task lists: agreement and version identity, `sha256`, accepted at; enrollment text version, the exact text, accepted at. These hold no buyer personal data and are the Subscriber's proof of what a buyer agreed.
- `registrations`: as `export_packs_of` today (date of birth, usernames, status) because they are Pack store data of this site and the purge erases them at day 90 or earlier. (Members' names, emails and mobiles stay in the `members` part of `export_account`; the order's `member` public id joins the two. The runbook step runs both.)

Never in the archive: `file_key`; checkout tokens; `token_hash`; session rows; sign-in code rows; any database row number; `actor_ref`; visitor IP addresses; the full account number of a receiving account (only `account_number_last4` exists in the snapshot); any provider credential or webhook secret (there is none in the Pack tables; test T7 asserts no field name containing `key`, `token`, `secret` or `hash` other than `sha256` appears in `pack.json`); anything of another site or another account.

`manifest.json`: `format` 1, site public id, `exported_at`, `window` (service ended at, window closes at, Manila and UTC), `pack_json` name and sha256, `files` (entry name, proof public id, order number, content type, byte size, sha256), `no_file` (proof public id and reason `key_empty`), `counts` (orders, proofs, files, bytes), and `warnings` (a stored size that differs from the bytes read). The sha256 is computed on the bytes read from the store, not trusted from a column, and written to the archive in the same pass (one read per file).

### 4.3 Reading the images

Each file is read with `private_files.read(key)` inside the command. Rules:

- Empty `file_key`: the proof has no file (already blanked by a disposal, or a store failure on a past delete); listed under `no_file`, not an error.
- Any `StoreError` or `StoreUnavailable`: the whole export fails with no output and **no audit entry**; the operator retries. A half export is never reported as complete. (`BlobStore` cannot tell "gone" from "down"; the safe reading is failure.)
- Total archive larger than `MAX_ARCHIVE_BYTES = 200 MiB` (a choice, a constant): refuse with a clear message and tell the Supervisor; no output. Proof images are at most 4 MiB, so this is about 50 maximum-size images; a store with more needs a part-by-part option that is not built now (decision D4).
- The archive is built into a 0600 temporary file in the system temp folder, deleted at the end in `finally`, so memory stays flat.

### 4.4 Access control and window checks

- **Who.** Same as `export_account`: an active staff user as `--actor`, a request reference number, and the runbook's identity check (the request comes from the account's registered email; the file goes only to that address, from the DPO mailbox, then the operator's copy is deleted). One staff account exists for months (rulings 509990, 510008); no staff-user feature. Nothing is exposed over HTTP: **no route, no new Subscriber or member surface** (so no tenant-isolation route test is needed, and the existing one cannot be weakened).
- **Which rows.** `--site` is a site public id and must belong to `ACCOUNT` (`Site.objects.filter(account=account, public_id=...)`); anything else answers "No such site for this account" and reads nothing. A disposed account is refused (`AlreadyDisposed`, as `export_account`). Every query filters `site=<that site>`; a test with a second account's site proves nothing of it appears.
- **Switch.** `PACK_ENABLED` must be on; off, the command refuses ("Pack is off here") rather than writing an empty archive that looks complete. No new switch.
- **Window.** Let `service_end, deletion_due = billing.end_dates(site, now)`. The command runs only when that is not `None` (the site is ended: suspended, cancelled after its end, or cancelled with no payment method; a served, trial-running, exempt or reactivated site answers "not in its export window") and
  `service_end <= now <= service_end + plan.DOWNLOAD_WINDOW` **and** `now < deletion_due` when `deletion_due` is set. The last clause matters: a site that never paid has its deletion point **exactly at** the end of the 30 days (`DELETION_NEVER_PAID` = `DOWNLOAD_WINDOW` = 30 days, `apps/billing/plan.py`), so the export must refuse from the same instant the purge may run. The boundary tests (T4) use both edges and the never-paid case.
- **Not covered by the window:** a site not yet ended. A served owner can open each proof in the dashboard (`api_owner.py`); L65 and L67 are about the post-service window. See D3 for a person's own access or deletion request on a live site.
- **Removed sites.** A site with `deleted_at` set is still exported inside its window (the purge reaches removed sites too).
- **Read-only.** The only writes are the output and one audit entry.

### 4.5 Audit

One `OperatorAuditEntry`, action `pack_files_exported`, `KEPT`, `target_type="site"`, `target_id=<site public id>`, `reason=<request ref>`, `detail={"counts": {...}, "bytes": n, "archive_sha256": "...", "basis": "window"}`. No value, no name, no address, no file key. Written inside the same transaction as the hand-over, and after the archive was assembled in full, like `export_account`: a failure before output leaves no entry. For `--stdout-base64` the entry means "prepared and streamed"; a broken pipe after the entry cannot be undone (the runbook says to run it again and note the earlier reference).

### 4.6 Output and idempotence

- `--out-dir DIR`: writes `pack-export-<site public id>-<request ref>.zip` mode 0600, `O_EXCL`. The same site and reference again refuses ("already exists, nothing written") and writes **no second audit entry**. The service disk is lost on restart, so this is for local use and tests; production use is the next mode.
- `--stdout-base64`: prints the archive as base64 text (the runbook already prints JSON and redirects it through `railway ssh`; a pty is not binary-safe, base64 is). The operator runs `tr -d '\r' < in.txt | base64 -d > pack-export.zip` and checks the printed archive sha256 line (on stderr). A repeat run is allowed and writes one more audit entry each time (each is a hand-over); the content is the same except `exported_at`, because the entries, order and timestamps are fixed. The image sha256 values are always the same for the same files.
- Counts and the archive sha256 go to stderr; the personal content never goes to a log (the existing rule).

### 4.7 What it does to the purge

Nothing in `apps/pack/purge.py` changes in the build branch. The purge already reads only the constant. The deletion gate is wired as the executor plan wants it: purge and the executor's site step through `purge_site`/`files_deletable`; the executor's account step through `_pack_files_stay`; a person's own `dispose_account` unchanged (it deletes files; D3 covers the export before it).

## 5. When and how `PROOF_EXPORT_READY_FROM` is set

The constant is a date in code (`apps/pack/purge.py`). `files_deletable` allows deletion of a site's proof files only if `ready <= Manila date of its service end`, so the date must be **no earlier than the first day on which the export was really available** in the environment that holds the data. A literal chosen before the deploy could be wrong if the release slips. Therefore:

1. **Branch A (the build, section 4).** Constant stays `None`. Test T10 asserts it is `None` and that no proof file is deleted by `purge.run` or by the executor with it `None` (the existing purge tests, kept).
2. **Verification**, in order: local checks and a local rehearsal on a throwaway PostgreSQL with `PRIVATE_FILES_DIR` (images of different types, a never-paid site, a second account); the release PR merged; the staging deploy verified by the Supervisor; one smoke run of the command on staging against Coach Ed's test sign-up account (the approved "export dry run with his test sign-up", rulings 512065, items 3 to 9), which proves the command loads, the window refusal and the audit entry. No test Pack order or proof is created on staging data (ruling 509745) without his word (D2).
3. **Branch B (one line).** After the Supervisor reports the verified staging deploy, a second tiny branch sets `PROOF_EXPORT_READY_FROM = date(Y, M, D)` where `D` is the **Manila day after** the verified staging deploy day (so every site whose service ends on or after `D` has the export for its whole window), and replaces the "stay while None" test with: images go when `ready <= service end day`; stay when the site ended earlier. It rides the next release round; nothing else changes.
4. **What turns on.** Setting the constant does not delete anything by itself: deletion also needs `PACK_PURGE_ENABLED` (off, Coach Ed's word per environment), or an approved executor batch (`SITE_ERASURE_ENABLED`, off). Sites whose service ended before `D` keep their images for ever (L72, fail closed, Finance-only, no route). Production is not built; the constant is the same code there, and the production database is moved from staging, so its sites carry the same service-end dates.
5. **Never** set it in branch A, in the executor branch, or in a release round together with the first deploy of A.

## 6. Tests (all in `apps/pack/tests/test_export.py` and `apps/accounts/tests/test_export.py` kept green)

Style: Django `TestCase`, the existing pack bases, `LocalStore` under a temp dir, SQLite and PostgreSQL.

- T1 **Contents.** A cancelled site inside its window with 2 orders, 3 proofs (JPEG, PNG, WebP), decisions, an acceptance and an enrollment: the ZIP holds every file, each manifest sha256 equals `sha256(bytes)` of what was stored, `pack.json` lists every order and proof.
- T2 **Image held before the purge.** The same site: export, then run `purge.purge_site` with the constant mocked to a past date; the archive made before holds the image; after the purge the file is gone. (The task's "a test with an image the purge would delete".)
- T3 **Nothing else.** A second site of the same account and a second account's site, each with Pack rows and files: none of their ids, numbers, names or files appear in the archive or manifest; `--site` of another account's site is refused and reads no row.
- T4 **Window.** Refused: served site; trial running; exempt; reactivated; cancelled inside the paid period; before service end; after end plus 30 days; a never-paid site at and after its deletion instant (`now == deletion_due` refused; one second before accepted). Accepted: exactly at service end and at the last second. Each refusal writes no audit entry and no file.
- T5 **Access.** Inactive or non-staff `--actor` refused; `--request-ref` containing `@` refused; no account or site mismatch; disposed account refused; `PACK_ENABLED` off refused. No URL pattern, no new route (an assertion over the URL conf is unnecessary; the isolation test over `routes_under_a_site` stays unchanged and green).
- T6 **File mode and replay.** `--out-dir` file is 0600 and `O_EXCL`; the same call again refuses, writes no second file and **no second audit entry**; a different request ref writes a new file and one entry.
- T7 **Forbidden content.** No `file_key`, key path, token, hash (other than `sha256`), IP, row number, full account number, `actor_ref` or `actor_label` in any JSON of the archive; the receiving account's number appears nowhere.
- T8 **Store failures.** `StoreError` on the second file: command fails, no output, no audit entry; a proof with an empty key lands in `no_file`, export succeeds; a stored size that differs from the bytes read lands in `warnings`; an archive over `MAX_ARCHIVE_BYTES` (constant patched) is refused.
- T9 **Audit.** One `pack_files_exported` entry per successful hand-over with counts, bytes and the archive sha256, `KEPT`, no personal value; the audit-actions-used test still passes.
- T10 **Gate unchanged.** `purge.PROOF_EXPORT_READY_FROM is None`; with it `None`, `purge.run` and the executor delete no proof file (existing tests in `test_purge.py` and the executor's, kept).
- T11 **No write.** The command changes no Pack row (row counts and a checksum of the evidence tables before and after) and nothing else but the audit table.
- T12 **`stdout-base64`.** Output decodes to the same bytes whose sha256 the stderr line states; carriage returns removed from the redirect decode too (runbook command tested in the test).
- T13 **Existing export.** `export_account` output for Pack is byte-for-byte what it is today (`export_packs_of` refactor).

Repository checks before reporting ready: the targeted tests, the full suite on SQLite and on a throwaway PostgreSQL with a local Redis, `check --deploy`, `makemigrations --check`, migrations on a fresh PostgreSQL (the release builder's list, rulings 509948).

## 7. Runbook changes

- `docs/runbooks/retention.md`, "An export request": add "The Pack files of an ended site" (when it applies, the command, the base64 redirect with `tr -d '\r' | base64 -d`, what the archive holds and never holds, the window and why it refuses outside it, the audit entry, how to send it, delete your copy). Update the line "The export gains `pack_orders` and `pack_proof_submissions` ..." to say images and checksums are in the Pack archive, not in `export_account`. Update the "Proof files" paragraph of the Pack purge section: how the constant gets set (this plan, section 5), and that a site whose service ended before the date keeps its images (L72).
- `docs/runbooks/support-and-exit.md`, section 4 step 3: after `export_account`, if the site is in its 30 days and sold Membership Packs, run `export_pack_files`; send both files together; and section 5's "windows" bullet: the Pack archive is refused outside the window. Section 4 keeps the rule that staging or production runs need Coach Ed's word.
- `docs/runbooks/pack.md`: one line pointing to the two sections.
- No change to Terms, Privacy or any published page, and none to the G4b email in this branch (see D7).

## 8. Migration, switches, PR count

- **Migration: none.** No model changes. The audit action is a declared string; `OperatorAuditEntry.action` is a plain `CharField`. The 2026-11-11 standing migration approval is not used.
- **Switches: none new.** Needs the existing `PACK_ENABLED`. No variable changes.
- **Branches / PRs.** One backend feature branch A (`feat/z8uq9mbu0p-export-pack-files`), reported ready to the Supervisor, then one release PR through the release train. A second one-line branch B (section 5) in a later round. No frontend change. If decision D3 is refused, branch A is unchanged except for one flag.
- Builder: Sonnet 5.5, medium; the Supervisor starts it and reviews. Estimated size: about 350 lines of code, about 500 lines of tests.

## 9. Overlaps (open PRs and branches touching the same files)

There is **no open PR** (checked `gh pr list --state open`, empty). Unmerged branches that touch the same files:

| Branch | Files in common | Handling |
|---|---|---|
| `origin/feat/z8uq9mb22v-deletion-executor-pr2` (head `691988b`; executor PR2, not merged) | `apps/pack/retention.py` (adds `keep_files` to `blank_packs_of`), `apps/pack/purge.py` (hold check in `purge_site`; this plan changes purge only in branch B, one line), `apps/operators/audit.py` (new action names, same dict), `docs/runbooks/retention.md`, `docs/runbooks/support-and-exit.md` | Text conflicts only, no logic. Merge order: PR2 first, then A; A is rebased by the builder on the merged result, and the release builder keeps both sides in the audit dict and the runbooks. Branch B must follow both. Nothing in A needs PR2; PR2 already honours `files_deletable`, so A, PR2 and B in any order never delete a file early |
| `origin/feat/z8uq9mbbkj-owner-export-notice` | merged in round 5 (#153) as `apps/billing/notices.py` | None: A does not edit notices |
| `origin/feat/z8uq9me9mr-pack-checkout`, `origin/feat/z8uq9mek5x-owner-row-tracking-r2` | already on `main` (older branches) | None |

## 10. Owner decisions

Each has one recommendation, the risk and the approving words. The Manager may decide D1, D2, D4, D5 (inside L67, L71, L72) and D6; D3, D7 touch privacy or customer wording and go to Coach Ed. Approving the plan as a whole is one sentence at the end.

| # | Decision | Recommendation | Risk | Approving words |
|---|---|---|---|---|
| D1 | Set the constant in a separate one-line branch B after the verified staging deploy (section 5), not inside the build | Yes. A literal date written before the deploy can be earlier than the true availability day if the release slips, and deletion of a buyer's proof image cannot be undone | Two branches instead of one; B waits for a verified deploy | "Set PROOF_EXPORT_READY_FROM in a separate branch after staging is verified." |
| D2 | Staging verification of the images: smoke only (Coach Ed's test sign-up, no Pack order), images proven locally | Yes. Staging holds live Santé members (509745): no test order or proof on it | The staging image path (Blob read) is proven only by the already-live owner dashboard read plus local tests; a first real export is the first Blob run of the archive | "Staging check is the smoke run only; no test Pack order on staging." (Alternative: he allows one test order and proof on a named test site, then the full run) |
| D3 | A person's own access or deletion request on a **live** site (no window): `dispose_account` deletes proof files for a person's own request, with no image export first | Yes, add `--basis request` to the same command: it skips the window check but still needs a staff actor, a request reference, an account with Pack files, and records `basis` in the audit entry; the runbook runs it before any `dispose_account` of an account that sold Packs. About 20 lines and one test. Alternative: window only (the task text) and a runbook warning | Without it a Subscriber who deletes his account loses buyer proof images with no export, against the spirit of L65/L67; with it the DPO path gets a wider read of the same data | "Add --basis request for a person's own request." |
| D4 | Archive size limit and no parts option: refuse above 200 MiB | Yes (a choice). A part-by-part export waits until a real site exceeds it | A very large store cannot be exported through one ssh call until built; the refusal is loud, the proof images stay | "200 MiB limit; build parts only if needed." |
| D5 | Drop the Pack sale gate (refuse a non-test Pack sale while the constant is None, plus the switch `clean()` refusal) | Yes, drop it. Sales are live (509745); L72 already rules the residual case (images of a site cancelled before the constant is set stay Finance-only, no automatic deletion) | A site that ends before the date keeps its proof images indefinitely, and that is counted in the storage ledger; a later explicit retention rule is needed to remove them (as L72 says) | "Do not build the sale gate; L72 covers sites ended before the date." |
| D6 | The Finance alert `proof_export_not_ready`: not built (it does not exist in this code) | No new alert now. The purge already keeps the file and the storage ledger counts it; add one only with a later approved retention rule | Nobody is told that a site's images are being kept by the gate | "No alert for kept proof images now." |
| D7 | Words for the owner: the G4b service-end email and the Billing-only screen stay as they are (they promise the export of store data, no images) | Keep unchanged until branch B is on staging; then Coach Ed may approve one added sentence ("including the payment-proof images your buyers sent") as a separate wording task. No policy page changes (L70). The Manager's rule stands: no text promises images before the export exists | If the email says nothing, an owner may not know the images are in; if it said so early it would be false | "Leave the notices unchanged; I will approve a one-sentence addition after B." |

One sentence for the whole plan: **"Approved: the Pack store-data export plan z8uq9mbu0p as written (docs/superpowers/plans/2026-11-13-export-pack-proofs-z8uq9mbu0p.md), decisions D1 to D7 as recommended."** This approves the build of branch A (no migration). It approves no switch, no variable, no staging data work beyond D2's smoke run, no setting of `PROOF_EXPORT_READY_FROM` before section 5 step 3, and nothing in production.

## 11. Risks, in one place

- Deleting a proof image is irreversible, so the constant is the last step and has its own branch (D1).
- A pty can corrupt binary output; base64 plus a printed sha256 is the guard, tested (T12).
- The window is the single clock: the `now < deletion_due` clause (T4) closes the never-paid case where export and purge meet at the same instant.
- Data can change inside the window (a buyer's portal action); the archive is a dated snapshot (`exported_at`).
- PR2 merge order (section 9) is the only coordination point.
