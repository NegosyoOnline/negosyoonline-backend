# SMS inbox, plan 8: Bulk SMS, the do-not-text list and the batch driver, backend (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128).

Status: **a plan only. Nothing here is built.** It is the eighth plan of the build order (ClickUp comment 1100430000488202: "8 backend Bulk SMS (migration)"). Under Coach Ed's ruling "build until plan 9" (comment 1100430000490204) it does not return to him: the gate is the coordinator's read, the NPC coordinator's privacy review and, for the shared files, the Santé/onboarding coordinator's overlap check (section 8). Everything is built switched off.

**The code in this plan has not been run.** It was written against backend `origin/main` at `a125efb` and against the plan 6 build (PR 100, branch `feat/z8uq9me128-contacts-build`, open and not merged) by reading them. The builder writes each test first, sees it fail, and reports every place where the code below had to change. **The build starts only after PR 100 is merged**: this plan changes `apps/inbox/contacts.py` and `sms_inbox_delete.py`, which exist only there.

## 1. Sources

| Short name | Source |
|---|---|
| Design | `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md`: section 18 (Bulk SMS, 18.1 to 18.10), 19.3 to 19.5, section 6.3 (order, interval, cap), section 14 points 11 to 14 |
| Plans 3, 4, 6 | `...-sms-inbox-storage-plan.md`, `...-sms-inbox-reply-compose-plan.md` (the route pattern, section 3.1), `...-sms-contacts-groups-plan.md` (C1 to C17, the CSV reader) |
| Task | ClickUp z8uq9me128 and its comments, read 2026-11-11: R10 to R15 and R20 as recorded in the design, "build until plan 9" (1100430000490204), no Django admin (1100430000489781), the coordinator's records of plans 3 and 4 and the plan 6 notes |
| Code | Backend `origin/main` `a125efb`: `apps/notifications/{sms,services,gateway,models}.py`, `apps/inbox/{models,access,storing,sending,staff,reading,retention,switch}.py`, `apps/inbox/management/commands/{sms_inbox_access,sms_inbox_delete}.py`, `apps/operators/audit.py`, `apps/common/pagination.py`; and from PR 100 `apps/inbox/{contacts,contacts_staff}.py` |

Rulings applied, not reopened: R10 (bulk, in batches, the cap and the gap not raised), R11 (the announcement exception, after the wording is amended), R12 (Coach Ed supplies the numbers, no link to TWCinc), R13 (STOP and the do-not-text list), R14 (same phone, same SIM, risk accepted), R15 (the CSV input and the list of bulk sends with Stop), R16 and R19 (groups as a source), R20 (300 kept for codes), R23 (the screens call the backend), no Django admin screen.

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | Three records, `BulkBatch`, `BulkRecipient`, `DoNotText`, and `InboxAccess.can_bulk`, in one migration `inbox.0003_bulk` (section 4) | Design 18.8 |
| 2 | A switch of its own, `SMS_BULK_ENABLED`, off, read in `config/settings.py` | Design 18.7 |
| 3 | The STOP rule: an incoming text whose whole text is the word STOP (any case, with spaces or punctuation around it) puts the number on the do-not-text list, inside `storing.store` | Design 18.5 |
| 4 | `apps/inbox/bulk.py`: the source reader, the check, the confirm, the batch driver (`feed`), the settle step, stop, the left-out file | Design 18.2 to 18.4, 18.10 |
| 5 | `apps/inbox/bulk_staff.py`: seven POST staff routes under `/staff/v1/bulk/`, wired in by four added lines | Design 10, 18.2; the plan 4 and 6 pattern |
| 6 | A purpose `inbox_bulk` in `apps/notifications/sms.py`; `services.py` gains three small functions | Design 18.8 |
| 7 | The poll feeds the active batch: one added call in `gateway.poll` | Design 18.3 ("the phone's own poll is the proposal") |
| 8 | `opted_out` in the contacts answers and on a conversation | Design 18.5, 19.2, 19.4; plan 6 C13 |
| 9 | Three audit actions, all `KEPT` | Design 18.7 |
| 10 | Retention rule `bulk_batches` (365 days); `sms_inbox_delete` also removes the number's waiting bulk rows and bulk send records | Design 18.4; plan 3 task 9 |
| 11 | `sms_inbox_access grant --bulk` | Design 18.7 |
| 12 | Runbook lines, including the blocked-SIM note | Design 18.6 |

### 2.2 What is not built here

- Any page: plan 9, in the frontend repository.
- Any way to add a number to, or take one off, the do-not-text list by hand (design 18.5, 16).
- A second phone or SIM, an integration with TWCinc, a change to the cap, the gap or the 300 (design 16, R10, R14, R20).
- Any Django admin screen. Any Railway variable, any run of a migration, any switch turned on, any access grant, anything on staging or production (section 7).

### 2.3 Files

| File | Change |
|---|---|
| `apps/inbox/models.py` | `BulkBatch`, `BulkRecipient`, `DoNotText`, `InboxAccess.can_bulk` and its constraint, two id functions |
| `apps/inbox/migrations/0003_bulk.py` | New (section 4) |
| `apps/inbox/switch.py` | `bulk_on()`, `bulk_tables_exist()`, `dnt_tables_exist()` |
| `config/settings.py` | **One added line**, `SMS_BULK_ENABLED`, beside `SMS_INBOX_ENABLED` and `SMS_CONTACTS_ENABLED` |
| `apps/inbox/access.py` | `scope_for` reads only `can_send` from the row (C2) |
| `apps/inbox/bulk.py`, `bulk_staff.py` | New |
| `apps/inbox/storing.py` | The STOP rule (about 20 lines, one added import: `unicodedata`) |
| `apps/inbox/staff.py` | `bulk_running` in the `status` answer |
| `apps/inbox/reading.py`, `apps/inbox/contacts.py` | `opted_out` |
| `apps/inbox/retention.py` | Rule `bulk_batches` |
| `apps/inbox/management/commands/sms_inbox_access.py`, `sms_inbox_delete.py` | `--bulk`; the number's bulk rows |
| `apps/notifications/sms.py` | One line in `PURPOSES` |
| `apps/notifications/services.py` | `STAFF_PURPOSES` gains `inbox_bulk`; `sms_room_for_staff()`; `cancel_sms()` |
| `apps/notifications/gateway.py` | **Four added lines** in `poll` (section 3.1) |
| `apps/operators/audit.py` | Three added lines in `ACTIONS` |
| `apps/operators/staff_views.py` | **Four added lines, none changed** (as plans 4 and 6) |
| `apps/billing/tests/test_staff_finance.py`, `apps/inbox/tests/test_staff.py`, `apps/inbox/tests/test_contacts_staff.py` | The route-count assertions change (section 3.1) |
| `apps/inbox/tests/test_bulk.py`, `test_bulk_run.py`, `test_bulk_staff.py`, `test_stop_rule.py` | New |
| `docs/runbooks/sms-gateway.md`, `retention.md` | Task 9 |

No migration in `apps/notifications` (C3), no file in the frontend repository.

## 3. Choices this plan makes, each reported and none hidden

Each is a routine detail inside the approved design, decided as the smallest safe one. The coordinator or Coach Ed may overrule any of them.

### 3.1 The shared files

- `apps/operators/staff_views.py`: the same four lines as plan 6 (an import of `bulk_staff`, and an `ROUTES.update` through `_for_staff`). The three tests that count every staff route change the same way plan 6 changed them, plus an assertion that no `bulk/` path is any other route's.
- `apps/notifications/gateway.py`, in `poll`, in the branch that already hands texts out:

```python
    elif device == sms.pick_device():
        if bulk_on():
            bulk.feed(now)   # two added imports (from apps.inbox import bulk; from apps.inbox.switch import bulk_on) and these two lines
        answer["messages"] = _hand_out(device, now)
```

  `bulk_on()` reads a setting, so with the switch off the poll does no extra work and reads no table. `feed` queues at most one text, before `_hand_out` runs in the same call. The overlap check is the Santé/onboarding coordinator's for these files.

### 3.2 The choices

| # | Choice | Why | Source |
|---|---|---|---|
| C1 | **Seven POST routes, no id, number or text in an address**: `bulk/status/`, `bulk/list/`, `bulk/check/`, `bulk/left-out/`, `bulk/confirm/`, `bulk/open/`, `bulk/stop/`. The design's `GET /inbox/bulk/{id}/` forms are not used | The staff route table matches exact paths with one method (plans 4 and 6, C1); PR 98's shapes are followed where the design differs | Design 10; `staff_views.py` |
| C2 | **`can_bulk` is a column of `InboxAccess`, as designed, but `scope_for` stops reading it.** `scope_for` reads `can_send` alone (`values_list("can_send", flat=True).first()`) and `bulk.bulk_scope` reads `can_bulk` in its own query, only when `SMS_BULK_ENABLED` is on and the tables exist | The code is released before `inbox.0003` runs. A model query on `InboxAccess` selects every column, so a new column would make every inbox route fail with a database error until the migration is applied. A test asserts the SQL of `scope_for` holds no `can_bulk` | Plan 3 (`tables_exist`), plan 6 C17 |
| C3 | **No change in `apps/notifications` models.** `inbox_bulk` is declared with `Category.INBOX` and `Priority.MARKETING` (40), paced. That priority already exists and is already the lowest, so a code (10) and a staff text (20) are always handed out first, and the poll already hands a paced text out one at a time after the last is reported | A new priority or category would need `notifications.0005` for choices alone (as `0004` did). The name MARKETING is the sending class, not a statement that other marketing is allowed: the closed list of purposes is what forbids it | Design 6.3, 18.3; `gateway._waiting` |
| C4 | **Bulk texts count against the staff limit, not the platform limit.** Category `inbox` already makes `sms.refusal` stop at 2,000 less the 300 (`staff_limit`), which is the 1,700 of design 18.10 with no new code. A refused bulk text reads "Not sent: today's limit reached" | Design 18.3, 18.10 | `sms.refusal` |
| C5 | **The batch is driven by the poll, one text at a time.** `feed` runs inside the poll, takes the active batch, and queues the next recipient only when none of its recipients is waiting or with the phone. With the phone's gap (18 to 25 seconds) that is the design's 145 to 200 texts an hour. No worker, no beat, no new process. If the phone is offline nothing is queued and the batch waits | Design 18.3 names the poll as the proposal | Design 18.3; `gateway.poll` |
| C6 | **No held check.** `check` stores nothing. `confirm` takes the same source again with the text and the typed `count`, builds the list again, and refuses with 409 `changed` unless the size it builds equals `count`. A repeat of the same confirm (the same `request_id`) answers the batch it already made | Plan 6 C8: a held check is a second place that keeps numbers outside the batch table, and a record to expire and delete. The design's `check_id` is not used | Design 10, 18.4; plan 6 C8 |
| C7 | **One batch at a time is a database rule**, a partial unique constraint over the states waiting and sending, not only a check in code | Two confirms at once cannot both pass an `exists()` check | Design 18.3 |
| C8 | **The list is built in this order, first line first:** read, drop lines that are not a Philippine mobile (listed by line number and reason only, the first 200), drop repeats, drop numbers on the do-not-text list (counted), then cut to what is left for bulk today. The cut keeps the first numbers in source order; for groups the order is the contacts' order of adding | Design 18.2 step 3, 18.3 | Design 18.2, 18.3 |
| C9 | **Text rules.** The staff text is at most 138 plain characters and holds no link; the server adds `" Reply STOP to opt out"` (22 characters), and the whole text goes through `services.sms_text_problem`. The batch keeps the text as typed | Design 18.2 step 2 | Design 18.2 |
| C10 | **A batch is refused unless incoming texts are stored**: `SMS_INBOX_ENABLED` is on and the system phone's incoming switch is on (`forwards_incoming`). The check answers `blocked: "incoming_off"` and the confirm answers 409 `incoming_off` | A STOP reply is discarded otherwise, and the opt-out line would be a promise the system cannot keep | Design 18.5 |
| C11 | **The batch belongs to the Manila day it was confirmed.** At the first poll of a later day, every number still waiting is marked "not sent: day ended" and the batch is finished | Design 18.3 | Design 18.3 |
| C12 | **The numbers of a batch are rows only while it needs them.** A recipient row is deleted when its batch has finished or stopped and no recipient is waiting or with the phone. The counts (`sent`, `failed`, `not_sent`) are written to the batch first. **A confirmed batch therefore has no left-out or not-sent download**: the page offers the left-out file at the check (`bulk/left-out/`, before the confirm), from the source it still holds. Numbers that were not sent for another reason are gone with the rows | Design 18.4 says the rows go; design 10's left-out route for a confirmed batch cannot be kept with it. **For the coordinator and the NPC coordinator** | Design 18.3, 18.4, 10 |
| C13 | **`left-out` takes the same body as `check`**, builds the list again and answers the numbers beyond the cut as the text of a CSV file, full numbers, `Cache-Control: no-store`, to the staff user who sent the source. It writes `sms_inbox_bulk_left_out` (counts only). For a source of groups these are contacts' numbers, which is why it is audited | Design 10 ("full numbers: answered only to the staff user who ran it, no-store, audited with counts") | Design 10 |
| C14 | **Results per number are masked and shown only while the rows exist** (`bulk/open/`, paged 100, with the cursor of `apps.common.pagination.page`). After the batch ends the page shows the batch's counts | Design 18.2 step 6, 18.4 | Design 18.2 |
| C15 | **Stop** marks the batch stopped, marks every waiting recipient "not sent: stopped", takes back a text still waiting for the phone (`services.cancel_sms`), and leaves one already with the phone to finish; the batch frees the one-at-a-time rule at once. It is allowed on a waiting or sending batch only, and asks nothing twice (the page asks once) | Design 18.2 step 7, 18.2.1 | Design 18.2 |
| C16 | **The do-not-text rule is: strip whitespace and leading and trailing punctuation from the text, then `casefold() == "stop"`** (NPC coordinator's requirement), for a sender that is a Philippine mobile. "stop", "STOP.", "Stop!" and " stop " list; "STOP please", "nostop" and a name sender do not. No other word lists (not "unsubscribe", not "end"). The number is stored once (a unique `mobile`). It is written whenever the table exists and a text is stored, **whatever `SMS_BULK_ENABLED` says**, so a STOP that arrives before bulk is turned on is not lost | Design 18.5 | Design 18.5 |
| C17 | **The do-not-text list has no deletion path in this build**, not by a job, not by the Django admin, not by `sms_inbox_delete`. A person who asks to be forgotten is therefore a point for the NPC coordinator (section 7.3) | Design 14 point 13, 18.5, 16 | Design 18.5 |
| C18 | **Bulk send records are staff-text records for deletion.** `inbox_bulk` joins `STAFF_PURPOSES`, so `sms_inbox_delete` deletes the number's bulk send records, drops one still waiting, and deletes the number's waiting `BulkRecipient` rows. Its audit detail gains `bulk_recipients` | A bulk send record is a send record with the number, as a staff text's is; plan 4 made the command remove those | Plan 4; design 18.4 |
| C19 | **No cross-batch memory.** A second batch built from a list that holds a number already texted in an earlier batch texts it again. The left-out file is how a list is continued the next day | Design 19.5 ("a way to continue ... without the download is not designed here") | Design 19.5 |
| C20 | **A text over the cap is not queued and not lost.** If `send_sms` answers `staff_limit` for a recipient, every waiting recipient is marked "not sent: limit reached" and the batch finishes. Any other refusal of one text marks that recipient and goes on; `paused`, `gateway_offline` and `gateway_off` leave the recipient waiting for the next poll | Design 18.3 | Design 18.3 |

### 3.3 What bulk does that is new, said plainly

- **A text goes to a list of numbers for the first time.** The controls are the ones design 18.7 names: a separate permission, a switch, the typed count, the cap, the audit entry. Code cannot tell an announcement from other marketing; the words on the screen and Coach Ed's own list are what keep it to the announcement.
- **The do-not-text list is kept past 365 days** (design 14 point 13), the second retention period, and it holds a full number with no name.
- **A STOP reply is read for the first time**: every stored incoming text from a Philippine mobile is compared with one word.
- **A batch holds its numbers in a table** while it runs, copied from the source (a file, a pasted list or the contacts of chosen groups) at the confirm.
- **A carrier may block the SIM** if it sees the same text sent to many numbers, and sign-in codes would stop with it. Accepted by R14; the runbook says how it shows and what to do (task 9).

## 4. The migration

One migration, in `apps/inbox`:

`0003_bulk`: `CreateModel` for `BulkBatch`, `BulkRecipient` and `DoNotText`, their constraints, and on the existing table one `AddField` (`InboxAccess.can_bulk`, `BooleanField(default=False)`) and one `AddConstraint` (`can_bulk` requires `can_send`). It depends on `inbox.0002_contacts` and the migration `0002_contacts` depends on. No data operation, no change to `apps/notifications`. If `makemigrations` writes anything else, the builder stops and reports it.

Writing it is covered by the standing approval for a migration an approved plan specifies. Running it on staging is the coordinator's step after the merge. Production is never covered. The code can be released before it is applied (C2, `bulk_tables_exist`).

## 5. Tasks

Each step: write the test, run it and see it fail, write the code, run it and see it pass. Run from the repository root with `uv run python manage.py test apps.inbox`.

### Task 1: the switch, the records and the access column

**Files:** `config/settings.py`, `apps/inbox/switch.py`, `apps/inbox/models.py`, `apps/inbox/migrations/0003_bulk.py`, `apps/inbox/access.py`, `apps/inbox/tests/test_models.py`.

- [ ] `config/settings.py`, beside `SMS_INBOX_ENABLED`: `SMS_BULK_ENABLED = os.environ.get("SMS_BULK_ENABLED", "").strip() == "1"`.
- [ ] `switch.py`:

```python
BATCH_TABLE, DNT_TABLE = "inbox_bulkbatch", "inbox_donottext"


def bulk_on():
    return bool(settings.SMS_BULK_ENABLED)


def bulk_tables_exist():
    return BATCH_TABLE in connection.introspection.table_names()


def dnt_tables_exist():
    return DNT_TABLE in connection.introspection.table_names()
```

- [ ] `models.py` (`new_batch_id` is `new_public_id("bulk")`):

```python
class BulkBatch(models.Model):
    class State(models.TextChoices):
        WAITING = "waiting"
        SENDING = "sending"
        STOPPED = "stopped"
        FINISHED = "finished"

    class Source(models.TextChoices):
        GROUPS = "groups"
        FILE = "file"
        PASTED = "pasted"

    ACTIVE = ("waiting", "sending")

    public_id = models.CharField(max_length=40, unique=True, default=new_batch_id, editable=False)
    platform = models.CharField(max_length=16, choices=Platform.choices, default=SMS)
    owner_kind = models.CharField(max_length=8, default="hq")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    stopped_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    request_id = models.CharField(max_length=64)
    text = models.CharField(max_length=160)            # as typed, without the opt-out line
    source = models.CharField(max_length=8, choices=Source.choices)
    group_count = models.PositiveSmallIntegerField(default=0)   # how many groups, never which
    state = models.CharField(max_length=8, choices=State.choices, default=State.WAITING)
    local_day = models.DateField()                      # the Manila day it was confirmed
    lines_read = models.PositiveIntegerField()
    duplicates = models.PositiveIntegerField()
    refused = models.PositiveIntegerField()
    opted_out = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    left_out = models.PositiveIntegerField()
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    not_sent = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_bulk_platform", condition=Q(platform=SMS)),
            models.CheckConstraint(name="inbox_bulk_owner", condition=Q(owner_kind="hq")),
            models.UniqueConstraint(name="inbox_bulk_one_running", fields=["platform"], condition=Q(state__in=["waiting", "sending"])),
            models.UniqueConstraint(name="inbox_bulk_one_request", fields=["created_by", "request_id"]),
        ]


class BulkRecipient(models.Model):
    class State(models.TextChoices):
        WAITING = "waiting"
        QUEUED = "queued"          # a send record exists; the phone has not reported
        SENT = "sent"
        FAILED = "failed"
        NOT_SENT = "not_sent"

    batch = models.ForeignKey(BulkBatch, on_delete=models.CASCADE, related_name="recipients")
    position = models.PositiveIntegerField()
    mobile = models.CharField(max_length=13)
    state = models.CharField(max_length=8, choices=State.choices, default=State.WAITING)
    reason = models.CharField(max_length=40, blank=True)
    send_record = models.CharField(max_length=40, blank=True)   # the SmsMessage public id

    class Meta:
        constraints = [models.UniqueConstraint(name="inbox_bulk_one_position", fields=["batch", "position"])]
        indexes = [models.Index(fields=["mobile"], name="inbox_bulk_recipient_mobile")]


class DoNotText(models.Model):
    platform = models.CharField(max_length=16, choices=Platform.choices, default=SMS)
    mobile = models.CharField(max_length=13)
    message = models.ForeignKey(ConversationMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_dnt_platform", condition=Q(platform=SMS)),
            models.UniqueConstraint(name="inbox_dnt_one_number", fields=["platform", "mobile"]),
        ]
```

  and on `InboxAccess`: `can_bulk = models.BooleanField(default=False)` with `models.CheckConstraint(name="inbox_access_bulk_needs_send", condition=Q(can_bulk=False) | Q(can_send=True))` added to its `Meta.constraints`.
- [ ] `makemigrations inbox -n bulk`, read the file (section 4), `makemigrations --check` clean.
- [ ] `access.py`: replace the row read with `can_send = InboxAccess.objects.filter(operator=actor, platform=SMS).values_list("can_send", flat=True).first()`; `None` means no row (note `False` is a read-only row: compare `is None`).
- [ ] Tests: an `InboxAccess` with `can_bulk=True, can_send=False` is refused by the database; two active batches are refused by the database, a stopped one and a new one are not; a recipient position is unique per batch; a number is on the list once; `scope_for` still answers for a read-only and a sending row, **and its SQL (`CaptureQueriesContext`) holds no `can_bulk`**; `bulk_tables_exist()` and `dnt_tables_exist()` are true after migrate.

### Task 2: the STOP rule

**Files:** `apps/inbox/storing.py`, `apps/inbox/tests/test_stop_rule.py`.

- [ ] In `store`, after `ConversationMessage.objects.create(...)` (keep the object), when `can_reply` is true and `_is_stop(text)`:

```python
def _is_stop(text):
    # whitespace and punctuation (Unicode category P*) at either end are dropped; nothing inside the text is
    word = text.strip()
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(word[end - 1]).startswith("P"):
        end -= 1
    return word[start:end].strip().casefold() == "stop"

...
            if can_reply and _is_stop(text) and dnt_tables_exist():
                DoNotText.objects.get_or_create(
                    platform=SMS, mobile=contact, defaults={"message": message}
                )
```

  (`dnt_tables_exist()` is evaluated once per `store` call, above the loop, not once per text.) It is inside the same `transaction.atomic()`, so a text and its listing are stored together or not at all. No log line holds the number.
- [ ] Tests (`test_stop_rule.py`, made-up numbers): `STOP`, `stop`, `  Stop  `, `STOP\n`, `stop.`, `STOP!`, `Stop!!`, ` stop. ` list the sender; `STOP please`, `nostop`, `unsubscribe`, `end`, an empty text and a text from `BDO` (a name) do not; the same number twice lists once; the text itself is stored and shown like any other; the list is written with `SMS_BULK_ENABLED` off; with the table missing (`mock` on `dnt_tables_exist`) the text is stored and nothing else happens; the number is in no log line (`assertNoLogs`).

### Task 3: `apps/notifications`: the purpose and three functions

**Files:** `apps/notifications/sms.py`, `services.py`, `apps/notifications/tests/test_sms_bulk.py`.

- [ ] `sms.PURPOSES`: `"inbox_bulk": Purpose(category=INBOX, priority=Priority.MARKETING, paced=True)`.
- [ ] `services.py`: `STAFF_PURPOSES = ("inbox_reply", "inbox_compose", "inbox_bulk")`, and

```python
def sms_room_for_staff():
    """(used today, texts a staff or bulk text may still use today). Reads no SMS table
    while the gateway is off."""
    if not sms.gateway_on():
        return 0, 0
    used = sms.platform_count()
    return used, max(sms.policy().platform_daily_limit - sms.RESERVED_FOR_CODES - used, 0)


def cancel_sms(public_id):
    """Take back a text still waiting for the phone. True when it was waiting; False when the
    phone has it or it is already over."""
    message = SmsMessage.objects.filter(public_id=public_id).first()
    if message is None:
        return False
    taken = SmsMessage.objects.filter(pk=message.pk, status=SmsMessage.Status.QUEUED).update(
        status=SmsMessage.Status.EXPIRED, reason="stopped"
    )
    if taken:
        sms.forget(message)
    return bool(taken)
```

- [ ] Tests: `inbox_bulk` is paced, in the `inbox` category, behind a waiting code and a waiting staff text in `gateway._waiting`; a bulk text is refused at 1,700 used with reason `staff_limit` while a code is still sent; `sms_room_for_staff` at 214 used is (214, 1486); `cancel_sms` on a queued text makes it expired and its text unavailable to `take_text`, on a dispatched one changes nothing and answers False; the purpose list holds nothing else new.

### Task 4: the source reader and the check

**Files:** `apps/inbox/bulk.py`, `apps/inbox/tests/test_bulk.py`.

- [ ] Scope: `bulk_scope(actor)` answers a `BulkScope(actor)` or `None`: `bulk_on()`, `bulk_tables_exist()`, `scope_for(actor, SMS)` is not None and `can_send`, and `InboxAccess.objects.filter(operator=actor, platform=SMS, can_bulk=True).exists()`. Anything else is `None` and the route answers 404 (C2).
- [ ] Constants: `OPT_OUT = " Reply STOP to opt out"`, `TEXT_MAX = 160 - len(OPT_OUT)` (138), `MAX_SOURCE_BYTES = 1_048_576`, `MAX_REFUSED_SHOWN = 200`, `SECONDS = (18, 25)`.
- [ ] `read_source(scope, data)` returns `(numbers, lines_read, refused, duplicates, groups_used)` where `numbers` is the list of Philippine mobiles in source order with repeats dropped:
  - `source == "pasted"`: `data["numbers"]` is a string, one number per line, blank lines skipped, at most `MAX_SOURCE_BYTES`.
  - `source == "file"`: `data["csv"]`, at most `MAX_SOURCE_BYTES` (413 `file_too_large`), UTF-8 text, read with `csv.reader`; a first row whose first cell has no digit is a header; **only the first cell of a row is read**, the other cells are never looked at (design 18.2.2). `csv.Error` is 400 `invalid_file`.
  - `source == "groups"`: `data["groups"]` is a list of group ids (at most 50); needs `contacts_on()` and `contacts_tables_exist()` or it is 400 `invalid_request`; the numbers are `Contact.objects.filter(owner_kind="hq", groups__public_id__in=ids).order_by("pk").values_list("mobile", flat=True).distinct()`; an unknown group id is 404 `not_found`; `lines_read` is the contacts read.
  - Each line goes through `normalise_ph_mobile`; a line that fails is `(line number, "The mobile number is not a Philippine mobile.")` and only the first 200 are kept, the full count is returned.
  - Any other `source` is 400 `invalid_request`.
- [ ] `check(scope, data)`:

```python
def check(scope, data):
    text = _text(data.get("text"))                    # 400 invalid_text with the reason, 138 characters at most
    numbers, lines, refused, repeated, groups = read_source(scope, data)
    listed = set(DoNotText.objects.filter(platform=SMS, mobile__in=numbers).values_list("mobile", flat=True))
    eligible = [n for n in numbers if n not in listed]
    used, room = services.sms_room_for_staff()
    size = min(len(eligible), room)
    return {
        "lines_read": lines, "duplicates": repeated, "refused_count": len(refused[0]),
        "refused": [{"line": n, "reason": r} for n, r in refused[1]],
        "opted_out": len(numbers) - len(eligible), "eligible": len(eligible),
        "used_today": used, "left_today": room, "size": size, "left_out": len(eligible) - size,
        "seconds_min": size * SECONDS[0], "seconds_max": size * SECONDS[1],
        "text_length": len(text) + len(OPT_OUT), "blocked": _blocked(),
    }, eligible[:size], eligible[size:]
```

  `_blocked()` answers `"running"` when a batch is waiting or sending, `"incoming_off"` (C10), `"no_room"` when `room` is 0, or `""`. The route sends the first dict only. `numbers` is chunked (500) in the `__in` query. Nothing is stored, logged or audited by `check`.
- [ ] Tests (made-up numbers, no real list): pasted, file (header, extra columns, names beside the numbers: only the first column is read, and no other column's value appears in any answer or log), groups (a contact in two groups counts once; an unknown group is 404; groups with contacts off is 400); a line that is not a mobile is refused with its line number and no content; 201 refused lines show 200 and count 201; a file over 1 MB is 413 and nothing is read; a number twice counts once; listed numbers are skipped and counted; 2,293 numbers on a day with 214 used give size 1,486, left_out 807 and a time of about 7 to 10 hours; nothing left gives size 0 and `blocked: "no_room"`; a 139-character text is refused, a text with a link is refused, a 138-character text passes and `text_length` is 160; **`check` writes no row and no audit entry**.

### Task 5: confirm, the batch and the audit

**Files:** `apps/inbox/bulk.py`, `apps/operators/audit.py`, `apps/inbox/tests/test_bulk.py`.

- [ ] `audit.ACTIONS`: `"sms_inbox_bulk": KEPT`, `"sms_inbox_bulk_stop": KEPT`, `"sms_inbox_bulk_left_out": KEPT`.
- [ ] `confirm(scope, data)`:

```python
def confirm(scope, data):
    request_id = _request_id(data.get("request_id"))             # 16 to 64 letters, digits, - and _
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise _invalid(count="Type the number of texts.")
    existing = BulkBatch.objects.filter(created_by=scope.actor, request_id=request_id).first()
    if existing:
        return _row(existing), False                              # a repeat: the same batch, 200
    answer, chosen, left = check(scope, data)
    if answer["blocked"]:
        raise ApiError(answer["blocked"] if answer["blocked"] != "running" else "batch_running", _BLOCKED[answer["blocked"]], status=409)
    if answer["size"] == 0 or answer["size"] != count:
        raise ApiError("changed", "The list changed since the check. Check it again.", status=409)
    try:
        with transaction.atomic():
            batch = BulkBatch.objects.create(
                created_by=scope.actor, request_id=request_id, text=_text(data["text"]),
                source=data["source"], group_count=len(data.get("groups") or []),
                local_day=timezone.localdate(), lines_read=answer["lines_read"],
                duplicates=answer["duplicates"], refused=answer["refused_count"],
                opted_out=answer["opted_out"], size=answer["size"], left_out=answer["left_out"],
            )
            BulkRecipient.objects.bulk_create(
                [BulkRecipient(batch=batch, position=i, mobile=n) for i, n in enumerate(chosen)], batch_size=500)
            audit.record(scope.actor, "sms_inbox_bulk", target_type=TARGET, target_id=batch.public_id, detail={...})
    except IntegrityError:
        raise ApiError("batch_running", _BLOCKED["running"], status=409) from None
    return _row(batch), True
```

  The `detail` is the design's list: `platform`, `batch`, `source`, `groups` (how many, never ids or names), `lines_read`, `duplicates`, `refused`, `opted_out`, `size`, `left_out`, `text_length`. **Never the text, a number or a group name.** A repeat of the same `request_id` with a different list is not compared: it answers the first batch.
- [ ] `_row(batch)` is `{"batch", "state", "when", "who": username, "source", "size", "sent", "failed", "not_sent", "waiting", "left_out", "text_length", "seconds_left"}` with no number and no text (design 18.2.1). `waiting` is `size - sent - failed - not_sent`.
- [ ] Tests: confirm with the typed count creates the batch and its recipients in source order, the cut ones are not rows; a wrong count is 409 `changed` and nothing is written; a changed source (one line more) is 409 `changed`; a second confirm while one runs is 409 `batch_running` (code and database); the same `request_id` answers 200 with the same batch and writes no second one and no second audit entry; blocked by incoming off, by `SMS_INBOX_ENABLED` off, by no room; **the audit entry holds the design's list of keys and none of: the text, a number, a group name** (the plan 4 `assertNothingPersonalIn` pattern); a user without `can_bulk`, a read-only user and a user with no row are all 404 on `confirm`, `check`, `list`, `open`, `stop` and `left-out`.

### Task 6: the batch driver

**Files:** `apps/inbox/bulk.py`, `apps/notifications/gateway.py`, `apps/inbox/tests/test_bulk_run.py`.

- [ ] `feed(now)`, called from the poll with the switch on:

```python
def feed(now):
    """Queue the next text of the running batch. Runs inside a phone's poll."""
    if not bulk_tables_exist():
        return
    with transaction.atomic():
        batch = BulkBatch.objects.select_for_update().filter(state__in=BulkBatch.ACTIVE).first()
        if batch is None:
            return
        settle(batch, now)
        if batch.state not in BulkBatch.ACTIVE:
            return
        if batch.local_day != timezone.localdate():
            _end_rest(batch, "day_ended")
            settle(batch, now)
            return
        if batch.recipients.filter(state="queued").exists():
            return                                    # one at a time: the last is not reported yet
        following = batch.recipients.filter(state="waiting").order_by("position").first()
        if following is None:
            return
        result = services.send_sms(
            to=following.mobile, purpose="inbox_bulk", text=batch.text + OPT_OUT,
            ttl=services.SMS_STAFF_TEXT_LIFETIME,
        )
        if result.outcome == services.SMS_QUEUED:
            following.state, following.send_record = "queued", result.message.public_id
            following.save(update_fields=["state", "send_record"])
            if batch.state == "waiting":
                batch.state = "sending"
                batch.save(update_fields=["state"])
        elif result.reason == "staff_limit":
            _end_rest(batch, "staff_limit")
        elif result.reason in ("paused", "gateway_offline", "gateway_off"):
            return                                    # try again at the next poll
        else:
            following.state, following.reason = "not_sent", result.reason
            following.save(update_fields=["state", "reason"])
        settle(batch, now)
```

  `_end_rest(batch, reason)` marks every waiting recipient `not_sent` with `reason`.
- [ ] `settle(batch, now)`: reads `services.sms_statuses` for the batch's queued recipients (one query): `sent` becomes `sent`; `failed`, `expired` or `refused` become `failed` with the record's reason; a record that is gone becomes `failed` with `record_gone`; `queued` and `dispatched` stay. Then it writes the counts from an aggregate over the recipients (`sent`, `failed`, `not_sent`). When no recipient is waiting or queued: a running batch becomes `finished` (`finished_at`), and for a finished or stopped batch **its recipient rows are deleted** (C12). `settle` is idempotent and holds the batch locked.
- [ ] `stop(scope, public_id)`: locks the batch (404 if not found or not the platform's), 409 `not_running` unless waiting or sending; marks every waiting recipient `not_sent` with `stopped`; for each queued recipient `services.cancel_sms(send_record)` true means `not_sent`/`stopped`, false means leave it queued for `settle`; sets `stopped`, `stopped_by`, `stopped_at`; writes `sms_inbox_bulk_stop` with `{platform, batch, sent, waiting, not_sent}`; calls `settle`.
- [ ] `gateway.poll`: the two lines of section 3.1.
- [ ] Tests (a fake phone through `gateway._hand_out`, made-up numbers, 5 recipients): the first poll queues and hands out one text, with the opt-out line, to the first recipient, and the batch is `sending`; a second poll before the report queues nothing; after a `sent` report the next poll queues the next one; a code asked for while the batch runs is handed out before the next bulk text; a staff reply is handed out before the next bulk text; with the phone offline nothing is queued and the batch stays `waiting`; with `sending_stopped` nothing is queued and recipients stay waiting; at `staff_limit` the rest are `not_sent` and the batch finishes with the right counts; a `failed` report counts under `failed` with its reason; a day change (`mock` on `timezone.localdate`) marks the rest `not_sent` with `day_ended` and finishes; when every recipient is final the rows are deleted and the batch row keeps `sent`, `failed` and `not_sent`; `stop` at 2 sent of 5 leaves 3 `not_sent`, sends nothing more, frees the one-at-a-time rule (a new confirm works at once), cancels a queued text so `take_text` gives nothing, and leaves a dispatched one to finish and then removes its row; stopping a finished batch is 409; `SMS_BULK_ENABLED` off: the poll calls no bulk code and reads no bulk table (`assertNumQueries` equal to the poll without the setting).

### Task 7: the routes

**Files:** `apps/inbox/bulk_staff.py`, `apps/operators/staff_views.py`, `apps/inbox/staff.py`, `apps/billing/tests/test_staff_finance.py`, `apps/inbox/tests/test_staff.py`, `apps/inbox/tests/test_contacts_staff.py`, `apps/inbox/tests/test_bulk_staff.py`.

- [ ] `bulk_staff.py` follows `contacts_staff.py`: `_route(view, limit=...)` calls `bulk_scope(operator)` (404 when `None`), reads the JSON body (4096 bytes; **2,400,000 for `check`, `left-out` and `confirm`**), turns `ApiError` into the shared error body and sets `Cache-Control: no-store`. The seven routes:

| Path | Body | Answer |
|---|---|---|
| `bulk/status/` | none | `{"bulk": true, "running": bool, "used_today", "left_today", "reserved_for_codes", "opt_out_line", "text_max": 138}` |
| `bulk/list/` | `{"cursor", "limit"}` | `{"results": [row], "next"}` newest first (settles the running batch first, so the list is true with the phone away) |
| `bulk/check/` | `{"text", "source", "groups" or "numbers" or "csv"}` | the check dict of task 4 |
| `bulk/left-out/` | as `check` | `200` `text/csv` body: one `+639…` per line, `no-store`, and one `sms_inbox_bulk_left_out` entry with `{platform, source, count}`; 409 `nothing_left_out` when there are none |
| `bulk/confirm/` | as `check` plus `count`, `request_id` | `201` (or `200` for a repeat) with the batch row |
| `bulk/open/` | `{"batch", "cursor", "limit"}` | the batch row and `results` `[{"number": masked, "state", "reason"}]` while rows exist, else `[]`; settles first |
| `bulk/stop/` | `{"batch"}` | the batch row |

- [ ] `staff_views.py`: the four lines of section 3.1 (`from apps.inbox import bulk_staff` and an `ROUTES.update` through `_for_staff`). `apps/inbox/staff.py` `status` gains `"bulk_running": bulk.running_for(operator)`, true only for a user with `bulk_scope` and a running batch, and false with no query otherwise.
- [ ] The three route-count tests change as plan 6 changed them; one new assertion in each that no `bulk/` path is an inbox, contacts, Finance or sign-in path.
- [ ] Tests: every route is 404 with `SMS_BULK_ENABLED` off, with `SMS_INBOX_ENABLED` off, with the tables missing, with no access row, with a read-only row, with a sending row without `can_bulk`, and 401 for no or a first-step session; every route is POST only and takes the staff origin only; every answer, 404s and 403s included, carries `no-store`; no path holds a value; **no answer of `list`, `open`, `status` holds a full number or the text; `left-out` is the only one with full numbers**; a body over 4096 bytes is read as empty on the routes that are not `check`, `left-out`, `confirm`; the list shows each state with no number and no text; `open` pages with a valid cursor and refuses a bad one; with `inbox.0003` not applied (the table check mocked) every route is 404 and `inbox/status/` still answers.

### Task 8: `opted_out`, the access command, retention and deletion

**Files:** `apps/inbox/contacts.py`, `reading.py`, `management/commands/sms_inbox_access.py`, `sms_inbox_delete.py`, `retention.py`, tests.

- [ ] `contacts.py`: a helper `_opted_out(numbers)` returns the set of those numbers on the list (empty, with no query, when `dnt_tables_exist()` is false); `_row` takes it and adds `opted_out`; `list_contacts` and `open_contact` pass it (one extra query per page). The upload check gains `already_opted_out` (how many of the file's numbers are listed; counted, not refused; design 19.3: "added and stays marked").
- [ ] `reading.py`: the conversation answers of `open` gain `opted_out` for a conversation that can be replied to (design 18.5: "Opted out of bulk SMS"). A reply or a composed text to that person is still allowed.
- [ ] `sms_inbox_access grant --bulk`: requires `--send` (the command refuses `--bulk` without it), sets `can_bulk`, audited with `level` `"bulk"`; `grant --send` on a row that has `can_bulk` removes it only if `--bulk` is not given and says so; `revoke` removes the row; `list` shows "read, send and bulk".
- [ ] `retention.py`: `bulk_batches(now, dry_run)` deletes `BulkBatch` rows 365 days after `created_at` (their recipients with them) wherever the table exists; registered; not behind `RETENTION_ENABLED` or any switch. **The do-not-text list is not touched by any rule.**
- [ ] `sms_inbox_delete`: inside the same transaction, `BulkRecipient.objects.filter(mobile=number)` for a Philippine mobile is counted and, with `--confirm`, deleted; the bulk send records go with the staff texts' (C18); the audit detail gains `bulk_recipients`; the counts line gains ", N bulk recipient(s)" only when `bulk_tables_exist()`. The command **does not touch the do-not-text list**, and says so in the runbook (C17).
- [ ] Tests: `opted_out` on a listed contact in a list and in `open`, false for an unlisted one, absent-safe with the table missing; a conversation answer; the grant rules above; a batch older than 365 days is deleted with its recipients, a younger one and a `DoNotText` row older than 365 days are kept; the delete command counts and removes a waiting recipient and a queued bulk send record for the typed number, leaves another number's, leaves the do-not-text row, and works with `SMS_BULK_ENABLED` off and with `inbox.0003` not applied.

### Task 9: the runbooks

- [ ] `docs/runbooks/sms-gateway.md`, a section "Bulk SMS": the switch `SMS_BULK_ENABLED`, off, per environment, Coach Ed's approval, **and only after the NPC and privacy wording covers the announcement (R11)** and the list of filings above is amended; the order to turn it on (`inbox.0003` applied, `SMS_INBOX_ENABLED`, the phone's incoming switch, access granted with `--bulk`, then `SMS_BULK_ENABLED=1`); one batch at a time; the 1,700-a-day room; how a batch is driven (the poll, one text at a time, about 145 to 200 an hour); what is kept (the text and counts 365 days, the numbers only while the batch runs, a send record per number 365 days, the do-not-text list with no end); how to stop (the page, or unsetting the variable freezes the batch); how to continue a list the next day (the left-out file from the check).
- [ ] The **blocked-SIM note** (design 18.6): how it shows (many texts turn "Not sent" with `phone_failed` or `no_report`, or the phone reports failures; codes stop with them), what to do (stop the batch, check the SIM with the carrier, `sms_policy` stop if needed, and that codes go by email meanwhile only where a fallback exists), and that the risk was accepted by Coach Ed (R14).
- [ ] `docs/runbooks/retention.md`: rows for `bulk_batches` (365 days) and for the do-not-text list ("kept until the person asks otherwise; no job and no command removes it; a request to remove a number is an NPC and privacy decision, not built"). `sms-gateway.md` "Contacts" and the deletion paragraph gain one sentence each (bulk recipients; the list is not deleted by `sms_inbox_delete`).

### Task 10: the whole check

- [ ] `uv run python manage.py test apps.inbox apps.operators apps.billing apps.notifications`, then the full suite: all pass, the skipped count unchanged. `makemigrations --check --dry-run` clean, `check` clean, `check --deploy` only the dev-setting warnings it showed before.
- [ ] By hand, scratch SQLite, made-up numbers, a local server, a staff session as in plans 4 and 6, the fake phone (`sms_fake_phone`), `SMS_INBOX_ENABLED=1`, the phone's incoming switch on, an access row with `--send --bulk`:
  1. `SMS_BULK_ENABLED` unset: all seven routes answer 404 and the poll runs as before.
  2. On: a pasted list of 30 numbers with 3 bad lines and 2 repeats gives the right counts; send STOP from one of them (the fake phone's `received`): the next check counts it opted out.
  3. Confirm with the typed count: the fake phone prints one text at a time, each ending "Reply STOP to opt out", about one every 18 to 25 seconds (this walk may use a shorter gap through the fake phone only, reported); `list` shows "Still sending" and `open` shows masked results; a code asked for meanwhile prints before the next bulk text; Stop ends it and sends nothing more.
  4. A list longer than the room is cut and `left-out` returns the rest as a file; the next day's batch (a `mock` of the date, or the next real day) takes it.
  5. Migrate `inbox` back to `0002`: every bulk route is 404, `inbox/status/`, reply and compose and the poll still work, and a received STOP is stored as a text and lists nobody.
  6. `sms_inbox_delete` for a made-up number that is a waiting recipient: counted, then removed, the number in neither output.
- [ ] How the staff session is made locally is reported with the result.

## 6. What the pages of plan 9 need to know

- The seven routes, their bodies and answers (task 7), all `POST`, with the staff origin and the HQ cookie. The "Bulk SMS" button is shown when `bulk/status/` answers 200; the chip "Sending" when `inbox/status/` says `bulk_running`.
- The page keeps the source (the file's text, the pasted text or the chosen group ids) and the text in memory from the check to the confirm, and sends them again with `count` and a new `request_id`. A 409 `changed` sends the user back to the check; a 409 `batch_running` shows the running batch.
- The refused lines are in the check's answer as line number and reason; the page builds the download from them. The left-out file comes from `bulk/left-out/` and is offered at the check, before the confirm (C12).
- The page asks `list` or `open` again by itself while a batch runs; how often is the page's choice, and each call settles the batch.
- The opt-out line is 22 characters and the text limit 138, from `bulk/status/`; the 300 kept for codes is `reserved_for_codes`.

## 7. The gate for this plan, and what still needs Coach Ed's approval

### 7.1 The gate

The coordinator's read, the NPC coordinator's privacy review (a full number kept past 365 days on the list, STOP read from every stored text, a batch's numbers copied to a table, the left-out file) and the Santé/onboarding coordinator's overlap check on `gateway.py`, `staff_views.py` and `services.py`. It does not return to Coach Ed.

### 7.2 Not asked, each still its own approval

Running `inbox.0003` anywhere; `SMS_BULK_ENABLED` in any shared environment; any access grant with `--bulk`; the NPC and privacy wording; incoming texts on; a second SIM; anything on production.

### 7.3 For the NPC coordinator

1. **The do-not-text list has no removal** (C17). A person who asks for "all my data" is answered with a number that stays. Is that acceptable wording, or is a command to remove a number, with its own audit entry, wanted?
2. **C12:** the numbers of a finished batch are gone, so a failed number cannot be retried from the batch.
3. **C13:** the left-out file hands staff full numbers of contacts for a source of groups.
4. **C16:** "stop" only, in any case, with whitespace and leading or trailing punctuation around it ("STOP.", "Stop!" list); "STOP please", "nostop", "unsubscribe" and "end" do not list. As the NPC coordinator required.

## 8. Review, overlap and order

- Backend: PR 100 (plan 6 build) must be merged first. No other open backend PR at the time of writing.
- Order of the build: plan 6 build merged and `inbox.0002` run on staging, then plan 8's build, then `inbox.0003` on staging by the coordinator.
- Files shared with others: `staff_views.py` (plans 4 and 6), `audit.py`, `config/settings.py` (one line), `gateway.py` (four lines), `services.py` and `sms.py` of `apps/notifications`, `contacts.py` and `sms_inbox_delete.py` (plan 6's files), three route-count tests.

## 9. What was verified, and what was not

- Read, not run: the code of `sms.py`, `services.py`, `gateway.py`, `storing.py`, `sending.py`, `access.py`, `staff.py`, `retention.py`, the access and delete commands and, from PR 100, `contacts.py` and `contacts_staff.py`; the design's section 18 and 19.3 to 19.5.
- **Nothing in this plan has been run.** In particular not verified: that the poll feeds fast enough to keep the phone busy with a 5-second wait (the plan assumes the phone's gap, not the poll, sets the pace); the partial unique constraints on PostgreSQL; `select_for_update` on the batch inside the poll; the size of the confirm body through Railway's proxy (a 1 MB file, up to 2.4 MB escaped, as in plan 6); the blocked-SIM behaviour on a real SIM; any STOP from a real phone.
- The design's `GET` routes, `check_id`, a left-out route for a confirmed batch and the `me/` answer's `bulk` keys are not followed (C1, C6, C12); the page learns bulk from `bulk/status/`.
