# SMS Inbox Storage Implementation Plan (plan 3 of 11, plan only)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend the place where an incoming text is kept: the inbox records, the `received` call that stores into them behind three switches, the 365-day retention rule, the one access check, the masked read functions with their audit entries, the terminal command that grants access, and the terminal command that deletes the conversations of one number. Every switch stays off.

**Architecture:** A new app, `apps/inbox`, owns three tables. `apps/notifications/gateway.py` keeps the shape of `received` and hands the body to one storing function in `apps/inbox`. Nothing stores into or shows from an inbox table unless `SMS_INBOX_ENABLED` is on; the only code that touches one while it is off deletes (the retention rule and the deletion command, section 3.2). No route, page or Django admin screen is added: the staff routes are plan 4.

**Tech Stack:** Django 5.2, Python, `uv`, Django's test runner. No new dependency. One migration, `inbox.0001_initial`.

---

Date: 2026-11-11. ClickUp task: z8uq9me128 (https://app.clickup.com/t/z8uq9me128), parent z8uq9md33v.
Plan branch: `claude/z8uq9me128-inbox-storage-plan`, cut from `origin/main` at `bef2f89`. The build branch will be cut from the latest `origin/main` when this plan is approved.
Status: **plan only; nothing in it is built.** Under Coach Ed's ruling "build until plan 9" (ClickUp comment 1100430000490204, read by this session on 2026-11-11), plans 3 to 9 do not return to him one by one. The gate for this plan is the coordinator's read and the NPC coordinator's review; the coordinator then merges it and says when to build. The code in it was written from reading the repository at `bef2f89` and has not been run.

Revised 2026-11-11 after the NPC coordinator's privacy review at `8fa9085` and the coordinator's read (ClickUp comment 1100430000490313): five changes, listed in section 3.3 with where each one lands.

## 1. Sources

| Short name | Source |
|---|---|
| Task | ClickUp z8uq9me128: description and every comment, read 2026-11-11 |
| Build order | The coordinator's comment 1100430000488202 on the Task: this is item 3, "inbox storage (records, received path behind its switches, retention, masking, audit, grant command). Migration. Switches stay off." |
| Design | `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md` on `main`, sections 2, 4, 5, 7, 8, 9, 11, 12, 14, 15 and 16 |
| Phone app | `NegosyoOnline/negosyoonline-sms-gateway` at `main` `b69aec3`, read only: `core/IncomingForwarder.kt`, `core/Inbox.kt`, `core/HttpGateway.kt`, `core/Gateway.kt`, `IncomingReceiver.kt`, `GatewayService.kt` (section 3.1) |
| Review | The coordinator's comment 1100430000490313 on the Task: the five changes of section 3.3 |
| Code | `apps/notifications/gateway.py`, `models.py`, `retention.py`, `apps.py`, `sms.py` (`gateway_on`), `management/commands/sms_device.py`, `tests/base.py`, `tests/test_sms_gateway.py` (`ReceivedTests`), `tests/test_sms_commands.py`; `apps/common/retention.py`, `masking.py`, `phones.py`, `ids.py`; `apps/operators/audit.py`, `retention.py`, `models.py`; `config/settings.py`; `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md`; all at `bef2f89` |

### 1.1 Rulings this plan applies, in Coach Ed's words

- **R0**: "what we will be building is a separate HQ tool, but make it ready because soon, it will be part of the add-on". Records carry an owner; one access check; every conversation in this build is owned by `hq`.
- **R1**: "Yes, amend first". Incoming texts may be stored only after the NPC and privacy wording is amended.
- **R2**: "Yes, 365 days".
- **R3**: "Yes, as proposed". Only HQ staff with an explicit SMS inbox permission; every opened conversation is written to the operator audit record.
- **R8**: "Yes, this is for SMS only." Records name their platform; only `sms` exists.
- **R27, R28** and his words of 2026-11-11, "there will be no Django admin and we will be building the HQ for the common cases in managing Negosyo Online": **this plan adds no Django admin screen, list or registration for any inbox record**, and task 7 has a test that holds it.
- "build until plan 9", after "or lest do it all in plans 9" and "is it possible you build upto 9?", as relayed by the coordinator and recorded by it in comment 1100430000490204. The coordinator's reading: plans 3 to 9 of the build order are written and built one after another without each plan returning to him; he comments on the look at the visual test. Each plan is still written first and gated by the coordinator's read and the reviewing coordinator. It turns no switch on and approves nothing in section 6.2.

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | `SMS_INBOX_ENABLED` read in `config/settings.py`; `apps.inbox` in `INSTALLED_APPS` | Design 5.1, 12 |
| 2 | `Conversation`, `ConversationMessage`, `InboxAccess` and the migration `inbox.0001_initial` | Design 4, 12 |
| 3 | The storing function, and `received` using it behind the three switches | Design 5.2 |
| 4 | `sms_device incoming on` refuses while `SMS_INBOX_ENABLED` is off; its closing line changes | Design 5.1 |
| 5 | `scope_for(actor, platform)`, the one access check | Design 7.2 |
| 6 | `manage.py sms_inbox_access grant|revoke|list`, audited | Design 7.1, 8 |
| 7 | The masked read functions (list, open, find by full number) and the audit entries `sms_inbox_open` and `sms_inbox_find` | Design 8, 9; review change 3 |
| 8 | The retention rule `inbox_messages`, in the daily rules, running wherever the tables exist | Design 11; review change 1 |
| 9 | `manage.py sms_inbox_delete`, audited: deletes the conversations of one full number | Design 14 point 9; review change 4 |
| 10 | The two runbooks | Design 12 |

### 2.2 What is not built here

- **Any route.** The staff API of design section 10 is plan 4. The read functions of task 7 are called only by tests until then.
- Reply and compose, the purposes `inbox_reply` and `inbox_compose`, the `inbox` category and its migration in `apps/notifications`, the 300 reserve, the audit actions `sms_inbox_reply` and `sms_inbox_compose`: plan 4.
- Any page: plan 5, in the frontend repository.
- Contacts, groups, Bulk SMS, the do-not-text list: plans 6 to 9.
- **Any Django admin screen** (1.1).
- Any Railway variable, any run of the migration, any switch turned on, anything on staging or production (section 6).

### 2.3 Files

| File | Change |
|---|---|
| `config/settings.py` | Two added lines of code and a comment. Announced to the NPC coordinator before the build (design 22.5) |
| `apps/inbox/__init__.py`, `apps.py`, `models.py`, `switch.py`, `storing.py`, `access.py`, `reading.py`, `retention.py` | New |
| `apps/inbox/migrations/__init__.py`, `0001_initial.py` | New |
| `apps/inbox/management/__init__.py`, `management/commands/__init__.py`, `management/commands/sms_inbox_access.py`, `management/commands/sms_inbox_delete.py` | New |
| `apps/inbox/tests/__init__.py`, `base.py`, `test_models.py`, `test_storing.py`, `test_access.py`, `test_reading.py`, `test_retention.py`, `test_delete.py` | New |
| `apps/notifications/gateway.py` | `store_incoming` and `received` replaced; the `poll` answer's `incoming`; the docstring |
| `apps/notifications/retention.py` | `DAILY_RULES` gains one name; two docstrings |
| `apps/notifications/management/commands/sms_device.py` | `_incoming` |
| `apps/notifications/tests/test_sms_commands.py`, `test_sms_gateway.py` | Changed and added cases |
| `apps/operators/audit.py` | Four added lines in `ACTIONS`: `sms_inbox_access`, `sms_inbox_open`, `sms_inbox_find`, `sms_inbox_delete` |
| `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md` | Rewritten section; one added row |

No file under `apps/operators/staff_views.py`, `apps/billing` or the frontend repository.

## 3. Choices this plan makes, each reported and none hidden

Each is a reversible detail inside the design, chosen as the smallest that holds. The coordinator accepted C1 to C12 at `8fa9085`, with C4, C5 and C9 changed by the review (they are rewritten below). C13 to C17 are new in this revision and the coordinator may overrule any of them.

| # | Choice | Why, and what the design says |
|---|---|---|
| C1 | "A message's platform equals its conversation's" is held by a check constraint on each table that allows only `sms`, plus the one storing function | Design 4.3 asks for "a database constraint and the one storing function". Django cannot write a constraint across two tables. While `sms` is the only value, the two checks make the values equal. The task that adds a second platform must replace them |
| C2 | Uniqueness of a conversation is three conditional constraints, one per owner kind | "One conversation per owner, platform, line and contact" (4.2). `site` and `account` are empty for `hq`, and PostgreSQL treats empty values as different from each other, so one plain constraint would allow duplicates |
| C3 | `site`, `account`, `line`, `device`, `author`, `operator` and `granted_by` are protected from deletion (`PROTECT`); a message is deleted with its conversation (`CASCADE`) | No row of this build has a Site or an account. What happens to a Site's conversations when the Site goes is the add-on's decision, and `PROTECT` makes it impossible to decide by accident |
| C4 | **Changed by the review.** One bad item no longer refuses the list. A bad item is skipped and counted; the good items are stored; the answer is 204. A bad item is one that is not an object, or whose `id`, `from` or `text` is not a string, or whose `id` or `from` is empty, or whose `id` is longer than 100 characters. The list itself is refused with 400, and nothing stored, only when the body is not an object holding a list of at most 20 items | Section 3.1: on a 400 the phone keeps the whole list and sends the same one again, so one bad text would hold back every later text for up to 7 days. **This differs from design 5.2 and 15** ("a bad list stores nothing and answers 400"), by the review. **What it costs:** a skipped item is answered 204 with the others, so the phone drops it and it never reaches the inbox; it stays only in the phone's own Messages app |
| C5 | **Changed by the review.** NUL characters are removed from `id`, `from` and `text` before anything else is done with them. `gateway_ref` holds 100 characters | PostgreSQL refuses a NUL character in text; the call would fail with a 500, which the phone treats like a 400 (3.1). The id the phone app sends is `in_` and 32 hexadecimal characters, 35 in all (read at `b69aec3`), so 100 is enough |
| C6 | `poll` answers `incoming: true` only when the phone's switch is on **and** `SMS_INBOX_ENABLED` is on | Design 5.1 changes the command "so the phone is never told to forward texts that would be thrown away". Unsetting the variable later while the phone's switch is on would do exactly that. This closes it in one line. Not in the design's words |
| C7 | `sms_inbox_access` refuses every action while `SMS_INBOX_ENABLED` is off | "While off: the inbox tables are never read or written" (5.1). The design's order sets the variable before granting (12) |
| C8 | Opening a conversation sets `unread_count` to 0 for everyone | Design 4.2: "since the conversation was last opened by anyone with access" |
| C9 | **Changed by the review.** `find` (a full number) returns list rows only and writes one audit entry, `sms_inbox_find`, kept whole: `detail` the platform and the number of matches, nothing personal, no number in any form | A lookup by full number confirms that a person wrote to us, so who looked is recorded. An entry is written for every lookup of a full Philippine mobile number, a lookup with no match included. Something typed that is not a full number looks nothing up and writes no entry |
| C10 | A sender kept "as received" is trimmed of surrounding spaces and cut to 32 characters | Design 4.2: "at most 32 characters" |
| C11 | `sim_slot` is kept when it is a whole number from 0 to 9, otherwise left empty; `received_at` that cannot be read as a time with a zone becomes the server's time | Design 4.3 gives the second; the first is the same rule for the other optional field |
| C12 | The read functions do not report an outgoing text's status | No outgoing text exists until plan 4, which reads the status from the send record (design 4.3) |
| C13 | **New (review change 2).** A sender with 7 or more digits that is not a Philippine mobile (a foreign mobile, a landline) is shown as `***` and its last 4 characters when it has 10 or more characters, otherwise as `***` alone. A sender with fewer than 7 digits (a name, a short code) is shown as received | `mask_mobile` shows the first 6 and the last 4 characters, which hides only 3 digits of a Philippine mobile and **nothing at all** of a 10-character landline. The new form always hides at least 6 characters. It is a function of `apps/inbox/reading.py`; `apps/common/masking.py` is not edited |
| C14 | **New (review change 1).** The retention rule and the deletion command decide by whether the inbox table exists (one read of the database's table names), not by `SMS_INBOX_ENABLED` | Section 3.2 |
| C15 | **New (review change 4).** `sms_inbox_delete` reads the number from standard input, never from an argument | An argument would be kept in the shell's history and shown in the process list of the machine it runs on |
| C16 | **New (review change 4).** `sms_inbox_delete` only counts unless `--confirm` is given; each run, counting or deleting, writes one `sms_inbox_delete` audit entry with the counts and no number | A deletion cannot be undone and a mistyped number can be another person's. The count is the check before it. A count also confirms that a person wrote to us, so it is recorded like a `find` |
| C17 | **New (review change 4).** `sms_inbox_delete` deletes conversations owned by `hq` only, on every phone line; it matches a Philippine mobile in any form `normalise_ph_mobile` accepts, and any other number (a foreign mobile, a landline) **by its digits**: the digits typed equal the digits of the stored sender, everything that is not a digit dropped from both | Every conversation of this build is owned by `hq` (R0). A Site's conversations will be the Subscriber's to delete (the add-on's decision). Staff only ever see such a number masked, and the person asking writes it their own way, so nobody can know the spaces and punctuation it was stored with (the coordinator's change A after its read). The comparison is done in Python over the `hq` conversations that cannot be replied to, which is small at launch volume. **Limit:** the same number written with a different prefix (`+1 415…`, `001 415…`, `415…`) has different digits and is not matched; the operator tries the forms the person gives |

### 3.1 What the phone app does with each answer from `received`

Read in `NegosyoOnline/negosyoonline-sms-gateway` at `main` `b69aec3`, read only, before change 5 was written. Not run.

- **The call.** `IncomingForwarder.forward()` takes the texts the phone holds, **oldest first, at most 20**, and sends them as one `received` call. It does so after each poll, and only while the last poll answered `incoming: true`.
- **On 200 or 204** (`Answer.Ok`): it drops every text of that list from the phone (`inbox.taken`) and sends the next 20. It does not read the body. So whatever the server answers 204 to is gone from the phone's held list, stored or not.
- **On 400** (`Failure.REJECTED`), and the same on 401, 404, 429, any 3xx, any 5xx and no network: it stops, keeps the list, and tries again after the next poll. Because it always takes the oldest 20, **it sends the same list again every time**. One text the server refuses therefore holds back that list and every text behind it, until the held texts are dropped at 7 days (`Inbox.KEEP_DAYS`), or pushed out when the phone holds more than 500 (`Inbox.MOST`, oldest dropped first).
- **So change 5 is the right choice**, and the wait it removes is wider than the review said: it is not only the 20 of that list, it is everything received after them. The same reasoning makes a 500 as harmful as a 400, which is why NUL characters are removed (C5) instead of left to fail in the database.
- **The id:** `in_` followed by a random UUID without hyphens, 35 characters, made when the text arrives; a repeat to the server uses the same id.
- **`from`:** Android's originating address, **or an empty string when Android gives none** (`parts[0].originatingAddress ?: ""`). Under C4 a text with an empty `from` is a bad item: skipped, counted, and lost to the inbox. **Reported to the coordinator** as a thing the design does not name: the alternative is to store it under a fixed word such as `Unknown`, which this plan does not invent.
- **`text`:** always a string; the parts of a long text joined. It can be empty.
- **`received_at`:** `Instant.toString()`, for example `2026-11-11T02:03:04.567Z`. Python 3.12's `datetime.fromisoformat` reads that form (the repository requires 3.12).
- **`sim_slot`:** a whole number, 0 when Android gives none.
- **When a poll answers `incoming: false`,** the phone deletes at once every text it still holds (`IncomingForwarder.switch`). So turning the phone's switch off, or unsetting `SMS_INBOX_ENABLED` (C6), discards whatever the phone had not yet handed over.

### 3.2 `SMS_INBOX_ENABLED` off: what still touches an inbox table

Design 5.1 says: "While off: the inbox tables are never read or written." The review changes that in one direction only, and this plan holds it to two things, **both of which only delete**:

1. **The retention rule** (task 8). Texts stored while the switch was on must not outlive 365 days because the switch was later unset (R2).
2. **The deletion command** (task 9). A person's request to delete their texts must not wait for a switch.

Both first check that the table exists (`switch.tables_exist()`, one read of the table names), because the code is released before the migration is applied; with no table they do nothing.

Everything else stays behind the switch as designed: `received` stores nothing, `poll` tells the phone not to forward, `scope_for` answers no scope so nothing can be listed, opened or found, and `sms_inbox_access` refuses every action (C7). **How change 4 was resolved against C7:** C7 is about giving access, and it stands. Deleting is not access: the command shows counts and no number or text, and refusing it while the switch is off would block a deletion request for texts that change 1 now deliberately leaves in the table. So `sms_inbox_delete` works with the switch on or off.

### 3.3 The five review changes, and where each lands

| # | Change | Where |
|---|---|---|
| 1 | Retention does not stop when the switch is off | 3.2, C14; 4.2; task 1 (`switch.tables_exist`, the settings comment); task 8 (code, `test_5`, `test_5b`, `test_5c`); task 10 (both runbooks); task 11 (the hand check) |
| 2 | Every sender with 7 or more digits is masked | C13; task 7 (`shown_contact`, `test_2`, `test_2b`, `test_4b`) |
| 3 | `find` writes `sms_inbox_find` | C9; task 6 (`ACTIONS`); task 7 (`find`, `test_8`, `test_8b`) |
| 4 | The audited deletion command | C15 to C17; 3.2; new task 9; task 10 (runbook); 6.2 |
| 5 | One bad item does not refuse the list; NUL characters removed | C4, C5; 3.1; task 3 (`_clean`, `store`, `test_10` to `test_11b`, `test_16`) |

## 4. The records and the migration

### 4.1 Tables

All in `apps/inbox/models.py`. Field names are the design's.

**`Conversation`**

| Field | Type | Notes |
|---|---|---|
| `public_id` | text, 40, unique | `conv_…` from `new_public_id("conv")` |
| `owner_kind` | text, 8 | `hq`, `site`, `account` |
| `site` | key to `sites.Site`, empty allowed, `PROTECT` | |
| `account` | key to `accounts.Account`, empty allowed, `PROTECT` | |
| `platform` | text, 16 | `sms` |
| `line` | key to `notifications.SmsDevice`, `PROTECT` | |
| `contact` | text, 32 | |
| `can_reply` | yes/no | |
| `unread_count` | whole number, 0 or more, default 0 | |
| `last_message_at` | time | |
| `created_at` | time, set on creation | |

Constraints: `inbox_conversation_owner` (hq: both empty; site: site set, account empty; account: account set, site empty); `inbox_conversation_platform` (`sms` only, C1); `inbox_conversation_one_per_hq_contact`, `…_site_contact`, `…_account_contact` (C2). Index on `-last_message_at`.

**`ConversationMessage`**

| Field | Type | Notes |
|---|---|---|
| `public_id` | text, 40, unique | `msg_…` |
| `conversation` | key to `Conversation`, `CASCADE` | |
| `platform` | text, 16 | `sms` |
| `direction` | text, 3 | `in`, `out` |
| `body` | text | At most 2,000 characters, cut by the storing function |
| `clipped` | yes/no, default no | |
| `gateway_ref` | text, 100, may be blank | |
| `device` | key to `SmsDevice`, empty allowed, `PROTECT` | |
| `sim_slot` | small whole number, empty allowed | |
| `author` | key to the staff user model, empty allowed, `PROTECT` | |
| `kind` | text, 8, may be blank | `reply`, `compose` (plan 4) |
| `occurred_at` | time, empty allowed | |
| `created_at` | time, set on creation, indexed | Retention counts from here |

Constraints: `inbox_message_platform` (`sms` only); `inbox_message_once_per_phone`: unique `device` and `gateway_ref` where `direction` is `in`.

**`InboxAccess`**

| Field | Type | Notes |
|---|---|---|
| `operator` | key to the staff user model, `PROTECT` | |
| `platform` | text, 16 | `sms` |
| `can_send` | yes/no, default no | |
| `granted_by` | key to the staff user model, `PROTECT` | |
| `granted_at` | time, set on creation | |

Constraint: `inbox_access_one_per_operator_platform`.

### 4.2 The migration

`apps/inbox/migrations/0001_initial.py`: three `CreateModel` operations and the constraints above, nothing else. No data, no change to any existing table. It depends on the latest migrations of `notifications`, `sites`, `accounts` and `operators` at the time the build branch is cut. The builder writes it with `makemigrations inbox` and checks the file holds exactly that (task 2).

Writing the file is covered once this plan is approved (Coach Ed's standing approval of 2026-11-11). **Running it is not part of the build:** the coordinator runs it on staging under the same standing approval, after the merge; production is never covered.

A release that has the code but not yet the table is safe. With `SMS_INBOX_ENABLED` off, storing, the access check and the grant command touch no inbox table (tasks 3, 5, 6, each with a test). The retention rule and the deletion command do not look at the switch (3.2); each first checks that the table exists and does nothing when it does not (tasks 8 and 9, each with a test, and the hand check of task 11).

## 5. Tasks

Every task: write the tests first, run them and see them fail for the stated reason, then write the code, run them and see them pass, then commit. Test command throughout:

```bash
uv run python manage.py test apps.inbox apps.notifications apps.operators
```

### Task 1: the switch and the app

**Files:** `config/settings.py`; new `apps/inbox/__init__.py` (empty), `apps/inbox/apps.py`, `apps/inbox/switch.py`, `apps/inbox/tests/__init__.py` (empty), `apps/inbox/tests/base.py`, `apps/inbox/tests/test_models.py`.

- [ ] **Step 1: the failing test.** `apps/inbox/tests/test_models.py`:

```python
from django.apps import apps
from django.contrib import admin
from django.test import SimpleTestCase, override_settings

from apps.inbox import switch


class SwitchTests(SimpleTestCase):
    def test_1_the_switch_is_off_unless_set(self):
        self.assertIs(switch.inbox_on(), False)
        with override_settings(SMS_INBOX_ENABLED=True):
            self.assertIs(switch.inbox_on(), True)

    def test_2_the_app_is_installed(self):
        self.assertTrue(apps.is_installed("apps.inbox"))

    def test_3_no_inbox_record_has_a_django_admin_screen(self):
        registered = [m._meta.label for m in admin.site._registry if m._meta.app_label == "inbox"]
        self.assertEqual(registered, [])
```

`admin.site` is the project's one admin site: `HqAdminConfig.default_site` names `apps.operators.admin_site.HqAdminSite`, and the existing `admin.py` files register with `@admin.register`.

- [ ] **Step 2:** run it; it fails with `ModuleNotFoundError: apps.inbox`.
- [ ] **Step 3: the code.**

`config/settings.py`, after the `SMS_GATEWAY_ENABLED` line:

```python
# The SMS inbox. "1" lets texts a phone forwards be stored and read. Off, nothing is stored or
# shown and `received` discards what a phone sends, as before the inbox existed. Texts already
# stored are still deleted at 365 days, and on a person's request, whatever this says.
SMS_INBOX_ENABLED = os.environ.get("SMS_INBOX_ENABLED", "").strip() == "1"
```

and in `INSTALLED_APPS`, after `'apps.notifications',`:

```python
    'apps.inbox',
```

`apps/inbox/apps.py`:

```python
from django.apps import AppConfig


class InboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inbox"

    def ready(self):
        from apps.inbox import retention  # noqa: F401  registers the retention rule
```

(`retention.py` arrives in task 8; until then the builder leaves `ready()` out and adds it there.)

`apps/inbox/switch.py`:

```python
"""The inbox's one switch. Off, nothing in this app stores into or shows from a table. Only
the retention rule and the deletion command touch one while it is off, and they only delete."""
from django.conf import settings
from django.db import connection

SMS = "sms"
MESSAGE_TABLE = "inbox_conversationmessage"


def inbox_on():
    return bool(settings.SMS_INBOX_ENABLED)


def tables_exist():
    """True once inbox.0001 is applied to this database. The code is released before its
    tables are created, so what must work with the switch off asks this first."""
    return MESSAGE_TABLE in connection.introspection.table_names()
```

The table's name is written out because `models.py` imports this module; task 2's `test_10` holds it equal to the model's own.

`apps/inbox/tests/base.py`:

```python
"""Helpers shared by the inbox tests."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.notifications.tests.base import SmsTestCase, online_phone

inbox_on = override_settings(SMS_INBOX_ENABLED=True)
SENDER = "+639189990000"


@inbox_on
class InboxTestCase(SmsTestCase):
    """The gateway and the inbox on, one phone that forwards, one staff user."""

    def setUp(self):
        super().setUp()
        self.phone = online_phone(forwards_incoming=True)
        self.staff = get_user_model().objects.create_user("ana", password="pw", is_staff=True)
```

- [ ] **Step 4:** run; the three pass. `uv run python manage.py check` is clean.
- [ ] **Step 5:** commit: `feat(inbox): the SMS_INBOX_ENABLED switch and the empty inbox app [z8uq9me128]`.

### Task 2: the records and the migration

**Files:** new `apps/inbox/models.py`, `apps/inbox/migrations/__init__.py`, `0001_initial.py`; `apps/inbox/tests/test_models.py`.

- [ ] **Step 1: the failing tests**, added to `test_models.py` (a `TestCase` class `RecordTests`, using `online_phone` and a staff user):

| Test | Asserts |
|---|---|
| `test_4_an_hq_conversation_has_no_site_and_no_account` | Creating `owner_kind="hq"` with a Site raises `IntegrityError`; `owner_kind="site"` without one raises it; `owner_kind="account"` with a Site raises it |
| `test_5_one_hq_conversation_per_platform_line_and_contact` | A second row with the same line and contact raises `IntegrityError`; the same contact on a second phone is allowed |
| `test_6_only_sms_is_a_platform` | `platform="email"` on a conversation, and on a message, each raises `IntegrityError` |
| `test_7_a_phone_stores_an_incoming_id_once` | Two `in` messages with the same `device` and `gateway_ref` raise `IntegrityError`; the same `gateway_ref` from a second phone is allowed; two `out` messages with a blank `gateway_ref` are allowed |
| `test_8_one_access_row_per_staff_user_and_platform` | A second `InboxAccess` for the same user and `sms` raises `IntegrityError` |
| `test_9_public_ids_carry_their_prefix` | `is_public_id(conversation.public_id, "conv")` and `is_public_id(message.public_id, "msg")` |
| `test_10_the_switch_knows_the_message_table` | `switch.MESSAGE_TABLE == ConversationMessage._meta.db_table`, and `switch.tables_exist()` is true in a test database |

Each `IntegrityError` case runs inside `with transaction.atomic():`.

- [ ] **Step 2:** run; they fail with `ImportError` on `apps.inbox.models`.
- [ ] **Step 3: the code.** `apps/inbox/models.py`:

```python
"""The inbox's three records. `body` is the only column in the database that holds message
text. No record here has a Django admin screen."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.ids import new_public_id
from apps.inbox.switch import SMS


def new_conversation_id():
    return new_public_id("conv")


def new_message_id():
    return new_public_id("msg")


class Platform(models.TextChoices):
    SMS = SMS, "SMS"


class Conversation(models.Model):
    class Owner(models.TextChoices):
        HQ = "hq", "NegosyoOnline"
        SITE = "site", "A Site"
        ACCOUNT = "account", "A Subscriber"

    public_id = models.CharField(max_length=40, unique=True, default=new_conversation_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    line = models.ForeignKey("notifications.SmsDevice", on_delete=models.PROTECT, related_name="+")
    contact = models.CharField(max_length=32)
    can_reply = models.BooleanField(default=False)
    unread_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["-last_message_at"], name="inbox_conv_latest")]
        constraints = [
            models.CheckConstraint(
                name="inbox_conversation_owner",
                condition=(
                    Q(owner_kind="hq", site__isnull=True, account__isnull=True)
                    | Q(owner_kind="site", site__isnull=False, account__isnull=True)
                    | Q(owner_kind="account", site__isnull=True, account__isnull=False)
                ),
            ),
            models.CheckConstraint(name="inbox_conversation_platform", condition=Q(platform=SMS)),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_hq_contact",
                fields=["platform", "line", "contact"], condition=Q(owner_kind="hq"),
            ),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_site_contact",
                fields=["site", "platform", "line", "contact"], condition=Q(owner_kind="site"),
            ),
            models.UniqueConstraint(
                name="inbox_conversation_one_per_account_contact",
                fields=["account", "platform", "line", "contact"], condition=Q(owner_kind="account"),
            ),
        ]

    def __str__(self):
        return self.public_id


class ConversationMessage(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Received"
        OUT = "out", "Sent by staff"

    class Kind(models.TextChoices):
        REPLY = "reply", "Reply"
        COMPOSE = "compose", "Composed"

    public_id = models.CharField(max_length=40, unique=True, default=new_message_id, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    body = models.TextField()
    clipped = models.BooleanField(default=False)
    gateway_ref = models.CharField(max_length=100, blank=True)
    device = models.ForeignKey("notifications.SmsDevice", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    sim_slot = models.PositiveSmallIntegerField(null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=8, blank=True, choices=Kind.choices)
    occurred_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_message_platform", condition=Q(platform=SMS)),
            models.UniqueConstraint(
                name="inbox_message_once_per_phone",
                fields=["device", "gateway_ref"], condition=Q(direction="in"),
            ),
        ]

    def __str__(self):
        return self.public_id


class InboxAccess(models.Model):
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inbox_access")
    platform = models.CharField(max_length=16, choices=Platform.choices)
    can_send = models.BooleanField(default=False)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(name="inbox_access_one_per_operator_platform", fields=["operator", "platform"]),
        ]
```

`condition=` is the spelling the repository already uses for a check constraint (`apps/catalog/models.py`).

- [ ] **Step 4: the migration.**

```bash
uv run python manage.py makemigrations inbox
```

Expected: one file, `apps/inbox/migrations/0001_initial.py`, with `CreateModel` for `Conversation`, `ConversationMessage` and `InboxAccess` and no other operation. Read the file. If it holds anything else, or if `makemigrations` proposes a change to another app, stop and report it.

- [ ] **Step 5:** run the tests; all pass. Run `uv run python manage.py makemigrations --check`; it reports no changes.
- [ ] **Step 6:** commit: `feat(inbox): the conversation, message and access records, with inbox.0001 [z8uq9me128]`.

### Task 3: storing an incoming text

**Files:** new `apps/inbox/storing.py`, `apps/inbox/tests/test_storing.py`; `apps/notifications/gateway.py`; `apps/notifications/tests/test_sms_gateway.py`.

What the phone app sends and what it does with each answer is in section 3.1; it was read before this task was revised. If the phone app's `main` has moved from `b69aec3` when the build starts, the builder reads `IncomingForwarder.kt`, `Inbox.kt` and `HttpGateway.kt` again and reports any difference before writing this task.

- [ ] **Step 1: the failing tests.** `apps/inbox/tests/test_storing.py`, class `ReceivedStoringTests(InboxTestCase)`, calling the real `received` view with the phone's token the way `GatewayCase.call` does (the builder reuses that helper):

| Test | Asserts |
|---|---|
| `test_1_inbox_switch_off_keeps_nothing_and_reads_no_inbox_table` | With `SMS_INBOX_ENABLED=False` and the phone's switch on: 204, empty body; `assertNumQueries` shows only the query that finds the phone by its token; no inbox row |
| `test_2_phone_switch_off_keeps_nothing` | The same with the inbox on and `forwards_incoming=False` |
| `test_3_a_valid_list_is_stored` | 204; one conversation: `owner_kind` `hq`, no site, no account, `platform` `sms`, `line` the phone, `contact` `+639189990000`, `can_reply` true, `unread_count` 1; one message: `in`, the text, `gateway_ref` `in_1`, the phone, `sim_slot` 0, `occurred_at` the phone's time, `author` empty, `kind` blank, `platform` `sms` |
| `test_4_a_repeat_of_the_same_ids_stores_nothing_new` | The same body twice: one message, `unread_count` 1 |
| `test_5_two_texts_from_one_sender_share_a_conversation` | Two ids, one sender: one conversation, two messages, `unread_count` 2, `last_message_at` the later arrival |
| `test_6_every_form_of_a_philippine_mobile_is_one_contact` | `09189990000`, `639189990000` and `+63 918 999 0000` land in the conversation of `+639189990000` |
| `test_7_a_sender_that_is_not_a_mobile_is_kept_as_received` | `from` `GLOBE`: contact `GLOBE`, `can_reply` false; a 40-character sender is cut to 32 |
| `test_8_a_long_text_is_cut_and_marked` | A 2,500-character text: `body` 2,000 characters, `clipped` true |
| `test_9_the_owner_is_hq_whatever_the_body_says` | An item carrying `owner_kind`, `site` and `platform` keys: stored as `hq`, `sms`, no site |
| `test_10_a_body_that_is_not_a_list_of_texts_stores_nothing_and_answers_400` | Each of: not JSON; a JSON list; `messages` missing; `messages` not a list; 21 items. 400, code `invalid_request`, no inbox row |
| `test_10b_a_bad_item_is_skipped_and_the_rest_are_stored` | A list of one good item and, in turn, each of: an item that is not an object; `id` missing; `id` not a string; `id` empty; `id` of 101 characters; `from` missing; `from` empty; `from` only spaces; `text` missing; `text` not a string. Each time: 204, the good item stored, one message in all. A list of bad items only: 204, no inbox row |
| `test_10c_the_same_list_again_stores_nothing_new` | The list of `test_10b` sent twice: still one message, `unread_count` 1 (what the phone does when an answer was lost) |
| `test_10d_an_empty_text_is_a_text` | `text` `""`: stored with an empty `body` (3.1: the phone can send one) |
| `test_11_skipped_items_log_their_count_only` | Two bad items and one good: `assertLogs("apps.inbox.storing", "ERROR")` gives exactly one line, `sms_incoming_skipped count=2`; it holds no sender, no text and no id |
| `test_11b_a_refused_list_logs_its_count_only` | 21 items: exactly one line, `sms_incoming_rejected count=21`; a body that is not JSON: `sms_incoming_rejected count=0` |
| `test_12_storing_writes_no_log_cache_or_audit_entry` | For a valid list: `assertNoLogs(level="DEBUG")`; the cache's `set`, `add`, `incr` and `set_many` are not called; no `OperatorAuditEntry` is written |
| `test_13_an_unreadable_time_becomes_the_servers` | `received_at` `"yesterday"`, missing, or without a zone: `occurred_at` is within a few seconds of now |
| `test_14_an_odd_sim_slot_is_left_empty` | `sim_slot` `"1"`, `-1`, `true`, `12`: stored empty |
| `test_15_received_still_does_not_count_as_a_poll` | `last_contact_at` stays empty |
| `test_16_nul_characters_are_removed` | `text` `"a\u0000b"`, `from` `"GLO\u0000BE"` and `id` `"in_\u00001"`: 204; stored `body` `ab`, `contact` `GLOBE`, `gateway_ref` `in_1`; no stored value holds a NUL character. A `from` that is only NUL characters is a bad item |

In `apps/notifications/tests/test_sms_gateway.py`: `test_17_…_with_the_switch_on` is renamed `…_with_the_phone_switch_on_and_the_inbox_off` and keeps its body (the inbox is off there). `test_16` gains: with `SMS_INBOX_ENABLED` off, `poll` answers `incoming` false even when the phone's switch is on; with both on, true (C6).

- [ ] **Step 2:** run; `test_storing` fails with `ImportError`, and the changed `test_16` fails on the `incoming` value.
- [ ] **Step 3: the code.** `apps/inbox/storing.py`:

```python
"""The one place an incoming text is stored. Called only by the gateway's `received` call.

No log line, cache entry or audit entry made here ever holds a number or a text."""
import logging
from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.switch import SMS, inbox_on
from apps.notifications.models import SmsDevice

logger = logging.getLogger(__name__)

MAX_TEXTS = 20
MAX_BODY = 2000
MAX_CONTACT = 32
MAX_REF = 100


def owner_of(device):
    """Who owns a conversation on this phone's line. Always NegosyoOnline in this build: no
    phone is assigned to a Site. The Dedicated SMS Line add-on changes this one function."""
    return {"owner_kind": Conversation.Owner.HQ, "site": None, "account": None}


def wanted(device):
    """True when a text from this phone is to be stored."""
    return inbox_on() and device.forwards_incoming


def _text(value, longest=None):
    """A string without its NUL characters, which PostgreSQL refuses; None when it is not a
    string. With `longest` it is also trimmed, and None when it is then empty or too long."""
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", "")
    if longest is None:
        return value
    value = value.strip()
    return value if 0 < len(value) <= longest else None


def _clean(body):
    """The good texts in the body as plain values, and how many items were skipped as bad.
    None, and how many items the list held, when the body is not a list of at most 20 items.

    One bad item never refuses the others: the phone sends a refused list again unchanged,
    so a single bad text would hold back every text behind it."""
    items = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None, 0
    if len(items) > MAX_TEXTS:
        return None, len(items)
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref, sender, text = _text(item.get("id"), MAX_REF), _text(item.get("from"), 10_000), _text(item.get("text"))
        if ref is None or sender is None or text is None:
            continue
        cleaned.append((ref, sender, text, item.get("received_at"), item.get("sim_slot")))
    return cleaned, len(items) - len(cleaned)


def _contact(sender):
    """The sender as stored, and whether it can be replied to."""
    try:
        return normalise_ph_mobile(sender), True
    except ValueError:
        return sender[:MAX_CONTACT], False


def _when(raw, now):
    try:
        value = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return now
    return value if value.tzinfo else now


def _slot(raw):
    whole = isinstance(raw, int) and not isinstance(raw, bool)
    return raw if whole and 0 <= raw <= 9 else None


def store(device, body):
    """Store the texts of one `received` call. True when the call is done with: its good
    texts are stored or were already, and its bad ones are skipped for good. False when the
    body is not a list of texts and nothing was stored."""
    texts, count = _clean(body)
    if texts is None:
        logger.error("sms_incoming_rejected count=%s", count)
        return False
    if count:
        logger.error("sms_incoming_skipped count=%s", count)
    if not texts:
        return True
    now = timezone.now()
    with transaction.atomic():
        # One call per phone at a time, so a repeat that overlaps the first cannot store twice.
        SmsDevice.objects.select_for_update().get(pk=device.pk)
        seen = set(
            ConversationMessage.objects.filter(
                device=device, direction=ConversationMessage.Direction.IN,
                gateway_ref__in=[ref for ref, *_ in texts],
            ).values_list("gateway_ref", flat=True)
        )
        for ref, sender, text, received_at, sim_slot in texts:
            if ref in seen:
                continue
            seen.add(ref)
            contact, can_reply = _contact(sender)
            occurred_at = _when(received_at, now)
            conversation, _ = Conversation.objects.get_or_create(
                platform=SMS, line=device, contact=contact, **owner_of(device),
                defaults={"can_reply": can_reply, "last_message_at": occurred_at},
            )
            ConversationMessage.objects.create(
                conversation=conversation, platform=SMS, direction=ConversationMessage.Direction.IN,
                body=text[:MAX_BODY], clipped=len(text) > MAX_BODY, gateway_ref=ref,
                device=device, sim_slot=_slot(sim_slot), occurred_at=occurred_at,
            )
            Conversation.objects.filter(pk=conversation.pk).update(
                unread_count=F("unread_count") + 1,
                last_message_at=max(conversation.last_message_at, occurred_at),
            )
    return True
```

`apps/notifications/gateway.py`: the import `from apps.inbox import storing`; in `poll`, `"incoming": storing.wanted(device)`; and the last block becomes:

```python
def store_incoming(device, request):
    """Hand the texts a phone received to the inbox. False when the list is not valid. With
    SMS_INBOX_ENABLED or the phone's switch off the body is never read and nothing is kept."""
    if not storing.wanted(device):
        return True
    return storing.store(device, _body(request))


@phone_call
def received(request, device):
    if not store_incoming(device, request):
        return _error(400, "invalid_request", "Send the texts as a list of id, from and text.")
    return HttpResponse(status=204)
```

The module docstring's `received` lines gain: `answers 400: the body is not a list of at most 20 texts; nothing was kept. An item that cannot be stored is skipped, and the answer is still 204.`

A text is never refused for what it says or who sent it: after this task the only 400 is for a body the phone app never sends (3.1). At most one log line is written per call, and it holds a count only.

The builder checks that `apps.inbox.storing` importing `apps.notifications.models`, and `apps.notifications.gateway` importing `apps.inbox.storing`, start without an import cycle (`manage.py check`). `get_or_create` with a conditional unique constraint: the lock on the phone's row is what stops two calls creating the same conversation; the test of step 1 row 4 covers the repeat.

- [ ] **Step 4:** run; all pass, and the rest of `apps.notifications` still passes.
- [ ] **Step 5:** commit: `feat(inbox): received stores a forwarded text, behind SMS_INBOX_ENABLED and the phone's switch [z8uq9me128]`.

### Task 4: `sms_device incoming on` refuses while the inbox is off

**Files:** `apps/notifications/management/commands/sms_device.py`, `apps/notifications/tests/test_sms_commands.py`.

- [ ] **Step 1: the failing tests.** The existing cases that run `incoming on` (`test_sms_commands.py` lines 114 and 143 at `bef2f89`) are wrapped in `override_settings(SMS_INBOX_ENABLED=True)`. Added:

| Test | Asserts |
|---|---|
| `test_8_incoming_on_is_refused_while_the_inbox_is_off` | `CommandError` "SMS_INBOX_ENABLED is off. Turn it on before a phone forwards texts."; `forwards_incoming` stays false; no audit entry |
| `test_9_incoming_off_works_while_the_inbox_is_off` | A phone with the switch on is turned off, and audited |
| `test_10_incoming_on_says_what_is_stored` | The output holds "Texts this phone receives are now stored in the inbox, for 365 days." and not "Nothing the phone forwards is stored" |

- [ ] **Step 2:** run; 8 and 10 fail.
- [ ] **Step 3: the code.** `_incoming` becomes:

```python
    def _incoming(self, name, actor, switch):
        if switch == "on" and not inbox_on():
            raise CommandError("SMS_INBOX_ENABLED is off. Turn it on before a phone forwards texts.")
        device = self._locked(name)
        device.forwards_incoming = switch == "on"
        device.save(update_fields=["forwards_incoming"])
        self._record(
            actor, "sms_device_incoming", device, {"forwards_incoming": device.forwards_incoming}
        )
        self.stdout.write(f"Incoming texts: {switch}. The phone learns it with its next poll.")
        if device.forwards_incoming:
            self.stdout.write("Texts this phone receives are now stored in the inbox, for 365 days.")
```

with `from apps.inbox.switch import inbox_on` at the top.

- [ ] **Step 4:** run; pass.
- [ ] **Step 5:** commit: `feat(inbox): a phone is not told to forward texts while the inbox is off [z8uq9me128]`.

### Task 5: the one access check

**Files:** new `apps/inbox/access.py`, `apps/inbox/tests/test_access.py`.

- [ ] **Step 1: the failing tests**, class `ScopeTests(InboxTestCase)`:

| Test | Asserts |
|---|---|
| `test_1_no_row_no_scope` | A staff user without a row: `scope_for(user, "sms")` is `None` |
| `test_2_a_superuser_without_a_row_has_no_scope` | The same for `is_superuser=True` |
| `test_3_a_row_gives_hq_sms_conversations` | With a row: the scope's conversations hold the `hq` conversation and not one owned by a Site or by an account (both made in the test); `can_send` false |
| `test_4_can_send_follows_the_row` | `can_send=True` on the row gives it on the scope |
| `test_5_any_other_platform_has_no_scope` | `messenger`, `instagram`, `tiktok`, `email`, `webchat`, `fax` and `None`, with a row for `sms`: `None`, and `assertNumQueries(0)` |
| `test_6_no_scope_and_no_query_while_the_inbox_is_off` | `SMS_INBOX_ENABLED=False`, with a row: `None`, `assertNumQueries(0)` |
| `test_7_only_an_active_staff_user` | `is_staff=False`, `is_active=False`, an anonymous user and `None`: `None` |

- [ ] **Step 2:** run; `ImportError`.
- [ ] **Step 3: the code.** `apps/inbox/access.py`:

```python
"""The one access check of the inbox. Every list, read and send starts from the scope it
answers; nothing reaches a conversation or a message any other way."""
from dataclasses import dataclass

from django.db.models import QuerySet

from apps.inbox.models import Conversation, InboxAccess
from apps.inbox.switch import SMS, inbox_on


@dataclass(frozen=True)
class Scope:
    actor: object
    platform: str
    conversations: QuerySet
    can_send: bool


def scope_for(actor, platform):
    """What this actor may reach on this platform, or None. A platform is always named; no
    scope spans platforms. Admitting a Subscriber later is a new branch here and nowhere else."""
    if platform != SMS or not inbox_on():
        return None
    if actor is None or not (actor.is_authenticated and actor.is_active and actor.is_staff):
        return None
    row = InboxAccess.objects.filter(operator=actor, platform=SMS).first()
    if row is None:
        return None
    conversations = Conversation.objects.filter(platform=SMS, owner_kind=Conversation.Owner.HQ)
    return Scope(actor=actor, platform=SMS, conversations=conversations, can_send=row.can_send)
```

- [ ] **Step 4:** run; pass.
- [ ] **Step 5:** commit: `feat(inbox): scope_for, the one access check [z8uq9me128]`.

### Task 6: the grant command

**Files:** new `apps/inbox/management/__init__.py`, `management/commands/__init__.py`, `management/commands/sms_inbox_access.py`; `apps/operators/audit.py`; `apps/inbox/tests/test_access.py`.

```text
manage.py sms_inbox_access grant --user USERNAME [--send] --actor USERNAME
manage.py sms_inbox_access revoke --user USERNAME --actor USERNAME
manage.py sms_inbox_access list
```

- [ ] **Step 1: the failing tests**, class `AccessCommandTests(InboxTestCase)`:

| Test | Asserts |
|---|---|
| `test_8_grant_gives_read` | One row: the user, `sms`, `can_send` false, `granted_by` the actor. One audit entry `sms_inbox_access`, operator the actor, `target_type` `inbox_access`, `target_id` the user's id, `detail` `{"platform": "sms", "change": "grant", "level": "read"}`, `personal` empty. Output "Granted: read." |
| `test_9_grant_with_send` | `can_send` true; `level` `send`; output "Granted: read and send." |
| `test_10_grant_again_changes_the_level` | A second `grant` without `--send` on a row with send: one row, `can_send` false, a second audit entry. A `grant` that changes nothing writes no entry and says "Already granted: read." |
| `test_11_revoke_removes_the_row` | No row; audit `change` `revoke`, `level` `none`. A `revoke` with no row says "No access to revoke." and writes no entry |
| `test_12_list_shows_who_and_what` | One line per row: `ana\tsms\tread and send\tgranted_by=coach\t<date>`; with no row, "No one has access." |
| `test_13_the_user_and_the_actor_must_be_active_staff` | `--user` or `--actor` missing, unknown, inactive or not staff: `CommandError`, no row, no entry |
| `test_14_every_action_is_refused_while_the_inbox_is_off` | `SMS_INBOX_ENABLED=False`: `grant`, `revoke` and `list` each raise `CommandError` "SMS_INBOX_ENABLED is off."; `assertNumQueries(0)` |
| `test_15_the_audit_actions_are_declared` | `audit.ACTIONS` holds `sms_inbox_access`, `sms_inbox_find` and `sms_inbox_delete` as `KEPT` and `sms_inbox_open` as `PERSONAL` |

- [ ] **Step 2:** run; fail (no such command).
- [ ] **Step 3: the code.** `apps/operators/audit.py`, in `ACTIONS`, after `"sms_device_incoming": KEPT,`:

```python
    "sms_inbox_access": KEPT,
    "sms_inbox_open": PERSONAL,
    "sms_inbox_find": KEPT,
    "sms_inbox_delete": KEPT,
```

All four names are declared here, in one edit of this shared file; `sms_inbox_find` is used by task 7 and `sms_inbox_delete` by task 9. The three kept whole may never hold a personal value: `audit.record` refuses one.

`apps/inbox/management/commands/sms_inbox_access.py`:

```python
"""Give or take away a staff user's access to the SMS inbox. This is the only way to do so:
the access is a row of its own, not a Django permission, and a superuser has none without it.

    manage.py sms_inbox_access grant --user USERNAME [--send] --actor USERNAME
    manage.py sms_inbox_access revoke --user USERNAME --actor USERNAME
    manage.py sms_inbox_access list
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inbox.models import InboxAccess
from apps.inbox.switch import SMS, inbox_on
from apps.operators import audit

TARGET = "inbox_access"
LEVELS = {False: "read", True: "send"}
WORDS = {False: "read", True: "read and send"}


class Command(BaseCommand):
    help = "Grant, revoke or list access to the SMS inbox."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("grant", "revoke", "list"))
        parser.add_argument("--user", help="the staff user the access is for")
        parser.add_argument("--send", action="store_true", help="grant: may also reply and compose")
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")

    def handle(self, *args, action, user, send, actor, **options):
        if not inbox_on():
            raise CommandError("SMS_INBOX_ENABLED is off.")
        if action == "list":
            return self._list()
        actor = self._staff_user(actor, "--actor")
        user = self._staff_user(user, "--user")
        with transaction.atomic():
            getattr(self, f"_{action}")(user, actor, send)

    def _staff_user(self, username, option):
        found = get_user_model().objects.filter(
            username=username or "", is_active=True, is_staff=True
        ).first()
        if found is None:
            raise CommandError(f"{option} must be an active staff user.")
        return found

    def _record(self, actor, user, change, level):
        audit.record(
            actor, "sms_inbox_access", target_type=TARGET, target_id=user.pk,
            detail={"platform": SMS, "change": change, "level": level},
        )

    def _grant(self, user, actor, send):
        row = InboxAccess.objects.select_for_update().filter(operator=user, platform=SMS).first()
        if row is not None and row.can_send == send:
            self.stdout.write(f"Already granted: {WORDS[send]}.")
            return
        if row is None:
            InboxAccess.objects.create(operator=user, platform=SMS, can_send=send, granted_by=actor)
        else:
            row.can_send, row.granted_by = send, actor
            row.save(update_fields=["can_send", "granted_by"])
        self._record(actor, user, "grant", LEVELS[send])
        self.stdout.write(f"Granted: {WORDS[send]}.")

    def _revoke(self, user, actor, _send):
        deleted, _ = InboxAccess.objects.filter(operator=user, platform=SMS).delete()
        if not deleted:
            self.stdout.write("No access to revoke.")
            return
        self._record(actor, user, "revoke", "none")
        self.stdout.write("Revoked.")

    def _list(self):
        rows = list(InboxAccess.objects.select_related("operator", "granted_by").order_by("operator__username"))
        for row in rows:
            self.stdout.write(
                f"{row.operator.username}\t{row.platform}\t{WORDS[row.can_send]}"
                f"\tgranted_by={row.granted_by.username}\t{row.granted_at:%Y-%m-%d}"
            )
        if not rows:
            self.stdout.write("No one has access.")
```

- [ ] **Step 4:** run; pass, and `apps.operators` still passes (its retention rule reads `ACTIONS` and now also blanks `sms_inbox_open` entries after a year, which design 8 asks for).
- [ ] **Step 5:** commit: `feat(inbox): the sms_inbox_access command, audited [z8uq9me128]`.

### Task 7: the masked read functions and the open entry

**Files:** new `apps/inbox/reading.py`, `apps/inbox/tests/test_reading.py`.

These are what plan 4's routes will answer with. Each takes a `Scope`; none takes a user or a table.

- [ ] **Step 1: the failing tests**, class `ReadingTests(InboxTestCase)`, with a granted staff user and texts stored through `storing.store`:

| Test | Asserts |
|---|---|
| `test_1_the_list_is_masked_and_holds_no_text` | `conversations(scope)` gives, newest first, dicts with exactly `id`, `contact` (`+63918***0000`), `can_reply`, `unread`, `last_message_at`, `last_direction`. `json.dumps` of the answer holds neither the full number nor any word of the text |
| `test_2_a_name_and_a_short_code_are_shown_as_received` | `GLOBE`, `8080` and `225687` are shown as they are |
| `test_2b_a_number_that_cannot_be_replied_to_is_masked_too` | In the list: a foreign mobile `+14155550123` is shown `***0123`; a landline `0281234567` is shown `***4567`; a 7-digit `8123456` is shown `***`; `+1 (415) 555-0123` is shown `***0123`. `can_reply` is false for each. `json.dumps` of the answer holds none of the four as received |
| `test_3_the_list_writes_no_audit_entry` | No `OperatorAuditEntry` |
| `test_4_open_gives_the_texts_masks_the_number_and_is_audited` | `open_conversation(scope, public_id)` gives `id`, `contact` masked, `can_reply`, and `messages` oldest first, each exactly `id`, `direction`, `body`, `clipped`, `at`. No full number in it. One audit entry `sms_inbox_open`: operator the staff user, `target_type` `conversation`, `target_id` the `public_id`, `detail` `{"platform": "sms", "owner_kind": "hq", "messages": 2}`, `personal` `{"contact": "+63918***0000"}`. Neither holds the text |
| `test_4b_open_masks_a_foreign_number_in_the_answer_and_in_the_entry` | Opening the conversation of `+14155550123`: `contact` is `***0123` in the answer, and the entry's `personal` is `{"contact": "***0123"}`; `json.dumps` of the answer, and of the entry's `detail` and `personal`, does not hold `+14155550123` or `4155550123` |
| `test_5_open_clears_the_unread_count` | `unread_count` becomes 0; a text stored afterwards makes it 1 |
| `test_6_each_open_is_one_entry` | Two opens, two entries |
| `test_7_an_id_outside_the_scope_is_not_found` | An unknown id, a malformed id, and the id of a conversation owned by a Site: `None`, no audit entry, `unread_count` unchanged |
| `test_8_find_matches_a_full_number_and_is_audited` | `find(scope, "0918 999 0000")` gives that conversation's list row, masked. One audit entry `sms_inbox_find`: operator the staff user, `target_type` and `target_id` blank, `detail` exactly `{"platform": "sms", "matches": 1}`, `personal` empty. `json.dumps` of the entry holds no run of 4 or more digits. `assertNoLogs` |
| `test_8b_a_lookup_with_no_match_is_audited_and_a_fragment_is_not` | A full number with no conversation: `[]`, and one entry with `matches` 0. `"0918"`, `"9990000"`, `""`, `"GLOBE"`, `None` and `"+14155550123"` each give `[]` and write no entry |
| `test_9_no_django_admin_screen` | Step 1 of task 1, `test_3`, run again here after the models exist |

- [ ] **Step 2:** run; `ImportError`.
- [ ] **Step 3: the code.** `apps/inbox/reading.py`:

```python
"""What a screen may be shown. Masking is done here, by the backend: an answer never carries
a full number, and the list never carries a text. Reading a text always goes through
`open_conversation`, which writes the audit entry in the same transaction."""
from django.db import transaction
from django.db.models import OuterRef, Subquery

from apps.common.ids import is_public_id
from apps.common.masking import HIDDEN, mask_mobile
from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import ConversationMessage
from apps.operators import audit

TARGET = "conversation"
# A sender with this many digits is somebody's number, whether or not it can be replied to.
NUMBER_DIGITS = 7


def is_a_number(sender):
    return sum(character.isdigit() for character in sender) >= NUMBER_DIGITS


def shown_contact(conversation):
    """Every number masked. Only a sender name or a short code is shown as received.

    A Philippine mobile keeps the form used everywhere else. Any other number (a foreign
    mobile, a landline) shows its last 4 characters at most: `mask_mobile` would show all of
    a 10-character number."""
    contact = conversation.contact
    if conversation.can_reply:
        return mask_mobile(contact)
    if not is_a_number(contact):
        return contact
    return HIDDEN + contact[-4:] if len(contact) >= 10 else HIDDEN


def _row(conversation):
    return {
        "id": conversation.public_id,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "unread": conversation.unread_count,
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_direction": conversation.last_direction,
    }


def _listed(queryset):
    latest = ConversationMessage.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-pk")
    rows = queryset.annotate(last_direction=Subquery(latest.values("direction")[:1]))
    return [_row(conversation) for conversation in rows.order_by("-last_message_at", "-pk")]


def conversations(scope):
    """The inbox list. No text, not even a preview. Paging is added by the route that calls it."""
    return _listed(scope.conversations)


def find(scope, raw):
    """The conversations of one full number, typed by the staff member. Never a fragment.

    Each lookup of a full number is one audit entry, with or without a match: it confirms
    whether that person wrote to us. The entry holds the count and never the number."""
    try:
        number = normalise_ph_mobile(raw if isinstance(raw, str) else "")
    except ValueError:
        return []
    rows = _listed(scope.conversations.filter(contact=number, can_reply=True))
    audit.record(scope.actor, "sms_inbox_find", detail={"platform": scope.platform, "matches": len(rows)})
    return rows


def open_conversation(scope, public_id):
    """One conversation with its texts, or None when it is not in the scope. Writes the audit
    entry and clears the unread count."""
    if not is_public_id(public_id, "conv"):
        return None
    with transaction.atomic():
        conversation = scope.conversations.select_for_update().filter(public_id=public_id).first()
        if conversation is None:
            return None
        messages = [
            {
                "id": message.public_id,
                "direction": message.direction,
                "body": message.body,
                "clipped": message.clipped,
                "at": (message.occurred_at or message.created_at).isoformat(),
            }
            for message in conversation.messages.order_by("created_at", "pk")
        ]
        conversation.unread_count = 0
        conversation.save(update_fields=["unread_count"])
        audit.record(
            scope.actor, "sms_inbox_open", target_type=TARGET, target_id=conversation.public_id,
            detail={"platform": scope.platform, "owner_kind": conversation.owner_kind, "messages": len(messages)},
            personal={"contact": shown_contact(conversation)},
        )
    return {
        "id": conversation.public_id,
        "contact": shown_contact(conversation),
        "can_reply": conversation.can_reply,
        "messages": messages,
    }
```

- [ ] **Step 4:** run; pass.
`find` matches Philippine mobiles only, as before: a foreign number or a landline is found by reading the list, where it is masked, or by the deletion command of task 9.

- [ ] **Step 5:** commit: `feat(inbox): masked read functions; opening and finding are audited [z8uq9me128]`.

### Task 8: retention

**Files:** new `apps/inbox/retention.py`, `apps/inbox/tests/test_retention.py`; `apps/inbox/apps.py` (`ready()`); `apps/notifications/retention.py`.

- [ ] **Step 1: the failing tests**, class `InboxRetentionTests(InboxTestCase)`. Rows are aged by `ConversationMessage.objects.filter(...).update(created_at=...)`.

| Test | Asserts |
|---|---|
| `test_1_the_rule_is_registered_and_daily` | `"inbox_messages"` in `apps.common.retention.names()` and in `apps.notifications.retention.DAILY_RULES` |
| `test_2_a_message_older_than_365_days_is_deleted` | A message of 366 days goes; one of 364 days stays; the count returned is 1 |
| `test_3_a_conversation_left_empty_is_deleted` | Its only message was old: the conversation goes. A conversation with one old and one young message stays, with the young one |
| `test_4_the_dry_run_changes_nothing` | It returns the count and deletes nothing |
| `test_5_the_rule_still_deletes_while_the_inbox_is_off` | `SMS_INBOX_ENABLED=False`, the table present: a message of 366 days is deleted and its emptied conversation with it; one of 364 days stays; the count returned is 1 |
| `test_5b_the_dry_run_counts_while_the_inbox_is_off` | `SMS_INBOX_ENABLED=False`: the dry run returns 1 and deletes nothing |
| `test_5c_with_no_table_the_rule_does_nothing` | `switch.tables_exist` patched to answer false, the inbox on and then off: returns 0, `assertNumQueries(0)`, the old message is still there |
| `test_5d_the_table_check_is_one_query` | With the table present and nothing to delete, a dry run makes the one read of the table names and the one count |
| `test_6_it_is_not_behind_retention_enabled` | With `RETENTION_ENABLED=False` and the inbox on, a real run deletes |
| `test_7_the_daily_run_includes_it` | `run_daily_rules()` reports a count for `inbox_messages`, and its `retention_run` audit entry names it |

- [ ] **Step 2:** run; fail.
- [ ] **Step 3: the code.** `apps/inbox/retention.py`:

```python
"""The inbox's retention rule. Loaded by InboxConfig.ready().

Not behind RETENTION_ENABLED, and not behind SMS_INBOX_ENABLED: "texts are kept 365 days,
then deleted" holds wherever texts are stored, and a text stored while the inbox was on must
not outlive that because the switch was later turned off. It asks only whether the tables
exist, because the code is released before they are created."""
from datetime import timedelta

from apps.common import retention
from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.switch import SMS, tables_exist

MESSAGE_LIFETIME = timedelta(days=365)


def inbox_messages(now, dry_run):
    """SMS inbox messages, received and sent, 365 days after they were stored; then any
    conversation left with no message. Rows are deleted, not blanked. The count is messages."""
    if not tables_exist():
        return 0
    rows = ConversationMessage.objects.filter(platform=SMS, created_at__lte=now - MESSAGE_LIFETIME)
    count = retention.remove(rows, dry_run)
    if not dry_run:
        Conversation.objects.filter(platform=SMS, messages__isnull=True).delete()
    return count


retention.register("inbox_messages", inbox_messages)
```

`apps/inbox/apps.py` gains the `ready()` of task 1. `apps/notifications/retention.py`:

```python
DAILY_RULES = ("sms_messages", "mobile_challenges", "inbox_messages")
```

and the docstrings of the module and of `start_daily` say "the SMS rules" in place of "the two SMS rules", naming the third as registered by `apps.inbox`. `apps.notifications` does not import `apps.inbox.retention`: the name is enough, because `run_all` looks rules up in the shared registry.

**This differs from design 11 and 15** ("the rule does nothing while the switch is off"), by review change 1. The rule is started by the phone's poll with the other daily rules, and a phone polls for as long as `SMS_GATEWAY_ENABLED` is on, whatever `SMS_INBOX_ENABLED` says. **What it does not cover:** an environment where the gateway itself is turned off has no poll, so no daily run starts by itself; the runbook says to run the retention command there (task 10).

- [ ] **Step 4:** run the three apps' tests, then the full run. All pass.
- [ ] **Step 5:** commit: `feat(inbox): inbox messages are deleted after 365 days, in the daily rules, switch on or off [z8uq9me128]`.

### Task 9: the command that deletes one number's conversations

**Files:** new `apps/inbox/management/commands/sms_inbox_delete.py`, `apps/inbox/tests/test_delete.py`.

Design 14 point 9: "This build has no delete screen". This command is how a person's request to delete their texts before 365 days is carried out until a screen exists. It must be deployed in an environment before `sms_device incoming on` is run there (6.2); it arrives in the same build as the storing, so it always is.

```text
manage.py sms_inbox_delete --actor USERNAME             count only; the number is typed at the prompt
manage.py sms_inbox_delete --actor USERNAME --confirm   delete
```

The number is typed at the command's prompt and is never an argument (C15). **It is not put into the command line any other way either:** not through `echo`, `printf` or a here-string, each of which leaves it in the shell's history exactly as an argument would. The command reads standard input, so the tests can supply it; that is not advice for an operator. Nothing the command prints, logs or records holds it.

- [ ] **Step 1: the failing tests**, class `DeleteCommandTests(InboxTestCase)`, with texts stored through `storing.store` and the number given by patching `sys.stdin` with an `io.StringIO`:

| Test | Asserts |
|---|---|
| `test_1_without_confirm_it_only_counts` | `0918 999 0000` typed, two texts stored: nothing is deleted. Output "Found: 1 conversation, 2 messages. Nothing was deleted. Run again with --confirm to delete them." One audit entry `sms_inbox_delete`: operator the actor, `target_type` `conversation`, `target_id` blank, `detail` exactly `{"platform": "sms", "change": "count", "conversations": 1, "messages": 2}`, `personal` empty |
| `test_2_with_confirm_it_deletes` | The conversation and its two messages are gone; another number's conversation and messages stay. Output "Deleted: 1 conversation, 2 messages." One entry with `change` `delete` and the same counts |
| `test_3_every_form_of_a_philippine_mobile_matches` | `09189990000`, `639189990000`, `+63 918 999 0000` and `9189990000` each find the conversation of `+639189990000` |
| `test_4_both_phone_lines_are_covered` | The same number has a conversation on each of two phones: both are deleted, `conversations` 2 |
| `test_5_a_number_that_is_not_a_philippine_mobile_matches_by_its_digits` | A conversation stored from `+14155550123` is found by typing `+14155550123`, `+1 415 555 0123`, `+1 (415) 555-0123` and `1.415.555.0123`. One stored from `0281234567` is found by typing `(02) 8123-4567`; one stored from `(02) 8123-4567` is found by typing `0281234567`. Typing `+14155550124` or `4155550123` finds neither: "No conversation is stored for that number.", both counts 0. With `--confirm`, only the matched conversation is deleted |
| `test_5b_digits_never_match_a_name_or_a_philippine_mobile` | A conversation stored from the sender name `BDO1234567` (7 digits, `can_reply` false) is found by typing `1234567`: it is a number by the rule of C13 and is treated as one. A conversation stored from `GLOBE2024` (4 digits) is never found. The conversation of `+639189990000` is not found by typing `+1 639 189 990 000`: a sender that can be replied to is matched only through `normalise_ph_mobile` |
| `test_6_anything_that_is_not_a_full_number_is_refused` | `GLOBE`, `8080`, `0918`, an empty line and a run of 40 digits: `CommandError` "Type one full number.", nothing deleted, no audit entry |
| `test_7_the_number_is_nowhere` | For a count and for a delete: the typed number, in any form, is not in standard output, not in standard error, and not in `json.dumps` of the entry's `detail` and `personal`; `assertNoLogs(level="DEBUG")`. The command has no `--number` option: `call_command("sms_inbox_delete", "--number", "x", ...)` raises |
| `test_8_it_works_while_the_inbox_is_off` | `SMS_INBOX_ENABLED=False`, the table present: `--confirm` deletes, and the entry is written |
| `test_9_with_no_table_it_refuses` | `switch.tables_exist` patched to answer false: `CommandError` "The inbox tables do not exist here, so no text is stored.", `assertNumQueries(0)`, standard input is not read |
| `test_10_a_number_with_no_conversation` | Output "No conversation is stored for that number."; one entry with both counts 0, with and without `--confirm` |
| `test_11_the_actor_must_be_active_staff` | `--actor` missing, unknown, inactive or not staff: `CommandError`, nothing deleted, no entry, standard input is not read |
| `test_12_a_conversation_owned_by_a_site_is_left` | A `site` conversation with the same contact (made in the test) is still there after `--confirm` and is not counted (C17) |
| `test_13_no_inbox_access_is_needed_or_given` | The actor has no `InboxAccess` row; the command works; no row is created |

- [ ] **Step 2:** run; fail (no such command).
- [ ] **Step 3: the code.** `apps/inbox/management/commands/sms_inbox_delete.py`:

```python
"""Delete the SMS inbox conversations of one number: how a person's request to delete their
texts is carried out. Works with SMS_INBOX_ENABLED on or off, wherever the tables exist.

    manage.py sms_inbox_delete --actor USERNAME             count only
    manage.py sms_inbox_delete --actor USERNAME --confirm   delete

The number is typed at the prompt. It is never an argument, and it must not be put into the
command line through echo or a here-string either: all of those leave it in the shell's
history. Nothing this command prints, logs or records holds it.
"""
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import Conversation, ConversationMessage
from apps.inbox.reading import NUMBER_DIGITS, is_a_number
from apps.inbox.switch import SMS, tables_exist
from apps.operators import audit

TARGET = "conversation"
MAX_CONTACT = 32


def _digits(value):
    return "".join(character for character in value if character.isdigit())


def _count(number, one):
    return f"{number} {one}" if number == 1 else f"{number} {one}s"


class Command(BaseCommand):
    help = "Delete the SMS inbox conversations of one number. The number is read from standard input."

    def add_arguments(self, parser):
        parser.add_argument("--actor", help="the staff user running this command, for the audit record")
        parser.add_argument("--confirm", action="store_true", help="delete; without it the command only counts")

    def handle(self, *args, actor, confirm, **options):
        if not tables_exist():
            raise CommandError("The inbox tables do not exist here, so no text is stored.")
        actor = get_user_model().objects.filter(username=actor or "", is_active=True, is_staff=True).first()
        if actor is None:
            raise CommandError("--actor must be an active staff user.")
        self.stderr.write("Type the full number, then Enter:")
        number, digits = self._typed(sys.stdin.readline())
        with transaction.atomic():
            found = self._matching(number, digits)
            messages = ConversationMessage.objects.filter(conversation__in=found).count()
            if confirm:
                Conversation.objects.filter(pk__in=found).delete()  # its messages go with it
            audit.record(
                actor, "sms_inbox_delete", target_type=TARGET,
                detail={
                    "platform": SMS, "change": "delete" if confirm else "count",
                    "conversations": len(found), "messages": messages,
                },
            )
        if not found:
            self.stdout.write("No conversation is stored for that number.")
            return
        counts = f"{_count(len(found), 'conversation')}, {_count(messages, 'message')}"
        if confirm:
            self.stdout.write(f"Deleted: {counts}.")
        else:
            self.stdout.write(f"Found: {counts}. Nothing was deleted. Run again with --confirm to delete them.")

    def _typed(self, typed):
        """What was typed, as a Philippine mobile or else as its digits. The error never
        repeats it."""
        typed = typed.replace("\x00", "").strip()
        try:
            return normalise_ph_mobile(typed), None
        except ValueError:
            pass
        digits = _digits(typed)
        if not NUMBER_DIGITS <= len(digits) <= MAX_CONTACT:
            raise CommandError("Type one full number.")
        return None, digits

    def _matching(self, number, digits):
        """The ids of this number's conversations, locked. A Philippine mobile is stored in
        one form. Any other number is stored as it was received and staff only see it masked,
        so it is matched by its digits, whatever spaces and punctuation either side has."""
        mine = Conversation.objects.select_for_update().filter(platform=SMS, owner_kind=Conversation.Owner.HQ)
        if number is not None:
            return list(mine.filter(contact=number).values_list("pk", flat=True))
        others = mine.filter(can_reply=False).values_list("pk", "contact")
        return [pk for pk, contact in others if is_a_number(contact) and _digits(contact) == digits]
```

What the command does **not** remove, said in the runbook (task 10) so that nobody answers a deletion request with this command alone:

- The copy in the phone's own Messages app (design 14 point 8). That is deleted on the phone.
- The masked number in `sms_inbox_open` audit entries. It is masked, and blanked after one year.
- Anything outside the inbox: after plan 4 the send record of a reply, after plan 6 a contact record. Each of those plans says how its records are deleted for one number.
- A conversation that arrives afterwards. The command deletes what is stored; it does not stop the number from texting again.

- [ ] **Step 4:** run; pass.
- [ ] **Step 5:** commit: `feat(inbox): sms_inbox_delete removes one number's conversations, audited [z8uq9me128]`.

### Task 10: the runbooks

**Files:** `docs/runbooks/sms-gateway.md`, `docs/runbooks/retention.md`.

- [ ] **Step 1:** `sms-gateway.md`: the section "Incoming texts: the switch stays off" is retitled "Incoming texts: the three switches" and rewritten to say, in this order:
  - A text a phone receives is stored only when `SMS_GATEWAY_ENABLED`, `SMS_INBOX_ENABLED` and the phone's own switch are all on. Each is Coach Ed's approval per environment; the phone's switch is his approval each time, **and only after the NPC and privacy wording is amended**.
  - With any of them off, `POST /gateway/v1/received` answers 204 and drops the body unread, and `poll` tells the phone not to forward.
  - What is stored when all are on: the sender's number, the full text, the arrival time and the SIM slot, for 365 days. **Every text the SIM receives is stored**, including the carrier's notices, wrong numbers and one-time codes other services send to that SIM.
  - The order to turn it on: migration applied, variable set, access granted, then the phone's switch.
  - **To stop taking texts in, turn the phone's switch off** (`sms_device incoming off`, audited). **Intake is stopped by the phone's switch, not by unsetting the variable.** Unsetting `SMS_INBOX_ENABLED` hides the inbox from staff and does also stop storing, but it leaves the phone's switch recorded as on and is written to no audit record. Either way the phone deletes at once whatever it had not yet handed over.
  - **What is already stored stays stored when the variable is unset, and is still deleted at 365 days.** The retention rule `inbox_messages` runs wherever the tables exist, whatever the variable says. It is started by the phone's poll; in an environment where `SMS_GATEWAY_ENABLED` is off no phone polls, so run `manage.py run_retention --only inbox_messages` there by hand, or the texts stay past 365 days.
  - **A person asks for their texts to be deleted:** `manage.py sms_inbox_delete --actor USERNAME`, type the number at the prompt, read the count, then the same with `--confirm`. **The number is typed at the prompt and nowhere else: never as an argument, and never fed in through `echo`, `printf` or a here-string, which would leave it in the shell's history.** A Philippine mobile may be typed in any usual form. Any other number (a foreign mobile, a landline) is matched by its digits, so spaces, brackets and dashes do not matter, but the prefix does: try the number as the person wrote it, with and without its country code. It works with the variable on or off. What it does not remove: the copy in the phone's Messages app, the masked number in the audit record (blanked after a year), and a text that arrives afterwards.
  - A text the server cannot store (no sender, for one) is skipped and counted in one log line, `sms_incoming_skipped count=N`, and the phone drops it: it is then only in the phone's Messages app. `sms_incoming_rejected` means the phone sent something that is not a list of texts, which the app never does.
  - The commands: `sms_device incoming on|off`, which refuses `on` while the variable is off; `sms_inbox_access grant|revoke|list`, which refuses while the variable is off; `sms_inbox_delete`. Who is granted access is Coach Ed's decision, per person.
  - There is no screen yet: the staff routes and pages are later plans. No Django admin screen shows a conversation or a text.
  The table row "Incoming texts | off" and line 96 keep their meaning; the sentence "There is no inbox table" is removed.
- [ ] **Step 2:** `retention.md`, in "The rules": one row, `inbox_messages` | SMS inbox messages, received and sent, then any conversation left empty | 365 days after the message was stored. Not behind `RETENTION_ENABLED` **and not behind `SMS_INBOX_ENABLED`**: it runs wherever the inbox tables exist and does nothing where they do not; started daily by the phone's poll, beside `sms_messages`, and included in `manage.py run_retention`. And where the runbook says which audit entries `admin_audit_personal_values` blanks, add the inbox's `sms_inbox_open`.
- [ ] **Step 3:** commit: `docs(runbooks): incoming texts, the inbox retention rule and the deletion command [z8uq9me128]`.

### Task 11: the whole check

- [ ] Run, and report the actual output of each:

```bash
uv run python manage.py test
uv run python manage.py makemigrations --check
uv run python manage.py check
uv run python manage.py check --deploy
```

Expected: the full run passes; no migration is proposed; `check` is clean; `check --deploy` gives the same warnings as `main`.

- [ ] With no variable set, by hand on a local SQLite database with the migration **not** applied: start the server with `SMS_GATEWAY_ENABLED=1` and a local Redis address, add a phone, and send one `received` call with its token. Expected: 204, and no error about a missing table. Then, still with the migration not applied: `manage.py run_retention --dry-run` and `manage.py run_retention --only inbox_messages` each end without an error and report 0 for `inbox_messages`; a `poll` call with the phone's token answers 200; `manage.py sms_inbox_delete --actor <a staff user>` refuses with the "tables do not exist" message. This is the proof that the code can be released before the table exists.
- [ ] Apply the migration locally, set `SMS_INBOX_ENABLED=1`, `sms_device incoming on`, send the same call. Expected: 204 and one row; the same call again, still one row. Made-up number and text only.
- [ ] Then unset `SMS_INBOX_ENABLED` and restart: the same `received` call stores nothing; `manage.py sms_inbox_delete --actor <a staff user>` with the made-up number typed reports 1 conversation, and with `--confirm` deletes it. The number appears in neither output.

## 6. The gate for this plan, and what still needs Coach Ed's approval

### 6.1 The gate: the coordinator's read and the NPC coordinator's review

This plan does not return to Coach Ed ("build until plan 9", 1.1). Once the coordinator has read it and the NPC coordinator has reviewed it, the coordinator merges it and says when to build. What that covers: building tasks 1 to 11 on a build branch, including writing `inbox.0001_initial`; a build PR reviewed by the NPC coordinator before merge; merged by the coordinator on green CI after the overlap check; that merge reaching staging with `SMS_INBOX_ENABLED` unset. The coordinator then runs the one migration on staging under the standing approval of 2026-11-11.

### 6.2 Not asked, each still its own approval

| Item | Whose, and when |
|---|---|
| `SMS_INBOX_ENABLED=1` on staging | Coach Ed, per environment. Never before `inbox.0001` is applied there |
| `sms_device incoming on` on any shared environment | Coach Ed, each time, **and only after the NPC and privacy wording is amended (R1)**, **and never in an environment where `sms_inbox_delete` (task 9) is not deployed**: a person's texts are not stored anywhere they cannot be deleted on request. This is the step that starts storing people's texts. Until then no incoming text is stored on staging or production, whatever is merged |
| `sms_inbox_delete --confirm` on a shared environment | Whoever Coach Ed has answer a person's deletion request, per request. It deletes for good |
| `sms_inbox_access grant` for a named person | Coach Ed names who, and whether they may send |
| Anything on production, the migration included | Coach Ed. Not covered by any standing approval |
| Plans 4 to 9 | Each its own plan-only PR, gated like this one ("build until plan 9") |
| Plans 10 and 11 | Coach Ed, each its own plan and approval |

### 6.3 What the coordinator and the reviewer should know, and what Coach Ed is told

- After this build there is still nothing to look at: no page and no route. The first screen is plan 5.
- When the three switches are on, **every** text the SIM receives is stored, including ones that have nothing to do with NegosyoOnline (design 5.2). Nothing filters them.
- A text the server cannot store is skipped, and the phone then drops it (C4, 3.1). The one known case is a text with no sender (`from` empty), which Android can produce. Such a text never reaches the inbox; one log line counts it.
- Texts stored while the switch was on stay in the table when it is turned off, hidden from staff, until 365 days or until `sms_inbox_delete` removes them (3.2).
- **Three places where this revision differs from the approved design's words**, each by the review: design 5.1 "the inbox tables are never read or written" while off (now two deleting exceptions, 3.2); design 5.2 and 15 "a bad list stores nothing and answers 400" (now C4); design 11 and 15 "the rule does nothing while the switch is off" (now change 1). The design file is not edited by this plan.
- The code has not been run. The design's open point stands: the phone app's behaviour with the switch on, on the real phone, is unproven.

## 7. Review, overlap and order

- **Review:** the NPC coordinator, on this plan (first at `8fa9085`, again on this revised head) and again on the build PR. `config/settings.py` is edited (two lines); design 22.5 asks that the NPC coordinator be told first.
- **Open backend PRs when this plan's PR was opened** (`gh pr list`, 2026-11-11, `main` at `bef2f89`): PR 91 (z8uq9me5qe, the storefront contact call), which changes `apps/notifications/services.py`, `apps/notifications/tests/test_email.py`, four files under `apps/storefront` and `docs/runbooks/storefront.md`. No file of section 2.3 is among them. Plan 4 (reply and compose) will edit `apps/notifications/services.py`; that is for plan 4's overlap check.
- **Open backend PRs when this plan was revised** (`gh pr list`, 2026-11-11, `main` at `59a8b10`): PR 93 (z8uq9me581, the Finance staff routes), which changes `apps/billing/services.py`, `apps/billing/staff.py`, `apps/billing/tests/test_staff_finance.py`, `apps/operators/staff_views.py`, `docs/runbooks/billing.md` and its own plan file. No file of section 2.3 is among them. The builder lists them again before the build PR.
- **Known coming work:** the Finance routes build (plan merged as PR 90) adds lines to `apps/operators/staff_views.py` and works in `apps/billing`. This plan touches neither. It adds four lines to `apps/operators/audit.py`, a file B2a also added lines to; if both are open at once, the first ready merges first and the other updates from `main` and keeps both sides' lines.
- **Order on an environment** (design 12): deploy dark; apply `inbox.0001`; set `SMS_INBOX_ENABLED`; grant access; and only after the wording is amended, the phone's switch. `sms_inbox_delete` is in the first step's deploy, so it is there before any text is.

## 8. What was verified, and what was not

Verified by reading at `bef2f89`: the files in section 1. That `received` drops the body unread today; that `sms_device incoming on` exists and is audited; that `audit.record` refuses an undeclared action and that `admin_audit_personal_values` selects by the `PERSONAL` class; that `run_all` raises on an unknown rule name and runs each rule in its own transaction; that the retention registry is filled from each app's `ready()`; that check constraints here are written with `condition=`; that `admin.site` is the project's one admin site (`apps/operators/admin_config.py`); that `mask_mobile` shows the first 6 and last 4 characters of any value of 10 or more; that `manage.py run_retention` runs every registered rule and takes `--only`; that the repository requires Python 3.12.

Verified by reading the phone app at `b69aec3` (not run): everything in section 3.1.

Not verified:

- No code in this plan was run. Field arguments, the query counts asserted in the tests and the import order between `apps.inbox` and `apps.notifications` are from reading, and the builder reports any that differ.
- That one read of the table names is one query on PostgreSQL and on SQLite, and that it is cheap enough for a daily run (`connection.introspection.table_names()`); asserted in task 8 `test_5d`, from reading.
- That PostgreSQL through this repository's driver refuses a NUL character with an error that would reach the phone as a 500. The removal (C5) is right either way; SQLite, which the tests use, accepts one, so `test_16` asserts the stored values and not an error.
- How often Android gives a received text no originating address (3.1). Not measured on a phone.
- What other values a real text carries that the database would refuse. Only NUL characters are handled; an item that still fails inside the database would fail the whole call with a 500, and the phone would send that list again (3.1). None is known.
- That `get_or_create` on a conversation behaves under two overlapping calls from one phone as reasoned in task 3; it is covered by the lock, not by a concurrent test.
- The exact wording of `docs/runbooks/retention.md` around `admin_audit_personal_values`; the builder reads it before editing.
- Nothing on staging or production. The phone app with the switch on, on a real phone with a real SIM.
- The NPC documents and the privacy page were not read by this session.
