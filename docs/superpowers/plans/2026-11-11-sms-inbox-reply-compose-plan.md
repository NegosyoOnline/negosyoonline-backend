# SMS inbox, plan 4: reply, compose and the inbox staff routes (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128).

Status: **a plan only. Nothing here is built.** It is the fourth plan of the build order (ClickUp comment 1100430000488202): "Backend: reply and compose, the 300 reserve, the inbox routes". Under Coach Ed's ruling "build until plan 9" (comment 1100430000490204) it does not return to him: the gate is the coordinator's read and the NPC coordinator's privacy review (section 7). Everything is built switched off.

**The code in this plan has not been run.** It was written against the code at `b6d37a4` by reading it. The builder writes each test first, sees it fail, and reports every place where the code below had to change.

## 1. Sources

| Short name | Source |
|---|---|
| Design | `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md`, sections 4, 6 to 10, 12, 15, 16 and 18.10 |
| Plan 3 | `docs/superpowers/plans/2026-11-11-sms-inbox-storage-plan.md`, section 2.2 (what it left to this plan) and task 9 (what `sms_inbox_delete` does not remove) |
| Task | ClickUp z8uq9me128 and its comments, read 2026-11-11: the build order (1100430000488202), "build until plan 9" (1100430000490204), no Django admin (1100430000489781), plan 3 merged and its migration applied on staging (as told by the coordinator; comment 1100430000490836 not read by me) |
| Code | Backend `origin/main` at `b6d37a4`: `apps/inbox`, `apps/notifications/sms.py`, `services.py`, `gateway.py`, `models.py`, `apps/operators/staff_views.py`, `audit.py`, `apps/billing/staff.py`, `apps/billing/tests/test_staff_finance.py`, `apps/common/pagination.py`, `errors.py`, `masking.py`, `phones.py` |

Rulings applied, not reopened: R3 (explicit permission, every reply and composed text audited), R4 (free text through the one send function: link gate, 160 characters, counted in the cap, the sending interval kept, no marketing), R5 (any Philippine mobile), R20 ("Hold back 300"), R23 (the screens call the backend), and no Django admin screen.

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | Two declared purposes, `inbox_reply` and `inbox_compose`; a third category, `inbox`; a purpose can be declared paced | Design 6.2 |
| 2 | The migration `notifications.0004_alter_smsmessage_category` (section 4) | Design 12 |
| 3 | The reserve: a staff text is refused once the day's count reaches the daily limit less 300 | Design 6.3, 18.10; R20 |
| 4 | `apps/inbox/sending.py`: `reply` and `compose`, through `send_sms`, with the audit entries `sms_inbox_reply` and `sms_inbox_compose` | Design 6.1, 8 |
| 5 | The read functions gain paging, the unread filter and the status of an outgoing text | Design 10 |
| 6 | Six staff routes under `/staff/v1/inbox/`, in their own module, wired into the staff routes by four added lines | Design 10; the coordinator's brief |
| 7 | `sms_inbox_delete` also deletes the send records of staff texts to the typed number | Plan 3 task 9: "Each of those plans says how its records are deleted for one number" |
| 8 | The runbooks, and one History line in `docs/runbooks/migrations.md` for `inbox.0001_initial` on staging | Design 12; the coordinator's brief |

### 2.2 What is not built here

- Any page: plan 5, in the frontend repository.
- Bulk SMS, contacts, groups, the do-not-text list, and the answer fields that belong to them (`bulk`, `bulk_enabled`, `bulk_running`, `opted_out`): plans 6 to 9.
- Any change to `GET /staff/v1/me/` (C2).
- Any Django admin screen.
- Any Railway variable, any run of a migration, any switch turned on, any access grant, anything on staging or production (section 7).

### 2.3 Files

| File | Change |
|---|---|
| `apps/notifications/models.py` | `SmsMessage.Category` gains `INBOX = "inbox"` |
| `apps/notifications/migrations/0004_alter_smsmessage_category.py` | New (section 4) |
| `apps/notifications/sms.py` | `Purpose.paced`; two purposes; `INBOX`, `STAFF_TEXT_LIFETIME`, `RESERVED_FOR_CODES`; `paced_purposes()`; `refusal`; the comment on the purpose list |
| `apps/notifications/services.py` | Four small functions other apps may call: `sms_text_problem`, `sms_line`, `sms_status`, `staff_send_records` |
| `apps/notifications/gateway.py` | `_waiting` and `_hand_out` pace by purpose as well as by the marketing class |
| `apps/notifications/tests/test_sms_send.py`, `test_sms_gateway.py` | Added cases; any case that lists the declared purposes or categories gains the new ones |
| `apps/inbox/sending.py`, `apps/inbox/staff.py` | New |
| `apps/inbox/reading.py` | `conversations` and `open_conversation` gain paging; rows gain `platform`, `last_status`; a message gains `kind`, `status`, `reason`, `author` |
| `apps/inbox/management/commands/sms_inbox_delete.py` | Also the send records (task 5) |
| `apps/inbox/tests/test_sending.py`, `test_staff.py` | New |
| `apps/inbox/tests/test_reading.py`, `test_delete.py`, `test_access.py` | Changed and added cases |
| `apps/operators/audit.py` | Two added lines in `ACTIONS` |
| `apps/operators/staff_views.py` | **Four added lines, none changed** (section 3.1) |
| `apps/billing/tests/test_staff_finance.py` | **One import and two assertions changed** (section 3.1). The billing coordinator's file |
| `docs/runbooks/sms-gateway.md`, `docs/runbooks/migrations.md`, `docs/runbooks/retention.md` | Task 6 (`retention.md`: the `admin_audit_personal_values` row and the sentence after the table name `sms_inbox_reply` and `sms_inbox_compose` as well as `sms_inbox_open`) |

No file under `config/`, `apps/billing` other than that one test file, or the frontend repository.

## 3. Choices this plan makes, each reported and none hidden

Each is a routine detail inside the approved design, decided as the smallest safe one. The coordinator or Coach Ed may overrule any of them.

### 3.1 The shared file `apps/operators/staff_views.py`

The staff routes are a table of exact paths, each with one method; a route another app owns is registered through `_for_staff`, which calls it with the staff user of a session that passed both steps. The Finance routes are there this way (PR 93). The inbox routes take the same way, so the origin check, the session check and the method check stay in that one file and are not written a second time.

The four lines, added and nothing else changed:

```python
from apps.inbox import staff as inbox_staff
```

after the line `from apps.billing import staff as billing_staff`, and

```python
ROUTES.update(
    {path: ("POST", _for_staff(view)) for path, view in inbox_staff.ROUTES.items()}
)
```

after the `ROUTES.update(...)` of the Finance routes.

**One Finance test counts every staff route, so it must change with this.** In `apps/billing/tests/test_staff_finance.py`, class `FinanceGateTests`:

- `test_the_routes_are_registered_beside_the_sign_in_routes_and_nothing_else_changed`: `set(staff_views.ROUTES) - set(staff.ROUTES)` becomes `set(staff_views.ROUTES) - set(staff.ROUTES) - set(inbox_staff.ROUTES)`.
- `test_no_finance_route_takes_the_place_of_another_staff_route`: the last assertion's right side gains `+ len(inbox_staff.ROUTES)`.
- One import: `from apps.inbox import staff as inbox_staff`.

What each test proves about the Finance routes is unchanged. The inbox has the same two tests of its own (task 4).

### 3.2 The choices

| # | Choice | Why | Source |
|---|---|---|---|
| C1 | **Every inbox route is a `POST` with a small JSON body, the ones that only read too, and no route has an id in its address.** The design's table shows `GET` routes with `{id}` in the address and `?platform=sms&state=unread&cursor=` | The staff route table matches exact paths with one method each. Supporting an id in a path and a query string would mean rewriting the shared dispatcher. The Finance routes already settled this the same way. It keeps the design's rule that no value is in an address, and goes further: no id is either | Design 10 ("the exact prefix is for the plan"); `staff_views.py`; `apps/billing/staff.py` |
| C2 | **`GET /staff/v1/me/` is not changed.** What the menu and the buttons may show comes from `inbox/status/`: it answers `read` and `send`, and answers `not_found` to a staff user with no access or while the inbox is off | `me/` is another plan's route in a shared file. A page that gets `not_found` shows no Inbox item | Design 10; the coordinator's brief ("prefer the smaller touch") |
| C3 | The 300 is a constant in code, `RESERVED_FOR_CODES` in `apps/notifications/sms.py`. No setting in `config/settings.py`, no column, no screen | The design leaves where it is stored to the plan and says it is not changed on any screen. A constant can only change through a reviewed pull request, which is where Coach Ed's ruling on a new value would be applied. No migration | Design 18.10 |
| C4 | A staff text refused by the reserve gets the reason `staff_limit` and the one message "Today's limit for staff texts is reached. 300 texts are kept for sign-in codes." The design's older line "Today's text limit is reached. Nothing was sent." is not used | A staff text always meets the reserve before the full limit, so the older line could never be shown. R20 is the later ruling | Design 6.3 against 6.5 |
| C5 | A text or a number refused on the form (empty, too long, a link, not a Philippine mobile) writes **no audit entry** and no record. An attempt that reaches the send function and is refused (limit, kill switch, phone offline, gateway off), and one refused by the hourly limit, is audited | The design audits "each reply, sent or refused", and says of the send function's refusal that "the attempt is still audited". A form error never reaches the send function and sends nothing. **For the NPC coordinator's review** | Design 6.1 step 5, 8 |
| C6 | A staff user with read access who calls a send route is answered 403 `forbidden`, not `not_found`, and nothing is audited | That user can already see the inbox, so the answer gives nothing away. The design's test list says only "is refused" | Design 15 |
| C7 | The hourly limit of 60 is counted from the outgoing messages that staff user wrote in the last hour, with that user's access row locked while a text is queued. Refused attempts do not count | The rows are the record of what was sent. No cache entry to drift. The lock stops two calls at once from both taking the 60th | Design 6.3 |
| C8 | Every send carries a `request_id` made by the page (16 to 64 letters, digits, `-` or `_`). It is held in the cache for 24 hours per staff user. A repeat answers the first text and sends nothing; a repeat while the first is still being sent answers 409 `request_in_progress`; after a refusal the same id may be used again. No column and no migration | Design 15: "a repeated send with the same request id does not send twice" | Design 15 |
| C9 | A composed text starts or joins the conversation on the system phone's line (`sms.pick_device()`), the only phone that sends | Design 4.1: the owner is taken from the phone the text leaves from. Today one phone sends | Design 4.1; `sms.py` |
| C10 | A reply in a conversation that cannot be replied to is answered 400 `cannot_reply`. A new code, not in the design's list | The page offers no reply there; this is the backend's own refusal | Design 6.4 |
| C11 | `limit_reached` is answered with status 429 | The design names the code and no status | Design 10 |
| C12 | The list is paged 20 at a time (100 at most), newest conversation first. A conversation's texts are paged 50 at a time (100 at most), the newest page first, each page oldest to newest. **Each page of texts is one `sms_inbox_open` entry** | The design shows `cursor` and `next` and leaves the sizes to the plan. Every answer that carries texts is audited | Design 8, 10 |
| C13 | The answers keep plan 3's names where they differ from the design's table: `contact` (always masked) not `contact_masked`, `at` not `occurred_at`; a send answers `{"conversation", "message"}` for a reply and a composed text alike | They are built, tested and reviewed. One shape for both sends | Plan 3 task 7 |
| C14 | An outgoing text's `status` is the send record's own word (`queued`, `dispatched`, `sent`, `failed`, `expired`), or null when the record is gone. The page turns it into "Waiting", "Sent" or "Not sent" | Design 4.3: no status column on the message; the status is read from the send record | Design 4.3, 6.5 |
| C15 | **`sms_inbox_delete` with `--confirm` also deletes the send records of staff texts (`inbox_reply`, `inbox_compose`) to the typed number, and forgets a text still waiting for the phone, so it is never sent.** It does not touch the send records of codes. It refuses, deleting nothing, while a staff text to that number is with the phone | Plan 3 said this plan must say how its records are deleted for one number. A send record holds the full number. A record the phone holds cannot be deleted without making the phone's report fail. **For the NPC coordinator's review** (section 5, task 5) | Plan 3 task 9 |
| C16 | `inbox/status/` reads the phone and the count through one function in `apps/notifications/services.py`. While `SMS_GATEWAY_ENABLED` is off it answers the phone offline and null counts, and reads no SMS table | The rule of that app: nothing reads an SMS table while the gateway is off | `services.sms_on` |

### 3.3 What staff texts do that is new, said plainly

- **A staff text waits up to 1 hour for the phone**, not a code's 10 minutes (design 6.1).
- **A staff text carries the 18 to 25 second gap, and one is with the phone at a time** (design 6.3). A code never waits for one.
- **Staff texts and codes share the day's count.** A staff text is refused from the 1,701st text of the day when the limit is 2,000; codes may use all 2,000. If the limit is set to 300 or less, no staff text can be sent.
- **A refused staff text leaves a send record** with status `refused` (number, purpose, reason, no text), as every refused text does, and one ERROR log line `sms_not_sent reason=… purpose=…` with no number and no text. It leaves no inbox row.
- **The text of a staff text is stored** in `ConversationMessage.body` for 365 days, with the staff user who wrote it. It is the first text the backend writes down that it sent. This is the design (4.3); the NPC wording must cover it before any switch is turned on (design 14).
- Code cannot recognise a marketing text. The one-number form, the hourly limit and the audit record are the controls (design 6.3).

## 4. The migration

One migration, in `apps/notifications`:

`0004_alter_smsmessage_category`: one `AlterField` on `SmsMessage.category`, whose choices become `code`, `status`, `inbox`. It depends on `notifications.0003_sms_daily_limits_start_at_2000`. No column is added, removed or resized (`max_length` stays 10), no row is changed, and no other operation is in it. If `makemigrations` writes anything else, the builder stops and reports it.

Writing it is covered by the standing approval for a migration an approved plan specifies. Running it on staging is the coordinator's step after the merge. Production is never covered.

**The code can be released before it is applied:** choices are checked by Django's forms, not by the database, so a staff text could be stored either way; and no staff text can be sent until `SMS_INBOX_ENABLED` is set and access is granted.

No migration in `apps/inbox`.

## 5. Tasks

Every task: tests first, see them fail, then the code, then the targeted tests, then a commit whose message ends with `[z8uq9me128]`. Test names carry a number so a report can cite them. `InboxTestCase` (plan 3) is the base unless a task says otherwise.

### Task 1: the purposes, the category, the reserve and the pace

**Files:** `apps/notifications/models.py`, the migration, `sms.py`, `services.py`, `gateway.py`, `tests/test_sms_send.py`, `tests/test_sms_gateway.py`.

- [ ] **Step 1: the failing tests.**

| Test | Asserts |
|---|---|
| `test_30_the_two_staff_purposes_are_declared` | `sms.PURPOSES["inbox_reply"]` and `["inbox_compose"]`: category `inbox`, priority `REPLY`, `paced` true. `mobile_verify` is not paced. No purpose has the marketing class |
| `test_31_a_staff_text_makes_a_send_record_without_a_site_or_a_fallback` | `send_sms(to=…, purpose="inbox_reply", text=…, ttl=sms.STAFF_TEXT_LIFETIME)` answers `queued`; the record has category `inbox`, no site, `expires_at` one hour on; it takes one slot of the platform count |
| `test_32_the_reserve_refuses_a_staff_text_and_not_a_code` | Limit 305: after 5 texts a staff text is refused with reason `staff_limit`, the record is `refused`, the count is still 5; a code is still queued. Limit 300 and limit 100: a staff text is refused at once |
| `test_33_a_refused_staff_text_gives_back_its_slot` | After a `staff_limit` refusal and after a `gateway_offline` refusal the platform count is unchanged |
| `test_34_the_helpers_other_apps_call` | `services.sms_text_problem` answers what `sms.text_problem` answers; `services.sms_status()` answers `phone_online`, `used_today`, `cap`, `reserved_for_codes`; with the gateway off it answers offline and null counts with `assertNumQueries(0)` |
| `test_35_a_staff_text_carries_a_gap_and_a_code_does_not` (gateway) | A queued `inbox_reply` is handed out with a gap from 18 to 25; a code in the same answer has gap 0 and comes first |
| `test_36_one_staff_text_at_a_time` (gateway) | Two staff texts waiting: one poll hands out the first only; a code queued meanwhile is handed out; after the first is reported, the second is handed out |
| `test_37_the_reply_class_alone_is_not_paced` (gateway) | The existing tests 19, 20 and 21 pass unchanged: a text of the `REPLY` class whose purpose is not paced still has gap 0 |

- [ ] **Step 2:** run; fail.
- [ ] **Step 3: the code.** In `models.py`, `Category` gains `INBOX = "inbox"`. Then `uv run python manage.py makemigrations notifications` and compare the file with section 4.

In `sms.py`:

```python
CODE, STATUS = SmsMessage.Category.CODE.value, SmsMessage.Category.STATUS.value
INBOX = SmsMessage.Category.INBOX.value

STAFF_TEXT_LIFETIME = 3600  # seconds a staff text waits for the phone
# Of the day's texts, this many are for codes only (Coach Ed, "Hold back 300"). A staff text
# is refused once the day's count reaches the limit less this. Changing it is his ruling.
RESERVED_FOR_CODES = 300


@dataclass(frozen=True)
class Purpose:
    category: str
    priority: int
    paced: bool = False  # sent no sooner than the gap after the last paced text


# The closed list of what may be sent. A text of any other kind cannot be sent without a
# reviewed change to this list: codes, order-status texts, and a text an HQ staff member
# writes to one number from the inbox. Marketing has no purpose here and cannot be sent.
PURPOSES = {
    "mobile_verify": Purpose(category=CODE, priority=Priority.CODE),
    "inbox_reply": Purpose(category=INBOX, priority=Priority.REPLY, paced=True),
    "inbox_compose": Purpose(category=INBOX, priority=Priority.REPLY, paced=True),
}


def paced_purposes():
    return [name for name, declared in PURPOSES.items() if declared.paced]
```

In `refusal`, the platform claim becomes:

```python
    limit, reason = rules.platform_daily_limit, "platform_limit"
    if message.category == INBOX:
        limit, reason = max(limit - RESERVED_FOR_CODES, 0), "staff_limit"
    if not _claim(_platform_key(), limit, _counted(message).count):
        if site_key:
            _release(site_key)
        return reason
```

In `services.py`, after `sms_on`:

```python
SMS_STAFF_TEXT_LIFETIME = sms.STAFF_TEXT_LIFETIME
SMS_RESERVED_FOR_CODES = sms.RESERVED_FOR_CODES
STAFF_PURPOSES = ("inbox_reply", "inbox_compose")


def sms_text_problem(text):
    """Why this text cannot be one SMS, or "". For a form that shows the reason before sending."""
    return sms.text_problem(text)


def sms_line():
    """The phone that sends, or None."""
    return sms.pick_device()


def sms_status():
    """What a status line may show. Reads no SMS table while the gateway is off."""
    if not sms.gateway_on():
        return {"phone_online": False, "used_today": None, "cap": None, "reserved_for_codes": None}
    return {
        "phone_online": sms.gateway_online(),
        "used_today": sms.platform_count(),
        "cap": sms.policy().platform_daily_limit,
        "reserved_for_codes": sms.RESERVED_FOR_CODES,
    }


def staff_send_records(to):
    """The send records of the staff texts to one number, for the deletion command."""
    return SmsMessage.objects.filter(recipient=to, purpose__in=STAFF_PURPOSES)


def forget_sms(message):
    """Drop a waiting text, so a deleted record's text is never handed to the phone."""
    sms.forget(message)
```

In `gateway.py`, `_waiting` selects paced texts with one expression and `_hand_out` uses the same rule:

```python
def _paced():
    return Q(priority=SmsMessage.Priority.MARKETING) | Q(purpose__in=sms.paced_purposes())
```

`queued.filter(priority=MARKETING)` becomes `queued.filter(_paced())`, `queued.exclude(priority=MARKETING)` becomes `queued.exclude(_paced())`, the `pacing_now` filter becomes `.filter(_paced(), device=device, status=Status.DISPATCHED)`, and in `_hand_out` `paced = message.priority == SmsMessage.Priority.MARKETING or message.purpose in sms.paced_purposes()`.

- [ ] **Step 4:** run `apps.notifications`; pass. `makemigrations --check` clean.
- [ ] **Step 5:** commit: `feat(notifications): staff texts are a declared, paced purpose with 300 a day kept for codes [z8uq9me128]`.

### Task 2: replying and composing

**Files:** new `apps/inbox/sending.py`, `apps/inbox/tests/test_sending.py` (including `test_15`, which imports `apps.operators.retention`); `apps/operators/audit.py`; `apps/inbox/tests/test_access.py` (`test_15`).

In `audit.py`, after `"sms_inbox_delete": KEPT,`: `"sms_inbox_reply": PERSONAL,` and `"sms_inbox_compose": PERSONAL,`.

- [ ] **Step 1: the failing tests**, class `SendingTests(InboxTestCase)`: the staff user holds an `InboxAccess` row with `can_send`, and the phone polled just now.

| Test | Asserts |
|---|---|
| `test_1_a_reply_is_one_send_record_one_message_and_one_entry` | The send record: recipient the full number, purpose `inbox_reply`, category `inbox`, status `queued`, no text anywhere on it. The message: direction `out`, the text, `kind` `reply`, `author` the staff user, `gateway_ref` the send record's id. The conversation's `last_message_at` moved and its `unread_count` did not. One entry `sms_inbox_reply`: target the conversation, `detail` exactly `{"platform": "sms", "outcome": "queued", "length": N, "message": id, "send_record": id}`, `personal` `{"contact": "+63918***0000"}`. The text waits in the cache for the phone |
| `test_2_a_composed_text_starts_a_conversation` | Every form of the number `normalise_ph_mobile` accepts. A new conversation on the system phone's line, owner `hq`, `can_reply` true; `detail` has `new_conversation` true; `kind` `compose` |
| `test_3_composing_to_a_number_with_a_conversation_adds_to_it` | One conversation still; `new_conversation` false |
| `test_4_a_bad_text_is_refused_before_anything_is_queued` | Empty, 161 characters, a character outside the SMS set, and each link form of `test_sms_send`: `ApiError` `invalid_text` with the send function's own words in `fields.text`; no send record, no message, no entry, no cache entry for the text |
| `test_5_a_number_that_is_not_a_philippine_mobile_is_refused` | A landline, a foreign number, a short number, a name, a value that is not a string: `invalid_mobile`, `fields.to`; nothing written |
| `test_6_a_refused_send_stores_no_message_and_is_audited` | For the kill switch, the phone offline, the gateway off and the reserve: the error's code, status and words of the table below; no message; the conversation unchanged (and none created by a composed text); one entry with `outcome` `refused` and the `reason`, and `send_record` when one was written; for a composed text with no conversation the entry's `target_id` is blank |
| `test_7_the_hourly_limit` | With 60 outgoing messages by this staff user in the last hour, the next is refused `rate_limited` (429), nothing is queued, the entry has `reason` `rate_limited`. Messages older than an hour and another staff user's messages do not count |
| `test_8_read_access_cannot_send` | Without `can_send`: `ApiError` `forbidden`, 403, for a reply and a composed text; nothing written, no entry |
| `test_9_a_reply_outside_the_scope_is_not_found` | An id that is not a conversation id, an unknown id, and a `site` conversation (made in the test): `not_found`, 404; nothing written |
| `test_10_a_sender_that_cannot_be_replied_to` | A conversation from `GLOBE` and one from a foreign number: `cannot_reply`; nothing written |
| `test_11_the_same_request_id_does_not_send_twice` | The second call answers the same message and `created` false; one send record, one message, one entry. Another staff user's same id is its own request. An id of 15 characters, of 65, with a space, and one that is not a string: `invalid_request`, `fields.request_id` |
| `test_12_a_request_still_being_sent_and_one_that_was_refused` | With the cache entry present and empty: `request_in_progress`, 409, nothing sent. After a refused send the same id sends |
| `test_13_no_text_and_no_full_number_in_an_entry_or_a_log` | `json.dumps` of every entry's `detail`, `personal` and `target_id` holds neither the text nor the number in any form; `assertNoLogs("apps.inbox", "DEBUG")` |
| `test_14_the_send_and_its_inbox_row_are_one_transaction` | With `ConversationMessage.objects.create` patched to raise: no send record is left, no entry, and the request id may be used again |
| `test_15_the_masked_number_of_a_reply_and_a_composed_text_is_blanked_after_a_year` | One `sms_inbox_reply` entry and one `sms_inbox_compose` entry made by a real reply and a real compose, then `admin_audit_personal_values(now, dry_run=False)` run with `now` 366 days later (the rule of `apps/operators/retention.py`): both entries' `personal` is `{}` and `personal_blanked_at` is set; their `detail`, `target_id` and `action` are exactly as before. Run the day before (364 days): neither is touched. A dry run counts 2 and changes nothing. The class assertion of `test_access.py` alone does not prove this, so this test runs the command itself |

The refusals:

| The send function's reason | Status and code | Words |
|---|---|---|
| `paused` | 503 `sms_paused` | "Text sending is stopped. Nothing was sent." |
| `gateway_offline`, `gateway_off` | 503 `sms_unavailable` | "The gateway phone is offline. Nothing was sent." |
| `staff_limit` | 429 `limit_reached` | "Today's limit for staff texts is reached. 300 texts are kept for sign-in codes." |
| `rate_limited` (this module's own) | 429 `rate_limited` | "Too many texts in the last hour. Please wait and try again." |

- [ ] **Step 2:** run; fail (no such module).
- [ ] **Step 3: the code.** `apps/inbox/sending.py`:

```python
"""Replying and composing. Both go through the one send function, `send_sms`. A staff text
is queued, its inbox row written and its audit entry made in one database transaction.

No log line, cache entry or audit entry made here ever holds a full number or a text."""
import re
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.inbox import reading, storing
from apps.inbox.models import Conversation, ConversationMessage, InboxAccess
from apps.inbox.switch import SMS
from apps.notifications import services
from apps.operators import audit

HOURLY_LIMIT = 60  # texts one staff user may send in an hour
REQUEST_KEPT = 24 * 3600  # seconds a request id is remembered
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
OUT = ConversationMessage.Direction.OUT
Kind = ConversationMessage.Kind

_OFFLINE = (503, "sms_unavailable", "The gateway phone is offline. Nothing was sent.")
REFUSALS = {
    "paused": (503, "sms_paused", "Text sending is stopped. Nothing was sent."),
    "gateway_offline": _OFFLINE,
    "gateway_off": _OFFLINE,
    "staff_limit": (
        429, "limit_reached",
        f"Today's limit for staff texts is reached. {services.SMS_RESERVED_FOR_CODES} texts "
        "are kept for sign-in codes.",
    ),
    "rate_limited": (429, "rate_limited", "Too many texts in the last hour. Please wait and try again."),
}


@dataclass
class _Sent:
    reason: str = ""
    record: object = None  # the send record, when one was written
    message: object = None  # the inbox row, when the text was queued
    conversation: object = None
    started: bool = False  # a composed text made a new conversation


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _refused(reason):
    status, code, words = REFUSALS.get(reason, _OFFLINE)
    return ApiError(code, words, status=status)


def _checked(scope, text, request_id):
    if not scope.can_send:
        raise ApiError("forbidden", "You may read the inbox but not send texts.", status=403)
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"request_id": ["Send a request id of 16 to 64 letters and digits."]},
        )
    problem = services.sms_text_problem(text)
    if problem:
        raise ApiError("invalid_text", problem, fields={"text": [problem]})


def _answer(message):
    return {"conversation": message.conversation.public_id, "message": reading.shown_message(message)}


def _once(scope, request_id, send):
    """Run `send` unless this staff user's request id was used already. Answers (the text as
    shown, whether this call sent it)."""
    key = f"inbox:request:{scope.actor.pk}:{request_id}"
    if not cache.add(key, "", REQUEST_KEPT):
        earlier = cache.get(key)
        message = earlier and ConversationMessage.objects.filter(
            conversation__in=scope.conversations, public_id=earlier
        ).first()
        if not message:
            raise ApiError("request_in_progress", "That text is already being sent.", status=409)
        return _answer(message), False
    try:
        message = send()
    except BaseException:
        cache.delete(key)  # refused or failed: the same id may be tried again
        raise
    cache.set(key, message.public_id, REQUEST_KEPT)
    return _answer(message), True


def _lock(scope):
    """One send at a time per staff user, so the hourly count cannot be passed by two calls."""
    if not InboxAccess.objects.select_for_update().filter(operator=scope.actor, platform=SMS).exists():
        raise _not_found()


def _queue(scope, conversation, number, text, kind, purpose):
    """Queue the text and write its inbox row. Inside the caller's transaction."""
    actor, now = scope.actor, timezone.now()
    written = ConversationMessage.objects.filter(
        author=actor, direction=OUT, created_at__gt=now - timedelta(hours=1)
    )
    if written.count() >= HOURLY_LIMIT:
        return _Sent(reason="rate_limited", conversation=conversation)
    result = services.send_sms(
        to=number, purpose=purpose, text=text, ttl=services.SMS_STAFF_TEXT_LIFETIME
    )
    if result.outcome != services.SMS_QUEUED:
        return _Sent(reason=result.reason, record=result.message, conversation=conversation)
    started = False
    if conversation is None:
        line = services.sms_line()
        conversation, started = Conversation.objects.get_or_create(
            platform=SMS, line=line, contact=number, **storing.owner_of(line),
            defaults={"can_reply": True, "last_message_at": now},
        )
    message = ConversationMessage.objects.create(
        conversation=conversation, platform=SMS, direction=OUT, body=text,
        gateway_ref=result.message.public_id, author=actor, kind=kind,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=now)
    return _Sent(record=result.message, message=message, conversation=conversation, started=started)


def _detail(scope, sent, text):
    detail = {
        "platform": scope.platform, "outcome": "refused" if sent.reason else "queued",
        "length": len(text),
    }
    if sent.reason:
        detail["reason"] = sent.reason
    if sent.message:
        detail["message"] = sent.message.public_id
    if sent.record:
        detail["send_record"] = sent.record.public_id
    return detail


def reply(scope, public_id, text, request_id):
    """Send `text` to the other party of one conversation in the scope."""
    _checked(scope, text, request_id)
    if not is_public_id(public_id, "conv"):
        raise _not_found()

    def send():
        with transaction.atomic():
            _lock(scope)
            conversation = scope.conversations.select_for_update().filter(public_id=public_id).first()
            if conversation is None:
                raise _not_found()
            if not conversation.can_reply:
                raise ApiError("cannot_reply", "This sender cannot be replied to.")
            sent = _queue(scope, conversation, conversation.contact, text, Kind.REPLY, "inbox_reply")
            audit.record(
                scope.actor, "sms_inbox_reply", target_type=reading.TARGET,
                target_id=conversation.public_id, detail=_detail(scope, sent, text),
                personal={"contact": mask_mobile(conversation.contact)},
            )
        if sent.reason:
            raise _refused(sent.reason)
        return sent.message

    return _once(scope, request_id, send)


def compose(scope, to, text, request_id):
    """Send `text` to one Philippine mobile, in its conversation or a new one."""
    _checked(scope, text, request_id)
    try:
        number = normalise_ph_mobile(to if isinstance(to, str) else "")
    except ValueError:
        words = "Enter a valid Philippine mobile number."
        raise ApiError("invalid_mobile", words, fields={"to": [words]}) from None

    def send():
        with transaction.atomic():
            _lock(scope)
            sent = _queue(scope, None, number, text, Kind.COMPOSE, "inbox_compose")
            detail = _detail(scope, sent, text)
            detail["new_conversation"] = sent.started
            audit.record(
                scope.actor, "sms_inbox_compose", target_type=reading.TARGET,
                target_id=sent.conversation.public_id if sent.conversation else "",
                detail=detail, personal={"contact": mask_mobile(number)},
            )
        if sent.reason:
            raise _refused(sent.reason)
        return sent.message

    return _once(scope, request_id, send)
```

Two things the builder checks while building, and reports:

- `send_sms` takes its day's slot and holds the text in the cache before the inbox row is written. If the row then fails, the transaction undoes the send record, so the text is never handed to the phone, but the cache count stays one too high until it is next started from the records. `test_14` shows the first half; the second is accepted and reported.
- The audit test of `apps/operators/tests/test_audit.py` reads the second argument of every call named `record` as an action name and requires a literal. That is why `reply` and `compose` each make their own call.

- [ ] **Step 4:** run `apps.inbox apps.operators`; pass.
- [ ] **Step 5:** commit: `feat(inbox): reply and compose through the send function, audited [z8uq9me128]`.

### Task 3: paging, the unread filter and the status of an outgoing text

**Files:** `apps/inbox/reading.py`, `apps/inbox/tests/test_reading.py`.

- `conversations(scope, *, state="all", cursor="", limit=None)` answers `(rows, next)`. `state` is `all` or `unread` (a conversation with `unread_count` above 0); anything else raises `ApiError("invalid_request", …, fields={"state": […]})`. Order: `-last_message_at`, `-pk`. `cursor` is the id of the last conversation already shown; an id that is not in the scope raises `invalid_cursor`. The next page is the conversations whose `(last_message_at, pk)` is below the cursor's. `limit` as `apps.common.pagination`: 20 by default, 100 at most.
- A row gains `"platform"` and `"last_status"`: the send record's status when the last text went out, else null. It still holds no text.
- `shown_message(message, records=None)`, new and public: `{"id", "direction", "body", "clipped", "kind", "status", "reason", "author", "at"}`. `kind` is null for an incoming text. `author` is the staff user's username, null for an incoming text. `status` and `reason` come from the send record whose `public_id` is the message's `gateway_ref`, both null for an incoming text or when the record is gone.
- `open_conversation(scope, public_id, *, cursor="", limit=None)` answers the newest 50 texts (100 at most) oldest to newest, with `"next"`: the id of the oldest text shown when older ones exist, else null. `cursor` is such an id; the page is the texts of that conversation before it. Each call clears the unread count and writes one `sms_inbox_open` entry with the number of texts in that page. The send records of a page are read in one query.
- `find` is unchanged.

Tests, added to `ReadingTests`; the existing cases change only where they unpack the new answer:

| Test | Asserts |
|---|---|
| `test_10_the_list_is_paged_newest_first` | 25 conversations: 20, then 5 with the cursor, `next` null at the end; a limit of 0 gives 1 and of 500 gives 100; a cursor that is not an id, an unknown id and a `site` conversation's id raise `invalid_cursor` |
| `test_11_the_unread_filter` | Only conversations with an unread text; an opened one is gone from it; a state that is not `all` or `unread` is refused |
| `test_12_an_outgoing_text_shows_its_send_records_status` | After a reply: `status` `queued`, then `sent` once the record says so, then `failed` with its reason; null when the record is deleted. `last_status` in the list follows it and is null when the last text came in |
| `test_13_a_conversations_texts_are_paged_from_the_newest` | 120 texts: the newest 50 oldest to newest with `next`; the next page the 50 before; then 20 and `next` null. Three entries, with 50, 50 and 20 |
| `test_14_the_answers_hold_no_full_number` | `json.dumps` of the list, the find answer and each page of an opened conversation, after a reply and a composed text, holds the number in no form |

- [ ] Run; fail; code; pass. Commit: `feat(inbox): the list and a conversation are paged; an outgoing text shows its status [z8uq9me128]`.

### Task 4: the staff routes

**Files:** new `apps/inbox/staff.py`, `apps/inbox/tests/test_staff.py`; `apps/operators/staff_views.py` and `apps/billing/tests/test_staff_finance.py` exactly as section 3.1.

| Route under `/staff/v1/` | Body | Answers |
|---|---|---|
| `inbox/status/` | `{"platform"}` | `{"platform", "read": true, "send", "phone_online", "used_today", "cap", "reserved_for_codes", "unread"}`. `unread` is how many conversations in the scope hold an unread text |
| `inbox/conversations/list/` | `{"platform", "state", "cursor", "limit"}`, the last three optional | `{"results": [row], "next"}`. No text. Writes no entry |
| `inbox/conversations/find/` | `{"platform", "number"}` | `{"results": [row], "next": null}`. Writes `sms_inbox_find` |
| `inbox/conversations/open/` | `{"platform", "conversation", "cursor", "limit"}`, the last two optional | `{"id", "platform", "contact", "can_reply", "messages": [message], "next"}`. Writes `sms_inbox_open`, clears the unread count |
| `inbox/conversations/reply/` | `{"platform", "conversation", "text", "request_id"}` | 201 with `{"conversation", "message"}`; 200 with the same when the request id was already sent; or an error |
| `inbox/compose/` | `{"platform", "to", "text", "request_id"}` | The same |

Rules of every route, each a test:

- The origin, the method and the session are checked by `staff_views` before the handler runs. No session answers 401; a session that passed only the first step answers 401.
- While `SMS_INBOX_ENABLED` is off: `not_found`, the body is not read, and no query is made.
- `platform` missing, not `sms`, or not a string: `not_found`, and no inbox table is read.
- A staff user with no `InboxAccess` row, a superuser without one included: `not_found`.
- Every answer the handler makes, an error included, carries `Cache-Control: no-store`.
- A body over 2,048 bytes or one that is not a JSON object is read as empty, as on the Finance routes, so it answers `not_found` (no platform).

`apps/inbox/staff.py`:

```python
"""The inbox routes: what the Inbox pages of the HQ staff app call.

They are in no URL list. `apps.operators.staff_views` registers `ROUTES` under /staff/v1/
and calls each handler with the staff user of a session that passed both steps; the allowed
Origin, the method and the session are checked there and nowhere else. A handler answers as
if it did not exist while SMS_INBOX_ENABLED is off, for any platform but SMS, and for a
staff user without inbox access; it reads no inbox table in the first two cases.

Every route is a POST with a small JSON body, the ones that only read too: no number, text
or id is ever in an address. Every read and send starts from `scope_for`.
"""
import json

from django.http import JsonResponse

from apps.common.errors import ApiError, error_body
from apps.inbox import reading, sending
from apps.inbox.access import scope_for
from apps.inbox.switch import inbox_on
from apps.notifications import services

MAX_BODY = 2048


def _body(request):
    """The JSON object sent, or {} when there is none, it is too large or it is not one."""
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return {}
        data = json.loads(request.body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _route(view):
    def handler(request, operator):
        try:
            if not inbox_on():
                raise _not_found()
            body = _body(request)
            scope = scope_for(operator, body.get("platform"))
            if scope is None:
                raise _not_found()
            response = view(scope, body)
        except ApiError as refusal:
            response = JsonResponse(
                error_body(refusal.code, refusal.message, refusal.fields), status=refusal.status
            )
        response["Cache-Control"] = "no-store"
        return response
    return handler


def _limit(body):
    limit = body.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise ApiError(
            "invalid_request", "Some values are missing or invalid.",
            fields={"limit": ["Enter a whole number."]},
        )
    return limit


def _cursor(body):
    cursor = body.get("cursor") or ""
    if not isinstance(cursor, str):
        raise ApiError("invalid_cursor", "That page marker is not valid.")
    return cursor


@_route
def status(scope, body):
    unread = scope.conversations.filter(unread_count__gt=0).count()
    return JsonResponse({
        "platform": scope.platform, "read": True, "send": scope.can_send,
        **services.sms_status(), "unread": unread,
    })


@_route
def listing(scope, body):
    rows, following = reading.conversations(
        scope, state=body.get("state") or "all", cursor=_cursor(body), limit=_limit(body)
    )
    return JsonResponse({"results": rows, "next": following})


@_route
def find(scope, body):
    return JsonResponse({"results": reading.find(scope, body.get("number")), "next": None})


@_route
def open_one(scope, body):
    opened = reading.open_conversation(
        scope, body.get("conversation"), cursor=_cursor(body), limit=_limit(body)
    )
    if opened is None:
        raise _not_found()
    return JsonResponse(opened)


def _sent(answer, created):
    return JsonResponse(answer, status=201 if created else 200)


@_route
def reply(scope, body):
    return _sent(*sending.reply(scope, body.get("conversation"), body.get("text"), body.get("request_id")))


@_route
def compose(scope, body):
    return _sent(*sending.compose(scope, body.get("to"), body.get("text"), body.get("request_id")))


ROUTES = {
    "inbox/status/": status,
    "inbox/conversations/list/": listing,
    "inbox/conversations/find/": find,
    "inbox/conversations/open/": open_one,
    "inbox/conversations/reply/": reply,
    "inbox/compose/": compose,
}
```

`open_conversation` puts `"platform"` in its answer. A `state` that is not a string reaches `reading.conversations`, which refuses it.

Tests, class `InboxRouteTests`, on `StaffTestCase` (`apps/operators/tests/test_staff_routes.py`) with `HQ_STAFF_ORIGIN` and `SMS_INBOX_ENABLED` set and a staff session that passed both steps, made the way `FinanceTestCase` makes one:

| Test | Asserts |
|---|---|
| `test_1_the_routes_are_registered_and_none_takes_another_routes_place` | `staff.ROUTES` holds exactly the six paths; each starts with `inbox/`; each is `POST` in `staff_views.ROUTES`; none is a sign-in or a Finance path; `staff_views.ROUTES` has as many entries as the sign-in routes, the Finance routes and these six together |
| `test_2_the_module_imports_nothing_from_the_staff_routes` | As the Finance test of the same name: no import of `apps.operators.staff_views` in `apps/inbox/staff.py` |
| `test_3_every_route_is_closed_while_the_inbox_is_off` | `SMS_INBOX_ENABLED` off: each of the six answers 404 `not_found` with `assertNumQueries` showing only the session's own queries and no `inbox_` table; `json.loads` is never called on the body |
| `test_4_no_session_and_a_first_step_session_get_401` | On all six |
| `test_5_no_access_row_is_not_found` | A staff user without a row and a superuser without a row: 404 on all six, no data |
| `test_6_any_other_platform_is_not_found` | `messenger`, `instagram`, `tiktok`, `email`, `webchat`, `fax`, a missing platform, a number and a list: 404 on all six, and no query names an `inbox_conversation` table |
| `test_7_another_origin_is_refused` | The dashboard's origin, the public site's origin, a preview address and no `Origin`: 403 `origin_not_allowed` on all six |
| `test_8_every_answer_says_no_store` | A 200, a 201, a 400, a 403, a 404 and a 429 made by a handler each carry `Cache-Control: no-store` |
| `test_9_status` | The keys of the table; `send` follows the row; `unread` counts conversations; with the gateway off the phone is offline and the counts are null |
| `test_10_list_find_and_open` | The shapes of the table; the list holds no text; open writes the entry and clears the count; an id outside the scope and a `site` conversation's id are 404 |
| `test_11_reply_and_compose` | 201 and the shape; the same request id again answers 200 and sends nothing; a read-only user gets 403; each refusal of task 2 arrives with its status, code and words |
| `test_12_a_get_is_not_allowed_and_no_route_has_a_value_in_its_address` | `GET` on each answers 405; no path in `staff.ROUTES` holds anything but letters, `-` and `/` |
| `test_13_no_answer_holds_a_full_number` | Every answer of tests 9 to 11, as text, holds the number in no form |

Reported with the build, not changed by it: the answers `staff_views` writes itself (401, the 403 for another origin, the 404 for an unknown path, 405) carry no `Cache-Control` today. They hold no data. Adding it there is one line in the shared file and is left to the coordinator to ask for.

- [ ] Run; fail; code; pass, with `apps.billing.tests.test_staff_finance` and `apps.operators`. Commit: `feat(inbox): six staff routes for the inbox, reply and compose [z8uq9me128]`.

### Task 5: `sms_inbox_delete` also deletes the send records of staff texts

**Files:** `apps/inbox/management/commands/sms_inbox_delete.py`, `apps/inbox/tests/test_delete.py`.

After this plan a number a staff member texted is in two places: the inbox rows (text, deleted by the command today) and the send records of those texts (full number, no text). The command now covers both.

- Only when the typed number is a Philippine mobile: a staff text can go to no other number.
- The send records are `services.staff_send_records(number)`: purposes `inbox_reply` and `inbox_compose` only. **A code's send record to the same number is not touched**: it is not part of the inbox, it holds no text, and it goes at 365 days by `sms_messages`.
- Without `--confirm` they are counted. With `--confirm` each one's waiting text is forgotten (`services.forget_sms`) and the record deleted, in the same transaction as the conversations. A text still waiting is therefore never sent.
- **If any of them has status `dispatched`, the command refuses before deleting anything:** `CommandError` "A text to that number is with the phone right now. Run again in 10 minutes.", nothing deleted, no audit entry. The phone reports a text within seconds, and the server ends an unreported one after 10 minutes; deleting the record first would make the phone's report fail.
- The output gains a third count: "Found: 1 conversation, 2 messages, 1 send record. Nothing was deleted. Run again with --confirm to delete them." and "Deleted: 1 conversation, 2 messages, 1 send record." "No conversation is stored for that number." is said only when all three counts are 0.
- The audit entry's `detail` gains `"send_records": N`. It still holds no number.

Tests: the output lines and `detail` of plan 3's tests 1, 2, 4, 5, 8, 10 and 12 gain the third count. Added:

| Test | Asserts |
|---|---|
| `test_14_the_send_records_of_staff_texts_go_with_the_conversation` | After a reply and a composed text to the number: counted without `--confirm`; with it the conversation, its texts and both send records are gone, and the cache holds neither text. A `mobile_verify` record to the same number and another number's staff send record stay |
| `test_15_a_text_with_the_phone_stops_the_command` | One send record `dispatched`: `CommandError` with those words, with and without `--confirm`; nothing deleted, no entry. Once it is `sent`, the command deletes |
| `test_16_a_waiting_text_is_never_sent_after_the_delete` | A reply queued, then the delete: the next `poll` hands out nothing |
| `test_17_a_number_that_is_not_a_philippine_mobile_reads_no_send_record` | Typed digits: `send_records` 0 and no query on the SMS message table |

- [ ] Run; fail; code; pass. Commit: `feat(inbox): sms_inbox_delete also removes the send records of staff texts [z8uq9me128]`.

### Task 6: the runbooks

**Files:** `docs/runbooks/sms-gateway.md`, `docs/runbooks/migrations.md`, `docs/runbooks/retention.md`.

- [ ] `sms-gateway.md`, a new section "Staff texts" after "Incoming texts: the three switches", saying:
  - A staff member with send access may reply in a conversation or write a new text to one Philippine mobile. Both go through the send function: one SMS of at most 160 plain characters, no web link, no marketing.
  - A staff text waits up to 1 hour for the phone, is sent after every waiting code, and leaves no sooner than 18 to 25 seconds after the last staff text.
  - 300 of the day's texts are kept for codes: a staff text is refused once the day's count reaches the limit less 300. The 300 is `RESERVED_FOR_CODES` in `apps/notifications/sms.py`; changing it is Coach Ed's ruling and a reviewed change.
  - One staff user may send 60 texts in an hour.
  - What is kept: the text, with who wrote it, in the inbox for 365 days; a send record with the full number and no text for 365 days; an audit entry with the masked number, blanked after a year (`admin_audit_personal_values`, see `retention.md`).
  - The six routes under `/staff/v1/inbox/`, each a `POST`, closed while `SMS_INBOX_ENABLED` is off and to a staff user without access.
  - There is still no screen: the pages are the next plan.
- [ ] In "Incoming texts: the three switches", the deletion bullet: the command also deletes the send records of staff texts to that number and drops one still waiting; it stops if a text to that number is with the phone, to be run again in 10 minutes; "what it does not remove" gains the send records of codes sent to that number (deleted at 365 days) and the masked number in the audit entries of replies and composed texts (blanked after a year).
- [ ] In "Release order" or beside it, one line: `notifications.0004_alter_smsmessage_category` changes the allowed values of a send record's category only; it is applied before `SMS_INBOX_ENABLED` is set.
- [ ] `migrations.md`, at the end of "History", exactly: "2026-11-11: `inbox.0001_initial` was applied to `staging` at commit `b6d37a4`. The plan read beforehand listed only that migration. Afterwards `migrate --plan` reported no pending operations and `/healthz/` returned ok."
- [ ] `retention.md`: in the `admin_audit_personal_values` row, after "`sms_inbox_open` entries (the masked number of the conversation that was opened)", add "and of the `sms_inbox_reply` and `sms_inbox_compose` entries (the masked number the text was sent to)"; in the sentence after the table, "the `sms_inbox_open` entries" becomes "the `sms_inbox_open`, `sms_inbox_reply` and `sms_inbox_compose` entries". Their `personal` is blanked 1 year after the entry; the rule needs no code change, because it reads every action declared `PERSONAL` (task 2).
- [ ] Commit: `docs(runbooks): staff texts, the inbox routes, retention of the reply and compose entries, and inbox.0001 on staging [z8uq9me128]`.

### Task 7: the whole check

- [ ] Run, and report the actual output of each:

```bash
uv run python manage.py test
uv run python manage.py makemigrations --check
uv run python manage.py check
uv run python manage.py check --deploy
```

- [ ] By hand, on a scratch SQLite database with made-up numbers and texts, a local server and an empty local Redis database, `SMS_GATEWAY_ENABLED=1`, `HQ_STAFF_ORIGIN` and `HQ_HOST` set to local names:
  1. `SMS_INBOX_ENABLED` unset: each of the six routes answers 404 with the staff origin and a signed-in staff session.
  2. `SMS_INBOX_ENABLED=1`, access granted with `--send`, `sms_fake_phone` running: a composed text answers 201, appears in the fake phone's terminal after the phone's next poll, and the conversation then shows it `sent`; the same request id again answers 200 and the fake phone prints nothing new; a reply the same way.
  3. With `notifications.0004` **not** applied (migrate `notifications` back to `0003` on the scratch database): step 2 still works. This is the proof that the code can be released before the migration is applied.
  4. `sms_inbox_delete` for the made-up number, without and with `--confirm`: three counts, and the number in neither output.
- [ ] How the staff session of step 1 is made locally (the sign-in codes) is reported with the result. If it cannot be made without a real email code, the builder says so and reports which steps were done through the test client instead.

## 6. What the pages of plan 5 need to know

- The six routes, their bodies and answers (task 4), all `POST`, with the staff origin and the HQ cookie.
- The Inbox menu item is shown when `inbox/status/` answers 200. "New SMS" and the reply box are shown when `send` is true.
- A send carries a `request_id` the page makes once per text typed (16 to 64 letters, digits, `-`, `_`) and repeats if it tries again.
- An outgoing text's `status`: `queued` and `dispatched` are "Waiting"; `sent` is "Sent"; `failed` and `expired` are "Not sent"; null is shown as "Not sent" with no reason.
- The errors: `invalid_text` (`fields.text`), `invalid_mobile` (`fields.to`), `cannot_reply`, `forbidden` (403), `rate_limited` and `limit_reached` (429), `sms_paused` and `sms_unavailable` (503), `request_in_progress` (409), `invalid_cursor`, `invalid_request`, `not_found` (404), `unauthenticated` and `second_step_required` (401). The words to show are in the answer's `message`.

## 7. The gate for this plan, and what still needs Coach Ed's approval

### 7.1 The gate

This plan does not return to Coach Ed ("build until plan 9"). Once the coordinator has read it and the NPC coordinator has reviewed it for privacy, the coordinator merges it and says when to build. That covers: building tasks 1 to 7 on a build branch, including writing `notifications.0004_alter_smsmessage_category`; a build PR reviewed by the NPC coordinator before merge; merged by the coordinator on green CI after the overlap check; that merge reaching staging with `SMS_INBOX_ENABLED` unset. The coordinator then runs the one migration on staging under the standing approval.

For the NPC coordinator in particular: C5 (which refused attempts are audited), C15 and task 5 (what a deletion request now removes, and what it leaves), and 3.3 (the text of a staff text is stored, with its author).

For the billing coordinator: section 3.1, the four lines in `staff_views.py` and the two assertions in its test file.

### 7.2 Not asked, each still its own approval

| Item | Whose, and when |
|---|---|
| `SMS_INBOX_ENABLED=1` on staging | Coach Ed, per environment. Never before `inbox.0001` and `notifications.0004` are applied there |
| `sms_inbox_access grant`, with or without `--send`, for a named person | Coach Ed names who, and whether they may send. **No staff text can be sent anywhere until this is done** |
| `sms_device incoming on` on any shared environment | Coach Ed, each time, and only after the NPC and privacy wording is amended |
| A staff text sent from a shared environment | Not covered by this plan. The wording amendment must cover that a staff text and its author are stored (design 14) |
| A new value for the 300 | Coach Ed's ruling, then a reviewed change |
| `sms_inbox_delete --confirm` on a shared environment | Whoever Coach Ed has answer a person's deletion request, per request |
| Anything on production, the migration included | Coach Ed |
| Plans 5 to 9 | Each its own plan-only PR, gated like this one |

## 8. Review, overlap and order

- **Open backend PRs when this plan was written** (`gh pr list`, 2026-11-11, `main` at `b6d37a4`): none.
- **Shared files:** `apps/operators/staff_views.py` and `apps/billing/tests/test_staff_finance.py` (section 3.1); `apps/operators/audit.py`, two added lines; `apps/notifications/sms.py`, `services.py`, `gateway.py` and `models.py`, which plan 8 (Bulk SMS) will also edit. If another PR touching any of them is open when the build PR is ready, the first ready merges first and the other updates from `main` and keeps both sides' lines. The builder lists the open PRs again before the build PR.
- **Order on an environment:** deploy dark; apply `notifications.0004`; then, each with its own approval, the variable, the access grant, and the phone's switch.

## 9. What was verified, and what was not

Verified by reading at `b6d37a4`: that the staff route table matches exact paths with one method and that `_for_staff` passes the staff user of a session that passed both steps; that a Finance test counts every staff route; that `send_sms` raises for an undeclared purpose, a bad text and a bad number, writes no record while the gateway is off, and answers `refused` with the record otherwise; that `refusal` takes the platform slot after the kill switch and gives it back when the phone is offline; that `_waiting` and `_hand_out` pace by the marketing class only; that `report` refuses a whole report when one of its texts has no record for that phone; that `audit.record` refuses an undeclared action and that the audit test requires a literal action name; that `inbox.0001_initial` depends on `notifications.0003`, so the SMS tables exist wherever the inbox tables do.

Not verified: none of the code in this plan has been run. That `makemigrations` writes exactly the one `AlterField`. How a staff session is made for the hand check. PostgreSQL behaviour of the row locks in `sending.py` (the tests run on SQLite, where `select_for_update` does nothing). Anything in a browser, on staging or on production.
