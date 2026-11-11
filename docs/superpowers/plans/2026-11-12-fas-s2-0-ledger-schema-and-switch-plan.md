# FAS stage 2, piece S2-0: ledger schema and switch (plan only)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the eight tables of the Financial Accounting System's books into the backend, with the database rules that keep a posted entry unchangeable, and the three switches that keep all of it off. Nothing writes a journal, nothing reads the tables, no route exists, no row is seeded.

**Architecture:** One new Django app, `apps/ledger`, label `ledger`, tables prefixed `ledger_`. `models.py` holds the eight models and the Django-level guards; `managers.py` holds the querysets that refuse bulk changes to posted rows; `switch.py` reads the three switches; `checks.py` registers two startup checks. Migration `0001_initial` creates the tables, constraints and indexes. Migration `0002_postgres_guards` creates the PostgreSQL triggers and is a no-op on SQLite. The posting writer (S2-A), the billing hook (S2-B), access and routes (S2-C), accounts (S2-D) and periods (S2-E) are later pieces and are not here.

**Tech Stack:** Django 5.2, Python 3.12, `uv`, Django's test runner, PostgreSQL 16 in CI. No new dependency.

---

Date: 2026-11-12. ClickUp task: z8uq9md5df (https://app.clickup.com/t/z8uq9md5df), the FAS-04 task, of which this is the S2-0 piece. Master task z8uq9md5db.
Plan branch: `docs/z8uq9md5df-fas-s2-0-plan`, cut from `origin/main` at `34b0d10`. The build branch is cut from the latest `origin/main` once Coach Ed approves this plan.
Status: **plan only; nothing in it is built.** The SQL of section 5 was run on a scratch PostgreSQL 14 against hand-made tables with the same column names (every refusal below fired as described, and the balanced post passed); the Python was not run. Approving the FAS stage 2 split approved no plan: this plan needs Coach Ed's approval before a builder starts, and the approval of this plan is what the standing approval of 2026-11-11 reads as "a migration that an approved plan specifies" (`CLAUDE.md`, `.claude/briefs/coordinator.md` section 10).

## Deviations from FAS-03 section 2.5

Coach Ed approves this plan, so the two places where it departs from the approved text are named here. The coordinator agreed both on 2026-11-12. The FAS-03 spec is not edited in this PR.

### Deviation 1: no TRUNCATE trigger on `ledger_event`

- **Design text (2.5, layer 2):** "`BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` on `ledger_event`: raise."
- **This plan instead:** the `BEFORE UPDATE OR DELETE` trigger only (`ledger_event_append_only`, section 5.2). No TRUNCATE trigger and no session-setting bypass.
- **Why:** Django's `TransactionTestCase` teardown runs one `TRUNCATE` over every table of every installed app, and `apps/sante`, `apps/catalog` and `apps/sites` each have such tests. With the trigger, the existing PostgreSQL CI job fails at the first teardown after `apps.ledger` is installed, and so would S2-A's two-thread test. **Evidence**, run 2026-11-12 on a scratch PostgreSQL 14 (`postgresql://postgres@127.0.0.1:5499/scratch`, nothing shared): `uv run python manage.py test apps.sites.tests.test_concurrency --keepdb` passed (`OK`); after adding `CREATE TRIGGER ... BEFORE TRUNCATE ON sites_site FOR EACH STATEMENT` (a stand-in table, the ledger tables not existing yet, raising with the same `RAISE EXCEPTION ... ERRCODE 'check_violation'` as section 5.2) the same command ended `FAILED (errors=1)` with `psycopg.errors.CheckViolation: ledger: the change history is append-only` raised through `django.db.utils.IntegrityError` in the teardown `flush` ("Database ... couldn't be flushed"). The build repeats this on the real `ledger_event` in task 5, step 1; if it does not fail there, the trigger is kept as designed.
- **Protection lost:** a session that is the table owner can empty `ledger_event` with `TRUNCATE`, which the design's trigger would have stopped. `UPDATE` and `DELETE` of its rows stay refused by the database and by every Django path.
- **Alternative not taken:** keep the trigger and let it pass when a session setting (for example `ledger.allow_truncate`) is `on`, set by the test runner. It needs a test-only hook that every test run, including `manage.py test apps.billing` alone, must set, and it is a switch that disables the protection. The design's layer 3 already says the triggers defend against the application and mistakes, not against the database owner, who can drop a trigger as easily; the seal chain (S2-A, FAS-06) is what reveals a tampered history.

### Deviation 2: the period trigger refuses only `locked`

- **Design text (2.5, layer 2):** "The period lock is checked in the same posted-transition trigger: the period must be `open` (or `soft_closed` with an entry kind FAS-06 allows [A])."
- **This plan instead:** `ledger_entry_guard` refuses a draft-to-posted transition into a `locked` period and does not look at `soft_closed`. Whether a `soft_closed` period takes an entry stays the writer's rule (design 3.3 step 3), as design 2.3 already says: "what `soft_closed` still allows ... is FAS-06's rule".
- **Why:** FAS-06 (S2-E) has not named the kinds a `soft_closed` period accepts. A trigger that refuses all of them would block the owner's adjustments; a trigger that allows a guessed list would be wrong the day FAS-06 decides, and either way a later migration would have to replace the function. Leaving it out means S2-E needs no trigger migration.
- **Protection lost:** a write that bypasses the writer (raw SQL, a bug in a future module) can post into a `soft_closed` period; the database still stops it in a `locked` one. Django-level code cannot do it through the writer.
- **Alternative not taken:** refuse `soft_closed` in the trigger now, and have S2-E replace the function in `0003`. That keeps the database strict but couples S2-0 to a policy not yet written and adds a trigger migration to stage 2.

## 1. Sources

| Short name | Source |
|---|---|
| Design | `docs/superpowers/specs/2026-11-11-fas-03-ledger-data-model-and-access.md` (FAS-03), approved 2026-11-12 as amended by the rulings: sections 2.3 to 2.5, 6.1, 6.3, 6.5, 8 (row S2-0) and 10 |
| Rulings | FAS-00 comments, 2026-11-11 and 12: rulings 7 (owner-only through a `LedgerAccess` row; a superuser without a row has none; no CPA login), 11 (no tax accounts or tax lines in journals), 12 (no suspense account), 13 (corrections by reversal only), 14 (system key `cash.bank_transfer_default`, no receiving-account field on the payment), 15 (staging holds rehearsal books only; live books only where `LEDGER_LIVE_BOOKS=1`) |
| Naming settled by the coordinator | Idempotency key `source_type:source_ref:source_event`; the rule version is a column, not part of the key; `system_key` on accounts; account type `revenue`; period states `open`, `soft_closed`, `locked`; journal status `draft`, `posted` |
| Task | ClickUp z8uq9md5df, read 2026-11-12: no comments. Its text is the FAS-04 engineering contract; its mentions of the legacy twcinc repository are reference only |
| Code | `apps/inbox/{apps,switch,models}.py`, `apps/billing/proofs.py` (a registered check), `apps/notifications/{apps,checks}.py`, `apps/common/ids.py`, `config/settings.py` lines 95 to 175, `.env.example`, `.github/workflows/ci.yml` (jobs `django` and `postgres-and-redis`), `docs/runbooks/migrations.md`, `apps/billing/tests/test_plan_snapshot_migration.py` (a migration test); all at `34b0d10` |

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | The app `apps/ledger` and `'apps.ledger'` in `INSTALLED_APPS` | Design 8 (S2-0 row) |
| 2 | The three switches in `config/settings.py` and the three lines in `.env.example`, all off by default | Design 6.1, 6.5 |
| 3 | `switch.py` (`ledger_on`, `post_billing_on`, `live_books_on`, `tables_exist`) and `checks.py` (two startup errors) | Design 6.1 |
| 4 | The eight models of design 2.3 with every field, constraint and index | Design 2.3 |
| 5 | The Django-level guards: model methods and the querysets in `managers.py` | Design 2.5 layer 1 |
| 6 | `ledger.0001_initial` | Design 6.3 row 1 |
| 7 | `ledger.0002_postgres_guards` | Design 2.5 layer 2, 6.3 row 2 |
| 8 | Model, guard, switch, check and trigger tests, on SQLite locally and on PostgreSQL in CI | Design 2.5 "Testing" |

### 2.2 What is not built (and nothing here may be)

- **No posting writer**, no `posting.py`, no `canonical.py`, no payload hash, no seal, no sequence number assigned, no idempotent replay: S2-A. The columns exist; nothing fills them but the tests' own helper.
- **No billing hook**, no `apps/billing/hooks.py`, no change to `apps/billing/services.py`, no catch-up command: S2-B.
- **No access check, no staff route, no management command**, no `staff_views.py` or `audit.py` edit, no HQ menu entry: S2-C. In particular `ledger_access` and `ledger_open_book` do not exist yet, so on staging no book can be opened until S2-C. That is intended.
- **No chart, no account, no period row, no book row, no data of any kind.** Migration `0001` contains no `RunPython` and no `RunSQL`; migration `0002` creates functions and triggers and touches no row. The chart is loaded later by an approved command (design 6.3, FAS-32). A test holds this (task 8).
- **No Django admin screen** for any ledger model (design 5.3, ruling "no new Django admin screens"). A test holds it.
- **No tax account or tax column.** The schema has no tax field and no tax-related choice (ruling 11). No suspense account concept (ruling 12). No receiving-account column anywhere, and nothing on `billing_banktransferpayment` (ruling 14).
- **No Railway variable, no staging or production action by the builder**, no switch turned on, no beat entry. The coordinator runs the migration on staging (section 8) under the standing approval; production is never covered.
- Nothing from design section 2.6 (placeholders for later stages), and no change in the frontend repository.

### 2.3 Files

| File | Change |
|---|---|
| `apps/ledger/__init__.py` | empty |
| `apps/ledger/apps.py` | `LedgerConfig`; `ready()` imports `checks` |
| `apps/ledger/switch.py` | new |
| `apps/ledger/checks.py` | new |
| `apps/ledger/managers.py` | new |
| `apps/ledger/models.py` | new |
| `apps/ledger/migrations/__init__.py`, `0001_initial.py`, `0002_postgres_guards.py` | new |
| `apps/ledger/tests/__init__.py`, `base.py`, `test_switch.py`, `test_checks.py`, `test_app.py`, `test_models.py`, `test_guards.py`, `test_postgres_guards.py` | new |
| `config/settings.py` | **shared**: one added block and one added list line (section 6) |
| `.env.example` | **shared**: one added block (section 6) |

No other file changes. In particular not `docs/runbooks/` (the accounting runbook is a later piece), not `apps/operators/*`, not `apps/billing/*`.

## 3. Decisions and conflicts to read before building

### 3.1 The two deviations

See "Deviations from FAS-03 section 2.5" above (no TRUNCATE trigger; the period trigger refuses only `locked`). If either is overruled, only section 5.2 and task 5 change.

### 3.2 Routine decisions

Each is reversible and inside the approved design; the report to the coordinator repeats them.

| # | Decision | Why | Source |
|---|---|---|---|
| D1 | Deviation 2 above: the period trigger refuses `locked` only | see Deviations | FAS-03 2.5 |
| D2 | Every choice column gets an enumerated `CheckConstraint` (`kind`, `status`, `type`, `normal_side`, `level`) | The triggers compare against literals such as `'posted'`; a value outside the vocabulary must not exist | Design 2.5 (the guarantees rest on those values) |
| D3 | A new `JournalEntry` can only be inserted as `draft` (Django guard); a posted entry arises only by the draft-to-posted update | Closes inserting a row already posted. On PostgreSQL the deferred balance trigger and the line trigger already make such a row useless | Design 2.5 layer 1 |
| D4 | The commit-time trigger also re-checks every line against its account (header, other book, effective dates) | The line trigger runs when a line is written; an entry date edited afterwards must be caught at the transition | Design 2.5 layer 2 (line rules) |
| D5 | `RecognitionSchedule` rows get the append-only guard that `LedgerEvent` has (save on an existing row, delete and queryset update/delete refuse). No trigger | Design 2.3: rows "are never changed" | Design 2.3 `ledger_recognition` |
| D6 | The querysets of `AccountingEntity`, `GLAccount` and `FiscalPeriod` also refuse `delete()` | A model's `delete()` is not called by `QuerySet.delete()` | Design 2.5 ("`delete()` raises") |
| D7 | `switch.live_books_on()` is `LEDGER_ENABLED and LEDGER_LIVE_BOOKS`; `post_billing_on()` is `LEDGER_ENABLED and LEDGER_POST_BILLING_ENABLED` | A running service treats a half-set pair as off; the startup check reports it | Design 6.1 (`inbox/switch.py` and `billing/proofs.py` pattern) |
| D8 | All raises from triggers use `ERRCODE = 'check_violation'`, which Django surfaces as `IntegrityError` | One exception type for the tests and for the writer to catch | `apps/inbox/tests/test_models.py` (`refused`) |
| D9 | The `created_by`, `granted_by` and `operator` foreign keys use `related_name="+"` (no reverse accessor) except `LedgerAccess.operator` (`ledger_access`) | Keeps `User` free of ledger names | `InboxAccess` |
| D10 | `source_detail` and `detail` are `JSONField(default=dict)`; `payload_hash`, `seal`, `rule_version`, `last_seal` default to `""` | A draft has no seal; the writer fills them; "posted" requires a non-blank seal by constraint | Design 2.3 |

### 3.3 Interactions with the rulings of 2026-11-12

- Ruling 7 (owner only): the table `ledger_access` and its `owner` and `viewer` levels are in the schema as the design has them. Nothing grants a row in S2-0. Only `owner` is ever granted in v1.
- Ruling 14: `GLAccount.system_key` exists; no account is created, so `cash.bank_transfer_default` is attached to account 1020 by the later chart-loading command, never by a migration.
- Ruling 15: `AccountingEntity.kind` is `live` or `rehearsal`; at most one `live` row; `kind` cannot change after creation. The switch `LEDGER_LIVE_BOOKS` is read by `switch.live_books_on()`; the code that refuses a live book is S2-C and S2-A's, which call it.

## 4. Models, exactly

Conventions (design 2.3): `id` is the default `BigAutoField`; `created_at` is `auto_now_add`; every `ForeignKey` is `PROTECT` except `JournalLine.entry`, which is `CASCADE`; money is `BigIntegerField` centavos; `Meta.db_table` is the table name below; every public id uses `apps.common.ids.new_public_id` with the prefix shown (`ent`, `gla`, `per`, `jrn`, `lev`); `AUTH_USER_MODEL` is `operators.User`. Constraint and index names are given and must be used exactly, so a later migration can name them. Index names are at most 30 characters.

### 4.1 `AccountingEntity`, table `ledger_entity`

| Field | Definition |
|---|---|
| `public_id` | `CharField(40, unique, default=new_entity_id, editable=False)` |
| `name` | `CharField(120)` |
| `kind` | `CharField(12, choices live/rehearsal)` |
| `books_start_date` | `DateField(null=True, blank=True)` |
| `currency` | `CharField(3, default="PHP")` |
| `first_fiscal_month` | `SmallIntegerField(default=1)` |
| `next_posting_seq` | `BigIntegerField(default=1)` |
| `last_seal` | `CharField(64, blank=True, default="")` |
| `created_at` | `DateTimeField(auto_now_add=True)` |
| `created_by` | `ForeignKey(AUTH_USER_MODEL, PROTECT, related_name="+")` |

Constraints: `ledger_entity_name_unique` (unique `name`); `ledger_entity_one_live` (unique on `kind` where `kind='live'`); `ledger_entity_kind_valid`; `ledger_entity_currency_php` (`currency='PHP'`); `ledger_entity_first_month_range` (1 to 12).
Guards: `delete()` raises; `save()` on an existing row raises if `kind` differs from the value loaded (`from_db` keeps it).

### 4.2 `GLAccount`, table `ledger_account`

| Field | Definition |
|---|---|
| `public_id` | `new_gl_account_id`, prefix `gla` |
| `entity` | `ForeignKey(AccountingEntity, PROTECT, related_name="accounts")` |
| `system_key` | `CharField(40, blank=True, default="")` |
| `code` | `CharField(16)` |
| `name` | `CharField(160)` |
| `type` | `CharField(10, choices asset/liability/equity/revenue/expense)` |
| `normal_side` | `CharField(6, choices debit/credit)` |
| `is_contra` | `BooleanField(default=False)` |
| `reporting_group` | `CharField(60, blank=True, default="")` |
| `parent` | `ForeignKey("self", null=True, blank=True, PROTECT, related_name="children")` |
| `is_postable` | `BooleanField(default=True)` |
| `effective_from`, `effective_to` | `DateField(null=True, blank=True)` |
| `status` | `CharField(8, choices active/retired, default="active")` |

Constraints: `ledger_account_code_unique` (`entity`, `code`); `ledger_account_system_key_unique` (`entity`, `system_key`, where `system_key` is not blank); `ledger_account_type_valid`; `ledger_account_status_valid`; `ledger_account_side_valid` (the normal side of design 2.3: for `asset` and `expense`, `debit` unless `is_contra`, then `credit`; for `liability`, `equity` and `revenue`, `credit` unless `is_contra`, then `debit`; written as four `Q` branches); `ledger_account_effective_order` (`effective_to` or `effective_from` null, or `effective_to >= effective_from`).
Guard: `delete()` raises (D6 for the queryset). The "no change of `code` once a line points at it" rule is the writer's (S2-D), not a schema rule.

### 4.3 `FiscalPeriod`, table `ledger_period`

Fields: `public_id` (`per`), `entity` (FK, `related_name="periods"`), `year` and `month` (`SmallIntegerField`), `start_date`, `end_date` (`DateField`), `status` (`CharField(11, choices open/soft_closed/locked, default="open")`), `status_changed_at` (`DateTimeField(null=True, blank=True)`), `status_changed_by` (FK User, null, `related_name="+"`).
Constraints: `ledger_period_unique` (`entity`, `year`, `month`); `ledger_period_month_range` (1 to 12); `ledger_period_dates_order` (`end_date >= start_date`); `ledger_period_status_valid`. Guard: `delete()` raises (D6).

### 4.4 `JournalEntry`, table `ledger_entry`

| Field | Definition |
|---|---|
| `public_id` | prefix `jrn` |
| `entity` | `ForeignKey(AccountingEntity, PROTECT, related_name="entries")` |
| `status` | `CharField(8, choices draft/posted, default="draft")` |
| `kind` | `CharField(10, choices normal/opening/reversal/adjustment, default="normal")` |
| `reason` | `TextField(blank=True, default="")` |
| `rule_version` | `CharField(8, blank=True, default="")` |
| `source_detail` | `JSONField(default=dict)` |
| `entry_date` | `DateField()` |
| `memo` | `CharField(240, blank=True, default="")` |
| `period` | `ForeignKey(FiscalPeriod, null=True, blank=True, PROTECT, related_name="entries")` |
| `posting_seq` | `BigIntegerField(null=True, blank=True)` |
| `posted_at` | `DateTimeField(null=True, blank=True)` |
| `posted_by` | FK User, null, `related_name="+"` |
| `created_by` | FK User, null, `related_name="+"` |
| `idempotency_key` | `CharField(160)` |
| `payload_hash` | `CharField(64, blank=True, default="")` |
| `source_type`, `source_ref`, `source_event` | `CharField(40)`, `CharField(64)`, `CharField(40)`; all `blank=True, default=""` |
| `reverses` | `OneToOneField("self", null=True, blank=True, PROTECT, related_name="reversed_by")` |
| `total_centavos` | `BigIntegerField(default=0)` |
| `seal` | `CharField(64, blank=True, default="")` |
| `created_at` | `DateTimeField(auto_now_add=True)` |

Constraints (all names `ledger_entry_...`): `_status_valid`; `_kind_valid`; `_idem_unique` (`entity`, `idempotency_key`); `_idem_not_blank`; `_source_unique` (`entity`, `source_type`, `source_ref`, `source_event`, where `source_type` is not blank); `_seq_unique` (`entity`, `posting_seq`, where not null); `_one_opening` (unique `entity` where `kind='opening'`); `_total_nonneg` (`total_centavos >= 0`); `_reason_required` (reason not blank, or `kind='normal'` with a non-blank `source_type`); `_reversal_link` (`kind='reversal'` exactly when `reverses` is not null); `_posted_complete` (`status='draft'`, or `period`, `posting_seq`, `posted_at` not null, `seal` not blank and `total_centavos > 0`).
Indexes: `ledger_entry_date_idx` (`entity`, `entry_date`); `ledger_entry_status_idx` (`entity`, `status`); `ledger_entry_period_idx` (`period`).
Guards: `from_db` keeps the loaded status; `save()` raises when the loaded status is `posted`, and raises on insert unless `status='draft'` (D3); `delete()` raises unless `draft`. Queryset: section 4.9.

### 4.5 `JournalLine`, table `ledger_line`

Fields: `entry` (`ForeignKey(JournalEntry, CASCADE, related_name="lines")`), `line_no` (`SmallIntegerField`), `account` (`ForeignKey(GLAccount, PROTECT, related_name="lines")`), `debit_centavos`, `credit_centavos` (`BigIntegerField(default=0)`), `memo` (`CharField(240, blank=True, default="")`), `evidence_ref` (`CharField(120, blank=True, default="")`).
Constraints: `ledger_line_no_unique` (`entry`, `line_no`); `ledger_line_no_positive` (`line_no >= 1`); `ledger_line_one_side` (debit `> 0` and credit `= 0`, or credit `> 0` and debit `= 0`; this also forbids a negative amount). Index: `ledger_line_acct_entry_idx` (`account`, `entry`).
Guards: `save()` and `delete()` raise when the parent entry **in the database** is not `draft`. Queryset: 4.9.

### 4.6 `RecognitionSchedule`, table `ledger_recognition`

Fields: `entity` (FK, `related_name="recognitions"`), `payment_ref` (`CharField(64)`), `period_month` (`CharField(7)`), `service_start`, `service_end` (`DateField`), `amount_to_release_centavos` (`BigIntegerField`), `payment_entry` (`ForeignKey(JournalEntry, PROTECT, related_name="recognitions")`), `created_at`.
Constraints: `ledger_recognition_unique` (`entity`, `payment_ref`, `period_month`); `ledger_recognition_amount_positive` (`> 0`); `ledger_recognition_service_order` (`service_end > service_start`). Guard: append-only (D5).

### 4.7 `LedgerEvent`, table `ledger_event`

Fields: `public_id` (`lev`), `entity` (FK, `related_name="events"`), `kind` (`CharField(40)`), `target_type` (`CharField(24)`), `target_ref` (`CharField(64)`), `actor` (FK User, null, `related_name="+"`), `detail` (`JSONField(default=dict)`), `at` (`DateTimeField(auto_now_add=True)`).
Index: `ledger_event_at_idx` (`at`). The vocabulary of `kind` is not fixed by the schema; S2-A and S2-C declare it in code. Guard: append-only: `save()` on an existing row, `delete()`, and queryset `update`, `delete`, `bulk_update` raise.

### 4.8 `LedgerAccess`, table `ledger_access`

Fields: `operator` (FK User, PROTECT, `related_name="ledger_access"`), `entity` (FK, `related_name="access_rows"`), `level` (`CharField(8, choices owner/viewer)`), `granted_by` (FK User, PROTECT, `related_name="+"`), `granted_at` (`auto_now_add`), `revoked_at` (null), `revoked_by` (FK User, null, `related_name="+"`).
Constraints: `ledger_access_one_per_user_book` (`operator`, `entity`); `ledger_access_level_valid`. No Django admin registration, no guard beyond the constraints.

### 4.9 `managers.py`

Three querysets, each used through `QuerySet.as_manager()` and set as `objects` (and `_default_manager`) on its model:

- `ProtectedQuerySet(QuerySet)`: `delete()` raises `LedgerRefusal("ledger rows are never deleted")`. Used by `AccountingEntity`, `GLAccount`, `FiscalPeriod` (D6).
- `AppendOnlyQuerySet(QuerySet)`: `update`, `bulk_update`, `delete` raise. Used by `LedgerEvent` and `RecognitionSchedule` (D5). `bulk_create` is allowed (inserting is what append-only means).
- `JournalEntryQuerySet` and `JournalLineQuerySet`: `update`, `bulk_update` and `delete` first evaluate `self.filter(<posted>).exists()` (`status="posted"` for entries; `entry__status="posted"` for lines) and raise if any row could be posted; `bulk_update(objs, fields)` checks the objects' primary keys; `bulk_create` raises for an entry with `status="posted"` and for a line whose entry is not `draft`. Draft deletion (the writer's `discard_draft`) uses `entry.delete()`, which cascades to lines through Django's collector and passes the line guards because the parent is `draft`.

The error class is `apps.ledger.managers.LedgerRefusal(Exception)`, used by every guard in this app; a plain subclass is enough, S2-A maps it to `ApiError` where it matters.

## 5. Migrations, exactly

Both are written by hand-checking the output of `makemigrations`: run `uv run python manage.py makemigrations ledger`, rename the result to `0001_initial`, and compare every field, constraint and index to section 4. The dependency is `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` only. CI's `makemigrations --check --dry-run` must then report nothing.

### 5.1 `ledger.0001_initial`

Operations, in this order: `CreateModel` for `AccountingEntity`, `GLAccount`, `FiscalPeriod`, `JournalEntry`, `JournalLine`, `RecognitionSchedule`, `LedgerEvent`, `LedgerAccess` (in that order, so each foreign key resolves), each with its fields, `options={"db_table": ...}`; then the constraints and indexes of section 4 as `AddConstraint` and `AddIndex` (or inside `options`, as `makemigrations` writes them). No `RunPython`, no `RunSQL`, no `RunSQL` for a partial index (Django's `UniqueConstraint(condition=...)` writes it on both databases). Reverse: Django's default (drops the tables). The migration is additive and safe with every switch off.

Acceptance: the eight tables exist and are empty; `sqlmigrate ledger 0001` on PostgreSQL contains no `INSERT`.

### 5.2 `ledger.0002_postgres_guards`

Dependencies: `("ledger", "0001_initial")`. One operation: `migrations.RunPython(apply, reverse)` where both functions begin with `if schema_editor.connection.vendor != "postgresql": return`. The SQL lives in the migration file as two string constants (the migration imports nothing from the app, so it can never change meaning later).

`apply` executes, in order, the four functions and four triggers below. `reverse` executes `DROP TRIGGER IF EXISTS <name> ON <table>` for each trigger, then `DROP FUNCTION IF EXISTS <name>()` for each function. The SQL was run on PostgreSQL 14 against stand-in tables; the `RAISE` messages below are the ones the tests assert.

```sql
CREATE FUNCTION ledger_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    per RECORD;
    book_start date;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION 'ledger: a posted entry cannot be deleted' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'posted' THEN
        RAISE EXCEPTION 'ledger: a posted entry cannot be changed' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'posted' THEN
        IF NEW.posting_seq IS NULL OR NEW.period_id IS NULL OR NEW.posted_at IS NULL OR NEW.seal = '' THEN
            RAISE EXCEPTION 'ledger: a posted entry needs posting_seq, period, posted_at and seal' USING ERRCODE = 'check_violation';
        END IF;
        SELECT entity_id, start_date, end_date, status INTO per FROM ledger_period WHERE id = NEW.period_id;
        IF per.entity_id IS DISTINCT FROM NEW.entity_id THEN
            RAISE EXCEPTION 'ledger: the period belongs to another book' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.entry_date < per.start_date OR NEW.entry_date > per.end_date THEN
            RAISE EXCEPTION 'ledger: the entry date is outside the period' USING ERRCODE = 'check_violation';
        END IF;
        IF per.status = 'locked' THEN
            RAISE EXCEPTION 'ledger: the period is locked' USING ERRCODE = 'check_violation';
        END IF;
        SELECT books_start_date INTO book_start FROM ledger_entity WHERE id = NEW.entity_id;
        IF book_start IS NULL OR NEW.entry_date < book_start THEN
            RAISE EXCEPTION 'ledger: the entry is dated before the books start' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER ledger_entry_guard BEFORE UPDATE OR DELETE ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_guard();

CREATE FUNCTION ledger_line_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    parent RECORD;
    acct RECORD;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO parent FROM ledger_entry WHERE id = OLD.entry_id;
        IF FOUND AND parent.status <> 'draft' THEN
            RAISE EXCEPTION 'ledger: the lines of a posted entry cannot be changed' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    SELECT entity_id, entry_date, kind, status INTO parent FROM ledger_entry WHERE id = NEW.entry_id;
    IF parent.status <> 'draft' THEN
        RAISE EXCEPTION 'ledger: a line cannot be added to a posted entry' USING ERRCODE = 'check_violation';
    END IF;
    SELECT entity_id, is_postable, effective_from, effective_to INTO acct FROM ledger_account WHERE id = NEW.account_id;
    IF NOT acct.is_postable THEN
        RAISE EXCEPTION 'ledger: a header account takes no line' USING ERRCODE = 'check_violation';
    END IF;
    IF acct.entity_id <> parent.entity_id THEN
        RAISE EXCEPTION 'ledger: the account belongs to another book' USING ERRCODE = 'check_violation';
    END IF;
    IF (acct.effective_from IS NOT NULL AND parent.entry_date < acct.effective_from)
       OR (acct.effective_to IS NOT NULL AND parent.entry_date > acct.effective_to) THEN
        RAISE EXCEPTION 'ledger: the entry date is outside the account''s effective dates' USING ERRCODE = 'check_violation';
    END IF;
    IF parent.kind = 'opening' AND btrim(NEW.evidence_ref) = '' THEN
        RAISE EXCEPTION 'ledger: every line of the opening entry needs an evidence reference' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER ledger_line_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_line
    FOR EACH ROW EXECUTE FUNCTION ledger_line_guard();

CREATE FUNCTION ledger_event_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'ledger: the change history is append-only' USING ERRCODE = 'check_violation';
END
$fn$;

CREATE TRIGGER ledger_event_append_only BEFORE UPDATE OR DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_event_guard();

CREATE FUNCTION ledger_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    n integer;
    debits numeric;
    credits numeric;
    bad integer;
BEGIN
    IF NEW.status <> 'posted' THEN
        RETURN NULL;
    END IF;
    SELECT count(*), COALESCE(sum(debit_centavos), 0), COALESCE(sum(credit_centavos), 0)
        INTO n, debits, credits FROM ledger_line WHERE entry_id = NEW.id;
    IF n < 2 OR debits <> credits OR debits <> NEW.total_centavos THEN
        RAISE EXCEPTION 'ledger: entry % is not balanced (% lines, debits %, credits %, total %)',
            NEW.id, n, debits, credits, NEW.total_centavos USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO bad FROM ledger_line l JOIN ledger_account a ON a.id = l.account_id
        WHERE l.entry_id = NEW.id AND (
            NOT a.is_postable OR a.entity_id <> NEW.entity_id
            OR (a.effective_from IS NOT NULL AND NEW.entry_date < a.effective_from)
            OR (a.effective_to IS NOT NULL AND NEW.entry_date > a.effective_to));
    IF bad > 0 THEN
        RAISE EXCEPTION 'ledger: entry % has a line on an account that cannot take it on that date', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$fn$;

CREATE CONSTRAINT TRIGGER ledger_entry_balanced AFTER INSERT OR UPDATE ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_entry_balanced();
```

The table names above are the `db_table` values of section 4. The migration is reversible and PostgreSQL-only in effect. On SQLite both functions return at once.

What the triggers guarantee, and what they leave to the writer:

| Rule | Trigger | Writer (S2-A, S2-D, S2-E) |
|---|---|---|
| A posted entry is not updated or deleted; its lines are not added, changed or deleted | `ledger_entry_guard`, `ledger_line_guard` | also refuses first, with an `ApiError` |
| The one allowed update, draft to posted, needs `posting_seq`, `period`, `posted_at`, `seal`; the date lies in the period; the period belongs to the book and is not `locked`; the books have started and the date is not before them | `ledger_entry_guard` | assigns them; refuses a `soft_closed` period for the kinds FAS-06 names (D1) |
| At commit, at least two lines and debits = credits = `total_centavos`; no line on a header, another book's account or outside its effective dates | `ledger_entry_balanced` (deferred, D4) | checks the same first |
| A line never points at a header account, another book's account, or one whose dates exclude the entry date; every line of an `opening` entry has an evidence reference | `ledger_line_guard` | checks the same first; also refuses a `retired` account |
| `ledger_event` rows are never updated or deleted | `ledger_event_append_only` | writes in the same transaction as the change |
| `ledger_event` is never truncated | **not a trigger** (Deviation 1) | seal chain (layer 3) |

## 6. Switches, settings and the shared files

### 6.1 The switches

Read once in `config/settings.py`, like the neighbouring ones. All off unless the variable is exactly `1`.

```python
# The Financial Accounting System's books (apps/ledger). "1" turns on the ledger app: its
# writer and its staff routes. Off, every ledger route answers 404 and no ledger table is
# read or written.
LEDGER_ENABLED = os.environ.get("LEDGER_ENABLED", "").strip() == "1"
# "1" lets a confirmed bank-transfer payment post its journal entry inside the confirmation.
# Counts only while LEDGER_ENABLED is on. Off, a confirmation behaves as it did before.
LEDGER_POST_BILLING_ENABLED = os.environ.get("LEDGER_POST_BILLING_ENABLED", "").strip() == "1"
# "1" lets a live book be opened and posted to. Set only where the real books live, which is
# nowhere yet: staging holds rehearsal books only.
LEDGER_LIVE_BOOKS = os.environ.get("LEDGER_LIVE_BOOKS", "").strip() == "1"
```

`.env.example` gets three lines at the end, with empty values (the file's convention):

```
LEDGER_ENABLED=
LEDGER_POST_BILLING_ENABLED=
LEDGER_LIVE_BOOKS=
```

### 6.2 `switch.py` and `checks.py`

`switch.py`: `ledger_on()` returns `bool(settings.LEDGER_ENABLED)`; `post_billing_on()` returns `ledger_on() and bool(settings.LEDGER_POST_BILLING_ENABLED)`; `live_books_on()` returns `ledger_on() and bool(settings.LEDGER_LIVE_BOOKS)` (D7); `tables_exist()` returns `"ledger_entity" in connection.introspection.table_names()` (the code is released before its migration; the same helper as `inbox/switch.py`).

`checks.py`, registered by `LedgerConfig.ready()` like `apps/notifications/checks.py`: `ledger.E001` when `LEDGER_POST_BILLING_ENABLED` is on without `LEDGER_ENABLED`; `ledger.E002` when `LEDGER_LIVE_BOOKS` is on while `DEBUG` is on. Both are `Error`, so `manage.py check` and `check --deploy` fail. With every switch off, neither fires, so CI's `check --deploy` (no ledger variables set) is unaffected.

### 6.3 `config/settings.py` and `.env.example` are shared files

Open PRs 103, 104, 109 and 118 (and later ones) also edit both files. The build keeps its footprint to the minimum so a conflict is a one-line decision:

1. **One place in each file.** In `config/settings.py` the three-switch block of 6.1 goes in one piece immediately above the `# Application definition` heading, and `'apps.ledger',` is one line appended after the last app of `INSTALLED_APPS` (after `'apps.storefront',` at `34b0d10`, or after whatever the last entry is once the branch is current). In `.env.example` the three lines go at the very end of the file. No other line of either file is touched, reordered or reformatted.
2. **Merge first.** Before the first commit that edits them, and again before opening the PR, run `git fetch origin` and merge the latest `origin/main` into the branch, then re-run the checks of section 7.
3. **Keep both sides.** On a conflict in either file, keep every line from `origin/main` and every line of this build, in both blocks. Never drop, revert or reorder another session's line to make the merge pass. If both cannot be kept, stop and report to the coordinator, naming the PR.
4. Before the PR is opened, list open PRs and their files (`gh pr list --state open --json number,files`); if a PR other than 103, 104, 109 or 118 touches `config/settings.py` or `.env.example`, tell the coordinator.

## 7. Task-by-task steps

Run all commands from the repository root with `uv run`. Branch: `feat/z8uq9md5df-ledger-schema-and-switch`, from the latest `origin/main`. Commit after each task; reference `[z8uq9md5df]` in each message. Tests are written first in each task.

### Task 1: the app, the switches and the checks

**Files:** `apps/ledger/{__init__,apps,switch,checks}.py`, `apps/ledger/tests/{__init__,test_switch,test_checks}.py`, `config/settings.py`, `.env.example`.

- [ ] **Step 1: merge the latest `origin/main`** (section 6.3, rule 2). Record the commit.
- [ ] **Step 2: write `test_switch.py` and `test_checks.py`** (all `SimpleTestCase` except where a database is named):
  - `test_1_every_switch_is_off_without_its_variable`: `runpy.run_path("config/settings.py")` with `os.environ` patched to remove the three variables returns `False` for each; patched to `"1"` returns `True`; `" 1 "` is `True` (stripped); `"true"` is `False`.
  - `test_2_post_billing_needs_the_ledger`, `test_3_live_books_needs_the_ledger` (`override_settings`, four combinations each).
  - `test_4_tables_exist_is_true_after_migrate` (a `TestCase`; the test database has the tables once task 5 lands, so this test is added in task 5).
  - `test_5_post_billing_without_ledger_is_check_error_e001`, `test_6_live_books_in_debug_is_check_error_e002`, `test_7_no_error_with_everything_off`, `test_8_no_error_for_a_valid_combination` (`call the registered check function` with `override_settings`).
- [ ] **Step 3: run them and see them fail** (`uv run python manage.py test apps.ledger`: import error).
- [ ] **Step 4: add** the app files, the block of 6.1 (section 6.3 placement), the `INSTALLED_APPS` line and the `.env.example` lines; `apps.py` uses `default_auto_field = "django.db.models.BigAutoField"`, `name = "apps.ledger"`, `label = "ledger"`, and `ready()` importing `checks`.
- [ ] **Step 5: run** `uv run python manage.py test apps.ledger`, `uv run python manage.py check` and `uv run python manage.py check --deploy`; expect pass (the app has no models yet, so no migration is asked for).
- [ ] **Step 6: commit.**

### Task 2: managers and the guards' tests (red)

**Files:** `apps/ledger/managers.py`, `apps/ledger/tests/base.py`, `apps/ledger/tests/test_guards.py`.

- [ ] **Step 1: write `base.py`**: `make_user(name, staff=False)`; `make_entity(kind="rehearsal", books_start="2026-01-01")`; `make_account(entity, code, type, **fields)` (derives `normal_side`); `make_period(entity, year, month, status="open")`; `make_draft(entity, entry_date, lines, **fields)` with `lines` a list of `(account, debit, credit)` and a unique `idempotency_key`; `force_post(entry, period, seq)`, **a test-only helper** that sets `status`, `period`, `posting_seq`, `posted_at`, `seal="x" * 64`, `total_centavos` and calls `entry.save()` (it exists because the writer is S2-A; it is deleted from use when S2-A's tests replace it). A comment says so.
- [ ] **Step 2: write `test_guards.py`** (a `TestCase`, runs on both databases). One test each:
  - posted entry: `save()` raises; `delete()` raises; `QuerySet.update`, `bulk_update`, `delete` on a queryset that contains it raise; the same calls on a queryset filtered to drafts pass;
  - inserting an entry with `status="posted"` raises (D3), via `create` and `bulk_create`;
  - draft entry: `save()` passes; `delete()` passes and removes its lines (the cascade);
  - lines of a posted entry: `save()`, `delete()`, `QuerySet.update`, `bulk_create` of a new line, `bulk_update` all raise; lines of a draft pass;
  - `LedgerEvent`: `save()` on an existing row, `delete()`, `update`, `bulk_update`, queryset `delete` raise; creating a row passes; `RecognitionSchedule` likewise (D5);
  - `AccountingEntity`, `GLAccount`, `FiscalPeriod`: `delete()` and queryset `delete()` raise (D6);
  - `AccountingEntity.kind` cannot change on an existing row; `name` can;
  - the three-prefix check: `public_id` starts with `ent_`, `gla_`, `per_`, `jrn_`, `lev_` and `is_public_id` accepts it.
- [ ] **Step 3: write `managers.py`** (4.9), leaving the models for task 3. Do not run yet.
- [ ] **Step 4: commit** (red tests allowed on the branch, not in the PR: the PR is opened only when everything passes).

### Task 3: models

**Files:** `apps/ledger/models.py`, `apps/ledger/tests/test_models.py`.

- [ ] **Step 1: write `test_models.py`** (`TestCase`; both databases). For each constraint of section 4, one test that violates it inside `transaction.atomic()` and asserts `IntegrityError` (the `refused` helper of `apps/inbox/tests/test_models.py`), and one that satisfies it. The list: entity (name unique, second `live` refused, a `rehearsal` second allowed, currency, first month 0 and 13); account (code unique per book and not across books, `system_key` unique per book but blank repeated, side branches for all five types with and without `is_contra`, effective order); period (unique, month 0 and 13, end before start); entry (idempotency unique per book and not across books, blank key, source triple unique only when `source_type` is set, `posting_seq` unique per book with several nulls, second `opening` refused, negative total, reason rule in its four cases: manual without reason refused, `reversal` without reason refused, system `normal` without reason passes, `opening` without reason refused; reversal link both ways; `reverses` one-to-one, so a second reversal of the same entry refused; posted without period, without seal, with zero total each refused); line (one side: both zero, both positive and a negative each refused; `line_no` 0 refused; `(entry, line_no)` unique); recognition (unique, zero amount, `service_end == service_start`); access (one row per user per book, level); every enumerated column refuses a value outside its choices (D2).
- [ ] **Step 2: write `models.py`** exactly as section 4 (names of constraints and indexes included), each public-id factory a module function (`new_entity_id` and so on, as `inbox/models.py` does) so the migration refers to it.
- [ ] **Step 3: run** `uv run python manage.py makemigrations ledger` and review the output against section 4 (task 4).
- [ ] **Step 4: commit.**

### Task 4: migration 0001

**Files:** `apps/ledger/migrations/{__init__,0001_initial}.py`.

- [ ] **Step 1:** rename the generated file to `0001_initial.py`; confirm the operations are only `CreateModel`, `AddConstraint`, `AddIndex` (or the same inside `options`), in the dependency order of 5.1; confirm each constraint and index name of section 4 appears exactly once; confirm `dependencies` is only `swappable_dependency(settings.AUTH_USER_MODEL)`.
- [ ] **Step 2: write** the migration tests of `test_app.py` (below, in task 8) that read this file's operations.
- [ ] **Step 3: run** `uv run python manage.py makemigrations --check --dry-run` (expect "No changes detected"), `uv run python manage.py sqlmigrate ledger 0001 | grep -ci insert` (expect `0`) and the whole `apps.ledger` suite on SQLite: tasks 2 and 3's tests now pass.
- [ ] **Step 4: commit.**

### Task 5: migration 0002 and the PostgreSQL tests

**Files:** `apps/ledger/migrations/0002_postgres_guards.py`, `apps/ledger/tests/test_postgres_guards.py`.

- [ ] **Step 1: prove the TRUNCATE finding** (Deviation 1) before writing the migration: on a local PostgreSQL (`DATABASE_URL` set to a scratch database; never staging), add the `BEFORE TRUNCATE` trigger by hand to a test database and run `uv run python manage.py test apps.sites.tests.test_concurrency`; it should fail in teardown. Record the output in the PR. Then drop the hand-made trigger. If it does not fail, tell the coordinator before going on.
- [ ] **Step 2: write `test_postgres_guards.py`**, every class decorated `@skipUnless(connection.vendor == "postgresql", "PostgreSQL triggers")`, `TestCase`, each refusal inside `with self.assertRaises(IntegrityError), transaction.atomic():` and asserting the message text of section 5.2 appears in `str(error)`:
  - entry: update of a posted entry; delete of a posted entry; draft to posted without a period, without a sequence, without `posted_at`, with a blank seal; with a date outside the period; with a period of another book; into a `locked` period; into a `soft_closed` period (**passes**, D1); with `books_start_date` null; dated before `books_start_date`; a draft update and a draft delete pass;
  - line: insert into a posted entry; update and delete on a posted entry's line; a header account; another book's account; an entry date before `effective_from` and after `effective_to`; an `opening` entry's line with a blank evidence reference refused and with one accepted; the deleting of a draft entry removes its lines;
  - event: update and delete refused; insert accepted; **`TRUNCATE` is not refused** is not tested (Deviation 1);
  - balance: the deferred trigger does not fire inside a test's transaction, so each case runs `SET CONSTRAINTS ledger_entry_balanced IMMEDIATE` after the writes, inside a savepoint: one line; unequal sides; total different from the sums; and a line on a header account placed on the entry by changing the account's `is_postable` afterwards (D4): each refused; a two-line balanced entry passes. This avoids `TransactionTestCase` (so no flush) and fires the same function.
  - `test_reverse_drops_the_triggers` (`TransactionTestCase`, after the others, `available_apps = ["apps.ledger", "apps.operators", "django.contrib.contenttypes", "django.contrib.auth"]`): migrate the `ledger` app back to `0001`, assert `pg_trigger` has no trigger whose name starts `ledger_` and `pg_proc` no function of that name, migrate forward to `0002`, assert all four triggers and four functions exist; the cleanup always migrates forward.
- [ ] **Step 3: write `test_sqlite_noop`** in `test_app.py` (`skipUnless` SQLite): `sqlite_master` holds no trigger whose name starts `ledger_`. (It runs wherever CI's `django` job runs; on PostgreSQL the test is skipped.)
- [ ] **Step 4: write** `0002_postgres_guards.py` from section 5.2.
- [ ] **Step 5: run** on SQLite (`uv run python manage.py test apps.ledger`: trigger tests skipped, the no-op test passes) and on a scratch PostgreSQL (`DATABASE_URL=postgresql://... uv run python manage.py test apps.ledger`: trigger tests pass). Report both outputs. If no local PostgreSQL is available, say so and rely on CI's `postgres-and-redis` job, which runs the whole suite.
- [ ] **Step 6: commit.**

### Task 6: the architecture of "nothing else"

**Files:** `apps/ledger/tests/test_app.py`.

- [ ] **Step 1: write** (`SimpleTestCase` or `TestCase`):
  - `test_1_the_app_is_installed_and_labelled_ledger`;
  - `test_2_no_ledger_model_has_a_django_admin_screen` (`admin.site._registry` holds no `ledger` label model, as `apps/inbox/tests/test_models.py` does);
  - `test_3_the_database_has_exactly_the_eight_tables` (`introspection.table_names()` filtered to `ledger_` equals the eight names);
  - `test_4_the_tables_are_empty_after_migrate` (a count of zero on each, so no migration seeds a row);
  - `test_5_migration_0001_holds_only_schema_operations` (import it, every operation is `CreateModel`, `AddConstraint` or `AddIndex`; none is `RunPython` or `RunSQL`);
  - `test_6_migration_0002_depends_only_on_0001` and `test_7_migration_0002_uses_no_app_import` (the source text has no `from apps` and no `import apps`);
  - `test_8_no_other_app_imports_ledger` (a scan of the source tree: no module outside `apps/ledger` contains `apps.ledger` or `from apps import ledger`, so S2-0 changes no behaviour elsewhere);
  - `test_9_the_schema_names_no_tax_or_receiving_account` (no field name in any ledger model contains `tax`, `suspense`, or `receiving`; ruling 11, 12, 14);
  - `test_10_with_every_switch_off_nothing_outside_the_app_reaches_a_ledger_table` (with `connection.execute_wrapper` recording statements, call `GET /healthz/` and one billing service call; no recorded statement mentions `ledger_`; the strongest form available before routes exist).
- [ ] **Step 2: run** the suite on SQLite; commit.

### Task 7: the repository checks and the PR

- [ ] **Step 1:** `git fetch origin && git merge origin/main`; resolve per section 6.3 and re-run.
- [ ] **Step 2: run and report the actual output of:** `uv run python manage.py makemigrations --check --dry-run`, `uv run python manage.py check`, `uv run python manage.py check --deploy`, `uv run python manage.py test` (the whole suite, on SQLite, as CI job `django` does), and the same `test` on a scratch PostgreSQL if one is available. Do not set any `LEDGER_*` variable for these runs (the defaults are what CI uses); one extra run with `LEDGER_ENABLED=1` only proves the app still loads.
- [ ] **Step 3:** list open PRs and their files; tell the coordinator about any overlap before opening the PR (builder brief section 2).
- [ ] **Step 4: open the PR.** Its description carries the ClickUp ID and URL, what was built against which section of this plan, what was verified where, and the line "No switch turned on; no migration run; no staging or production action". Post the one ClickUp comment, set `ACCEPTED` per the builder command (this task has nothing to look at, so `FOR CHECKING` is skipped).

## 8. Running the migration on staging (the coordinator's step)

Not part of the build. The standing approval of 2026-11-11 lets the coordinator run what this plan specifies on staging, as `docs/runbooks/migrations.md` sets out, after the PR is merged and Railway has deployed the code (the code is safe before the tables exist: every switch is off and nothing reads them):

1. `railway ssh ... -s api -- uv run python manage.py migrate --plan`: read it. It must list `ledger.0001_initial` and `ledger.0002_postgres_guards`, and nothing else the coordinator has not reviewed.
2. `migrate --noinput`; then `migrate --plan` again: "No planned migration operations."
3. Verify (a read-only query through `manage.py shell -c`, whole remote command single-quoted as `railway-ssh-quoting` notes): eight `ledger_%` tables exist and are empty; `pg_trigger` shows `ledger_entry_guard`, `ledger_line_guard`, `ledger_event_append_only`, `ledger_entry_balanced` (non-internal).
4. `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/`.
5. No `LEDGER_*` variable is set on staging by this piece. Setting `LEDGER_ENABLED` waits for S2-A and S2-C and Coach Ed's separate approval; `LEDGER_LIVE_BOOKS` is never set on staging (ruling 15).

Production: not covered, and has no services (`production-build-deferred-until-mvp`).

## 9. Rollback

- **Before the migration is run anywhere:** revert the PR. Nothing else changed.
- **After it is run on staging, nothing written (the expected state through all of stage 2 until a book is opened):** switches stay off (they are, so no step); then `migrate ledger 0001` (drops the triggers and functions) and `migrate ledger zero` (drops the eight tables), then revert the code. Both steps are the coordinator's and use the runbook's procedure. Dropping loses nothing because the tables are empty.
- **After a rehearsal book holds rows (a later piece):** the same commands drop the rows with the tables. The rehearsal books on staging hold no real data (ruling 15) and may be dropped; a `live` book exists nowhere, and if one ever does, this rollback is not to be run without Coach Ed.
- **Only the triggers misbehave:** `migrate ledger 0001` alone removes them, leaving the schema (this is why they are a separate migration). The Django guards stay.
- **A switch cannot be turned on by mistake** to reach the tables before the migration: `switch.tables_exist()` is there for the code that must work with the switch off; S2-0 itself reads no table.

## 10. Acceptance

| Criterion | Where it is proved |
|---|---|
| Every model and field of design 2.3 exists as approved, with the constraints and indexes of section 4 | Tasks 3, 4: `test_models.py`; review of the generated migration against section 4 |
| Two migrations, exactly as section 5 | Task 4, 5: `test_app.py` tests 5 to 7; `makemigrations --check` |
| The PostgreSQL triggers refuse what design 2.5 lists (with the two deviations) | Task 5: `test_postgres_guards.py`, run by CI job `postgres-and-redis` |
| A posted entry and its lines cannot be changed through Django on SQLite or PostgreSQL | Task 2: `test_guards.py` |
| Three switches, all off by default; the two startup checks | Task 1: `test_switch.py`, `test_checks.py` |
| No data, no seed, no admin screen, no tax or receiving-account field | Task 6: `test_app.py` tests 2, 4, 5, 9 |
| Nothing else in the repository changes behaviour | Task 6: tests 8 and 10; the whole existing suite still passes |
| Shared files edited in one place each, both sides kept | Section 6.3; the PR diff of `config/settings.py` and `.env.example` shows only the additions |

## 11. What this plan does not decide

- Nothing about the chart, the first book, the books-start date or any switch's value anywhere: those are S2-C, FAS-32's loading command and Coach Ed.
- I ran no migration, no test of the Python and no query against staging or production, and read no data from them.
