# SMS inbox, plan 6: contacts, groups and the CSV upload, backend (2026-11-11)

ClickUp task: z8uq9me128 "SMS inbox, reply and compose (HQ)" (https://app.clickup.com/t/z8uq9me128).

Status: **a plan only. Nothing here is built.** It is the sixth plan of the build order (ClickUp comment 1100430000488202): "6 backend contacts and groups and the CSV upload (migration)". Under Coach Ed's ruling "build until plan 9" (comment 1100430000490204) it does not return to him: the gate is the coordinator's read, the NPC coordinator's privacy review and, for the shared files, the Santé/onboarding coordinator's overlap check (section 8). Everything is built switched off.

**The code in this plan has not been run.** It was written against backend `origin/main` at `e01c979` by reading it. The builder writes each test first, sees it fail, and reports every place where the code below had to change.

## 1. Sources

| Short name | Source |
|---|---|
| Design | `docs/superpowers/specs/2026-11-11-sms-inbox-hq-design.md`, section 19 (contacts and groups), section 9 (masking), section 14 points 16 and 17, section 7.2 (the one access check) |
| Mockups | `docs/superpowers/specs/mockups/2026-11-11-sms-inbox/` `contacts.html`, `contact.html`, `groups.html`, `import.html` (the pages are plan 7; here only for the shape of what they need) |
| Plans 3 and 4 | `docs/superpowers/plans/2026-11-11-sms-inbox-storage-plan.md` (task 9, the delete command) and `...-reply-compose-plan.md` (the route pattern, section 3.1) |
| Task | ClickUp z8uq9me128 and its comments, read 2026-11-11: the build order (1100430000488202), "build until plan 9" (1100430000490204), no Django admin (1100430000489781), the coordinator's records for plans 3 and 4 (as relayed: PR 98 merged as `e01c979`, `notifications.0004` applied on staging) |
| Code | Backend `origin/main` at `e01c979`: `apps/inbox` (`models.py`, `access.py`, `switch.py`, `reading.py`, `staff.py`, `management/commands/sms_inbox_delete.py`), `apps/operators/staff_views.py`, `audit.py`, `apps/common/{phones,masking,ids,pagination,errors}.py`, `config/settings.py` line 102 |

Rulings applied, not reopened: R16 (contacts and groups), R17 (kept until staff delete), R18 (the same inbox staff), R19 (CSV upload into a group), R20 (not used here), R21 (Contacts is its own menu item), R22 (hand-entered only: no link to Subscriber accounts), R23 (the screens call the backend), and no Django admin screen.

## 2. Scope

### 2.1 What is built

| # | Deliverable | Traced to |
|---|---|---|
| 1 | Two records, `Contact` and `ContactGroup`, in `apps/inbox`, and the migration `inbox.0002_contacts` (section 4) | Design 19.9 |
| 2 | A switch of its own, `SMS_CONTACTS_ENABLED`, off by default | Design 14 points 16 and 17 (amend first); the coordinator's rulings (everything off) |
| 3 | `apps/inbox/contacts.py`: the contact, group and CSV functions, all behind one access check | Design 19.2, 19.3, 19.6 |
| 4 | `apps/inbox/contacts_staff.py`: twelve POST staff routes under `/staff/v1/contacts/`, wired into the staff routes by four added lines | Design 19.2; the plan-4 pattern |
| 5 | Nine audit actions, all `KEPT` | Design 19.8 |
| 6 | `sms_inbox_delete` also deletes the contact of the typed number | Plan 3 task 9 (a person's deletion request); design 19.1 (a name stored with a number) |
| 7 | Runbook lines (task 6) | Design 14 |

### 2.2 What is not built here

- Any page: plan 7, in the frontend repository.
- The do-not-text list, `opted_out` in any answer, "opted out" counts in the upload check, and groups as a source for a bulk batch: plan 8. The design's 19.2 and 19.3 mention them; they cannot exist before the list does (section 3.2, C13).
- Merging, exporting, custom fields, a contact's name beside a conversation, any link to Subscriber accounts (design 19.11, R22).
- Any Django admin screen.
- Any Railway variable, any run of a migration, any switch turned on, any access grant, any stored contact in a shared environment, anything on staging or production (section 7).

### 2.3 Files

| File | Change |
|---|---|
| `apps/inbox/models.py` | `Contact`, `ContactGroup`, and two id functions |
| `apps/inbox/migrations/0002_contacts.py` | New (section 4) |
| `apps/inbox/switch.py` | `contacts_on()`, `CONTACT_TABLE`, `contacts_tables_exist()` |
| `config/settings.py` | **One added line**, `SMS_CONTACTS_ENABLED`, beside `SMS_INBOX_ENABLED` (line 102) |
| `apps/inbox/contacts.py` | New |
| `apps/inbox/contacts_staff.py` | New |
| `apps/inbox/management/commands/sms_inbox_delete.py` | Also the contact (task 5) |
| `apps/inbox/tests/test_contacts.py`, `test_contacts_csv.py`, `test_contacts_staff.py` | New |
| `apps/inbox/tests/test_delete.py`, `test_models.py` | Added cases |
| `apps/operators/audit.py` | Nine added lines in `ACTIONS` |
| `apps/operators/staff_views.py` | **Four added lines, none changed** (section 3.1) |
| `apps/billing/tests/test_staff_finance.py`, `apps/inbox/tests/test_staff.py` | **One import and the route-count assertions changed** (section 3.1). The first is the billing coordinator's file |
| `docs/runbooks/sms-gateway.md`, `retention.md`, `migrations.md` | Task 6 |

No file under `config/` other than the one line, no `apps/billing` other than that test file, no `apps/notifications`, and no file in the frontend repository.

## 3. Choices this plan makes, each reported and none hidden

Each is a routine detail inside the approved design, decided as the smallest safe one. The coordinator or Coach Ed may overrule any of them.

### 3.1 The shared file `apps/operators/staff_views.py`

As in plan 4: a route another app owns is registered through `_for_staff`, so the origin, session and method checks stay in that one file. The four lines, added and nothing else changed:

```python
from apps.inbox import contacts_staff
```

after the line `from apps.inbox import staff as inbox_staff`, and

```python
ROUTES.update(
    {path: ("POST", _for_staff(view)) for path, view in contacts_staff.ROUTES.items()}
)
```

after the `ROUTES.update(...)` of the inbox routes.

**Three tests count every staff route and must change with this**, each by the same pattern plan 4 used for the inbox:

- `apps/billing/tests/test_staff_finance.py`, class `FinanceGateTests`: `test_the_routes_are_registered_beside_the_sign_in_routes_and_nothing_else_changed` subtracts `set(contacts_staff.ROUTES)` as well; `test_no_finance_route_takes_the_place_of_another_staff_route` adds `+ len(contacts_staff.ROUTES)`; one import.
- `apps/inbox/tests/test_staff.py`, `test_1_the_routes_are_registered_and_none_takes_another_routes_place`: its count adds `+ len(contacts_staff.ROUTES)`, and it asserts no contacts path is an inbox path.

The contacts routes are kept in their own module and their own route table, not merged into `apps/inbox/staff.py`'s, because that module's tests run every route with the inbox's `platform` body.

### 3.2 The choices

| # | Choice | Why | Source |
|---|---|---|---|
| C1 | **Every route is a `POST` with a small JSON body, and no number, name or id is in an address.** The design says the exact paths are for the plan | The staff route table matches exact paths with one method (plan 4, C1) | Design 19.2; `staff_views.py` |
| C2 | **A separate switch, `SMS_CONTACTS_ENABLED`, as well as `SMS_INBOX_ENABLED`.** Contacts are reachable only when both are on and the staff user has an `InboxAccess` row. With either off every contacts route answers 404 as if it did not exist | Storing a name with a number is the first of its kind (design 19.1) and its wording is amended on its own. One switch for the inbox and another for contacts lets the inbox be turned on without storing a single name. Not set anywhere | Design 14 points 16 and 17 |
| C3 | **Read is any `InboxAccess` row; adding, editing, deleting, uploading and every change to a group need `can_send`.** A read-only user who calls a change route is answered 403 `forbidden` and nothing is audited (as plan 4, C6) | Design 19.6 says this is "this design's reading" and that the coordinator confirms it | Design 19.6 |
| C4 | **Contacts are owned by `hq` only, with the same owner columns and CHECK as `Conversation`** (`owner_kind`, `site`, `account`); the uniqueness is written for `hq` only. Admitting a Site or a Subscriber later adds its own constraints with the code that admits them | Design 19.9 says "owner `hq` (R0: a Site or Subscriber later)". The columns now mean that day needs no column change; the constraints are written when there is something to constrain | Design 19.9; `models.py` |
| C5 | **A contact needs a first name (1 to 80 characters); a last name is optional (up to 80).** Names are stripped; control characters are refused. A contact has no other column | Design 19.2 shows both; 19.11 allows no other field. A first name is required so no row shows as only a number | Design 19.2, 19.11 |
| C6 | **A number already in contacts is refused with 409 `already_exists` and the existing contact's id**, so the page can link to it. Changing a contact's number to one that another contact holds is refused the same way | Design 19.2: "refused with a link to that contact" | Design 19.2 |
| C7 | **A group name is 1 to 60 characters, unique among groups ignoring capitals** (409 `already_exists`). Removing a group removes its links to contacts and nothing else | Design 19.2: "Removing a group keeps its contacts" | Design 19.2 |
| C8 | **Nothing is held between the upload's check and its save.** The page sends the file again at the save with `expected_new` (the count the check showed). The save reads the file again and refuses with 409 `changed` if the new-contact count is not that number. The design's `check_id` (10) is not used | A held check would be a second place that keeps a name and a number outside the contacts table, and a record to expire and delete. Reading twice costs one extra pass over at most 1 MB | Design 19.3 ("read once, in memory, never stored as a file"); design 10 |
| C9 | **Refused lines are returned as line number and reason only**, up to the first 200, with the full count. There is no download route: the design's "can be downloaded" (19.3) is the page's to do from this answer, and the answer holds no name and no number | A refused line may be a person's name with a malformed number; handing it back adds nothing the staff user's own file does not hold. **For the NPC coordinator's review** | Design 19.3 |
| C10 | **File rules.** UTF-8 (a leading byte-order mark dropped), at most 1,048,576 bytes of text, read with Python's `csv` module. Columns: first name, last name, mobile; others are ignored and not read into any value. A first row whose third column has no digit is a header and skipped. A line with fewer than three columns, a blank first name, a name over 80 characters or a number that is not a Philippine mobile is refused with a fixed reason. A number twice in the file is added once; the later line is counted as "repeated in the file", not refused | Design 19.3 for the first rules; the rest are the smallest safe reading of it | Design 19.3 |
| C11 | **An existing number in the file is put in the chosen group and keeps the name it has;** the file's name for it is dropped | Design 19.3, word for word | Design 19.3 |
| C12 | **Find.** A query that is a Philippine mobile (after `normalise_ph_mobile`) finds the contact with that number and writes `contact_find` (count only). Any other query of 2 to 80 characters is a name search (case-insensitive "contains", first or last name) and is not audited. A query of 1 character or none is refused `invalid_request` | Design 19.2 ("find by name or by a full number"); the number lookup is audited as the inbox's is | Design 19.2; plan 3 (`sms_inbox_find`) |
| C13 | **The answers carry no `opted_out` and the upload check no do-not-text count.** Plan 8 adds both, with the list they come from | The list does not exist yet; inventing a field for it now would be an API shape nobody approved | Design 18.5, 19.2 |
| C14 | **The list is paged 20 at a time (100 at most) in the order added, oldest first**, with the cursor of `apps.common.pagination.page`. The groups list is not paged (at most 500 groups; a 501st is refused `group_limit`) | The shared pager is by primary key. A name order is the page's to apply to what it has loaded, or a later change. **For the coordinator** | Design 19.2 |
| C15 | **Names are shown in lists; the full number only on `contacts/open/`**, which is audited (`contact_open`). Edit loads from `open`, so staff see the number they edit | Design 19.2, 19.8 | Design 9, 19.2 |
| C16 | **Deletion of a person's contact.** `sms_inbox_delete` also counts and deletes the contact whose number is typed (task 5), with `--confirm` as for conversations. Contacts and groups are not deleted by any job (R17) and are not in the account export | Plan 3 task 9 says each later plan says how its records are deleted for one number. A contact holds the name and the full number, so a request to delete a person's data must reach it | Design 19.7, 19.9; plan 3 task 9 |
| C17 | **Release before the migration:** a contacts route also answers 404 while `inbox_contact` is not a table in this database (`contacts_tables_exist()`), and the delete command skips the contact step then | The code is deployed before the coordinator runs `inbox.0002` on staging; the routes must not 500 in between. They are off anyway; this makes the order safe even with both switches on | Plan 3 (`tables_exist`) |

### 3.3 What contacts do that is new, said plainly

- **A name is stored together with a mobile number**, kept until a staff member deletes it (R17). No job removes it. This is the first place in the SMS records where it happens (design 19.1). The NPC and privacy wording must cover it before any contact is stored in any environment (design 14 points 16 and 17).
- **The name is never sent anywhere.** No text carries it, no log line and no audit entry holds it, and no answer to a staff user without access shows it.
- **A name in a CSV file is read once, in memory**, and stored only if the file is saved. The check stores nothing. A refused line is not kept.
- **A name is searchable** by "contains" over the contacts table, unaudited (C12). Names are not masked: only numbers are (design 9, 19.2).
- **A conversation does not show a contact's name** and a contact does not link to a conversation (design 19.11).

## 4. The migration

One migration, in `apps/inbox`:

`0002_contacts`: two `CreateModel` operations (`ContactGroup`, `Contact` with its many-to-many to `ContactGroup`), their constraints and one index. It depends on `inbox.0001_initial` and on the migration of `accounts` and `sites` that `0001` already depends on. No `AlterField`, no change to a table that exists, and no data operation. If `makemigrations` writes anything else, the builder stops and reports it.

Writing it is covered by the standing approval for a migration an approved plan specifies. Running it on staging is the coordinator's step after the merge. Production is never covered.

The code can be released before it is applied (C17).

## 5. Tasks

Each step: write the test, run it and see it fail, write the code, run it and see it pass. Run from the repository root with `uv run python manage.py test apps.inbox`.

### Task 1: the switch and the two records

**Files:** `config/settings.py`, `apps/inbox/switch.py`, `apps/inbox/models.py`, `apps/inbox/migrations/0002_contacts.py`, `apps/inbox/tests/test_models.py`.

- [ ] `config/settings.py`, after the `SMS_INBOX_ENABLED` line:

```python
SMS_CONTACTS_ENABLED = os.environ.get("SMS_CONTACTS_ENABLED", "").strip() == "1"
```

- [ ] `apps/inbox/switch.py` gains:

```python
CONTACT_TABLE = "inbox_contact"


def contacts_on():
    return bool(settings.SMS_CONTACTS_ENABLED)


def contacts_tables_exist():
    """True once inbox.0002 is applied to this database."""
    return CONTACT_TABLE in connection.introspection.table_names()
```

- [ ] `apps/inbox/models.py` gains, after `InboxAccess`:

```python
def new_contact_id():
    return new_public_id("cont")


def new_group_id():
    return new_public_id("grp")


class ContactGroup(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_group_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Conversation.Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_group_owner", condition=_OWNER),
            models.UniqueConstraint(
                Lower("name"), name="inbox_group_one_name_per_hq", condition=Q(owner_kind="hq"),
            ),
        ]


class Contact(models.Model):
    public_id = models.CharField(max_length=40, unique=True, default=new_contact_id, editable=False)
    owner_kind = models.CharField(max_length=8, choices=Conversation.Owner.choices)
    site = models.ForeignKey("sites.Site", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey("accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    mobile = models.CharField(max_length=13)          # +639XXXXXXXXX, from normalise_ph_mobile
    groups = models.ManyToManyField(ContactGroup, blank=True, related_name="contacts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(name="inbox_contact_owner", condition=_OWNER),
            models.UniqueConstraint(
                name="inbox_contact_one_number_per_hq", fields=["mobile"], condition=Q(owner_kind="hq"),
            ),
        ]
```

with `_OWNER` the `Q(...)` the `Conversation` check already holds, moved to a module-level name that both use (the only edit to existing lines: `Conversation`'s `condition=` becomes `_OWNER`; its constraint name and meaning are unchanged, so `makemigrations` writes nothing for it. If it does, stop and report). `from django.db.models.functions import Lower` is imported.

- [ ] Tests (`test_models.py`): a contact needs an owner shape the CHECK allows; two contacts with one number are refused for `hq`; two groups whose names differ only in capitals are refused; a group name may repeat across owners (when a Site is later admitted: not tested now); deleting a group keeps its contacts; deleting a contact keeps its groups; `contacts_on()` follows the setting; `contacts_tables_exist()` is true on the test database.
- [ ] `uv run python manage.py makemigrations inbox -n contacts` writes exactly `0002_contacts` with two `CreateModel` operations. `makemigrations --check` is then clean.

### Task 2: the access check, the contact functions and the audit actions

**Files:** `apps/inbox/contacts.py`, `apps/operators/audit.py`, `apps/inbox/tests/test_contacts.py`.

- [ ] `apps/operators/audit.py`: nine lines in `ACTIONS`, each `KEPT`: `contact_open`, `contact_find`, `contact_add`, `contact_edit`, `contact_delete`, `contact_upload`, `contact_group_add`, `contact_group_rename`, `contact_group_remove`. Written as literal names (the audit test requires it).
- [ ] `contacts.py` starts with the scope, the same shape as `inbox.access.Scope`:

```python
@dataclass(frozen=True)
class ContactScope:
    actor: object
    contacts: QuerySet
    groups: QuerySet
    can_change: bool


def contact_scope(actor):
    """What this actor may reach, or None. None while either switch is off, while the tables
    are not there, and for anyone without an InboxAccess row on SMS."""
    if not (inbox_on() and contacts_on() and contacts_tables_exist()):
        return None
    if actor is None or not (actor.is_authenticated and actor.is_active and actor.is_staff):
        return None
    row = InboxAccess.objects.filter(operator=actor, platform=SMS).first()
    if row is None:
        return None
    return ContactScope(
        actor=actor,
        contacts=Contact.objects.filter(owner_kind="hq"),
        groups=ContactGroup.objects.filter(owner_kind="hq"),
        can_change=row.can_send,
    )
```

- [ ] The functions, each taking the scope first and never reaching a row any other way. Every change calls `_may_change(scope)` first, which raises `ApiError("forbidden", "You may not change contacts.", status=403)`; every write is inside `transaction.atomic()` with its audit entry in the same transaction:

| Function | Does |
|---|---|
| `list_contacts(scope, *, group, query, cursor, limit)` | The page of C14. `group` is a group id (`grp_…`; unknown or another owner's: `not_found`). `query` as C12, a number lookup writes `contact_find` with `{"found": n}`. Rows: `{id, first_name, last_name, mobile (masked), groups: [{id, name}]}`. Groups prefetched in one query |
| `open_contact(scope, contact_id)` | The row with the **full** `mobile`, and writes `contact_open` (target `contact`, id, no detail). `None` if not found |
| `add_contact(scope, data)` | Checks (C5), normalises the number, resolves `groups` (a list of ids, at most 50, all must exist), creates, writes `contact_add` `{"groups": n}`. Duplicate number: 409 `already_exists` with `contact` in the answer's fields. Returns the opened shape without a second `contact_open` |
| `edit_contact(scope, contact_id, data)` | Same checks; replaces the groups; writes `contact_edit` `{"changed": ["first_name", "mobile"], "groups": n}` (field names only). Locks the row with `select_for_update`. An edit that changes nothing writes nothing |
| `delete_contact(scope, contact_id)` | Deletes the row and its group links; writes `contact_delete` `{"groups": n}` |
| `list_groups(scope)` | `[{id, name, count}]`, name order, `count` annotated |
| `add_group`, `rename_group`, `remove_group` | C7; audit `contact_group_add` `{}`, `contact_group_rename` `{}`, `contact_group_remove` `{"contacts": n}`; the group's id is the target, its name is nowhere |

The audit entries' `target_type` is `contact` or `contact_group`, `target_id` the public id. None has a `personal` value.

- [ ] Tests: each function against a staff user with and without `can_send`; a staff user with no access row, with the inbox off, with contacts off and with the table missing (patch `contacts_tables_exist`) gets `None` from `contact_scope`; a list never carries a full number and `open_contact` does; `open_contact` writes one entry whose `detail`, `target_id` and every field hold no name and no number; the same check for every action name in the table above (one test loops over a fixed set of made-up values and searches each entry's JSON for them); a duplicate number answers 409 with the other contact's id; a number written differently (`0917…`, `+63 917…`) is the same number; editing to another contact's number is refused and changes nothing; a read-only user's changes are 403 and write no entry and no row; a second group named with other capitals is refused; removing a group keeps its contacts and the count of the others is right; a find by name writes no entry and a find by number writes one with a count and no number.

### Task 3: the CSV check and save

**Files:** `apps/inbox/contacts.py`, `apps/inbox/tests/test_contacts_csv.py`.

- [ ] In `contacts.py`:

```python
MAX_CSV_BYTES = 1_048_576
MAX_REFUSED_SHOWN = 200
REASONS = {
    "columns": "This line has fewer than three columns.",
    "first_name": "The first name is missing or too long.",
    "last_name": "The last name is too long.",
    "mobile": "The mobile number is not a Philippine mobile.",
}


def _lines(text):
    """(line number, first, last, mobile) for each data line. The header row, blank lines and
    columns after the third are dropped here and never looked at again."""
    reader = csv.reader(io.StringIO(text.removeprefix("﻿")))
    for number, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        if number == 1 and len(row) >= 3 and not any(c.isdigit() for c in row[2]):
            continue
        yield number, row


def read_file(text):
    """Sort the file's lines. Pure: reads no table. Returns the people to consider
    ({mobile: (first, last)}, first line wins), the refused lines [(line, reason)], and how
    many lines repeated an earlier number."""
```

  A `csv.Error` (for example an unterminated quote) refuses the whole file with `invalid_file`. A text over `MAX_CSV_BYTES` when UTF-8 encoded is refused `file_too_large`; one that is not a string is `invalid_request`.
- [ ] `check_upload(scope, text, group_id)` : needs `can_change`; resolves the group; calls `read_file`; looks up which mobiles already exist in one query (`mobile__in`, in chunks of 500); returns `{"lines": read + refused, "new": n, "existing": n, "repeated": n, "refused": [{"line": 4, "reason": "..."}, ...first 200], "refused_count": n}`. Writes nothing, **not even an audit entry** (nothing was stored; counts only would be the only content, and the save writes them).
- [ ] `save_upload(scope, text, group_id, expected_new)`: inside `transaction.atomic()`: `read_file`; the same lookup; if the new count is not `expected_new` (an integer) raise 409 `changed` ("The file or the list of contacts is not as it was when it was checked. Check it again."); `Contact.objects.bulk_create(new, ignore_conflicts=True)` with `created_by` the actor; then the contacts of every number in the file are fetched and added to the group in one `group.contacts.add(*contacts)`; writes `contact_upload` target `contact_group`, detail `{"lines": n, "added": n, "existing": n, "refused": n, "repeated": n}`; returns those counts. A race that creates a number between the lookup and the insert makes `ignore_conflicts` skip it; it is then simply "existing" and put in the group.
- [ ] Tests (CSV text written in the test with made-up names and numbers):
  1. Three columns, header row present: the header is skipped, three people are read. Header absent: the first line is read.
  2. A fourth and fifth column are ignored: after a save no stored value contains them.
  3. Refused: two columns; blank first name; 81-character first name; `0917123`; `+14155550123`; empty number. Each carries its line number and fixed reason; the answer's JSON contains none of the file's names or numbers (searched).
  4. A number given as `0917…` in one line and `+63 917…` in another is the same number; the second is "repeated"; the first line's name is stored.
  5. A number already in contacts is not added again, keeps its name, and is put in the group.
  6. The check writes no `Contact`, no group link and no audit entry (counts before and after).
  7. The save with a wrong `expected_new` raises `changed` and writes nothing; with a contact added by hand between check and save, `changed`.
  8. The save stores the new contacts, links all of them, writes one `contact_upload` entry whose JSON holds only the five counts.
  9. A byte-order mark is dropped; a file over 1,048,576 bytes is refused before it is parsed; an unterminated quote is `invalid_file`.
  10. 5,000 lines save in one transaction and a number of queries that does not grow with the lines (`assertNumQueries` bounded by a small constant, chunks of 500 aside).
  11. A read-only user is 403 on both.

### Task 4: the staff routes

**Files:** `apps/inbox/contacts_staff.py`, `apps/operators/staff_views.py`, `apps/billing/tests/test_staff_finance.py`, `apps/inbox/tests/test_staff.py`, `apps/inbox/tests/test_contacts_staff.py`.

- [ ] `contacts_staff.py` follows `apps/inbox/staff.py`: a `_route` decorator that answers 404 when `contact_scope` is `None`, turns an `ApiError` into the error body, and sets `Cache-Control: no-store`; a `_body(request, limit)` that reads a JSON object or `{}`. The body limit is `MAX_BODY = 4096` for every route and `MAX_UPLOAD_BODY = 2_400_000` for the two upload routes only (a 1 MB file whose every character is escaped can double, and Django refuses a body over 2.5 MB before the view). The module imports nothing from `staff_views`.
- [ ] Twelve routes, all `POST`, every id in the body:

| Path | Body | Answer |
|---|---|---|
| `contacts/status/` | none | `{"read": true, "change": can_change}` |
| `contacts/list/` | `group?`, `query?`, `cursor?`, `limit?` | `{"results": [...], "next": cursor or null}` |
| `contacts/open/` | `contact` | the contact with the full number; 404 if none |
| `contacts/add/` | `first_name`, `last_name?`, `mobile`, `groups?` | 201 and the contact |
| `contacts/edit/` | `contact` and the same | 200 and the contact |
| `contacts/delete/` | `contact` | 204 |
| `contacts/groups/list/` | none | `{"results": [{id, name, count}]}` |
| `contacts/groups/add/` | `name` | 201 and the group |
| `contacts/groups/rename/` | `group`, `name` | 200 and the group |
| `contacts/groups/remove/` | `group` | 204 |
| `contacts/upload/check/` | `group`, `csv` | the check's counts |
| `contacts/upload/` | `group`, `csv`, `expected_new` | 201 and the saved counts |

  Find is `contacts/list/` with `query`; there is no separate find route.
- [ ] Errors, as plan 4's: `invalid_request` with `fields` (`first_name`, `mobile`, `name`, `group`, `csv`), `already_exists` (409, with the other record's id in `fields`), `changed` (409), `forbidden` (403), `not_found` (404), `invalid_cursor`, `invalid_file`, `file_too_large` (413), `group_limit`, `unauthenticated` and `second_step_required` (401, from the shared file).
- [ ] `staff_views.py`: the four lines of section 3.1. The two tests of section 3.1 are changed.
- [ ] Tests (`test_contacts_staff.py`): every route answers 404 with either switch off, with the table missing, with no access row, and for an unauthenticated or first-step session 401; every route is `POST` only (a `GET` is 405) and takes the staff origin only; every answer carries `Cache-Control: no-store` (404 and 403 included, as these are the module's own); no path holds a value; no list answer holds a full number and `open` does; the answers of `add`, `edit` and `upload` hold the full number nowhere except `open`; a read-only user reaches `status`, `list`, `open`, `groups/list` and is 403 on the other eight; a body over 4096 bytes is read as empty on every route but the two upload routes, which take one up to 2,400,000; the contacts paths are not any inbox, Finance or sign-in path.

### Task 5: `sms_inbox_delete` also deletes the contact

**Files:** `apps/inbox/management/commands/sms_inbox_delete.py`, `apps/inbox/tests/test_delete.py`.

- [ ] In the command, beside the conversation and send-record steps and inside the same `transaction.atomic()`: when `contacts_tables_exist()` is true and the typed number is a Philippine mobile, count the contacts with that exact `+639…` number (`owner_kind="hq"`), delete them with `--confirm`, and add `"contacts": n` to the `sms_inbox_delete` entry's `detail`. The group links go with the row. The group itself stays.
- [ ] The output gains a count, in the existing style: `"1 conversation, 3 messages, 1 send record, 1 contact"`. "No conversation is stored for that number." is printed only when no conversation, send record or contact is found, and its words become "Nothing is stored for that number." (the existing test that matches the old words changes with it). The number is in neither output, as before.
- [ ] A number that is not a Philippine mobile finds no contact (contacts hold only those).
- [ ] Tests: without `--confirm` a contact is counted and kept; with it, deleted, the groups it was in stay, and the audit entry holds the count and no number or name; a number written four ways finds the same contact; a deletion with contacts off or the table missing still works for conversations; a contact of another number is untouched.

### Task 6: the runbooks

- [ ] `docs/runbooks/sms-gateway.md`: one section "Contacts": the second switch, what it stores (a first name, a last name, a number, groups), that it is kept until a staff member deletes it, that the person's deletion request is `sms_inbox_delete` (it now removes the contact), that no job removes a contact, and that the list of filings and policy pages checked before an incoming switch is turned on gains "before `SMS_CONTACTS_ENABLED` is set in a shared environment: the same list, with contacts' names named".
- [ ] `docs/runbooks/retention.md`: a row "Contacts and groups: kept until staff delete; not deleted by any job; not in the account export".
- [ ] `docs/runbooks/migrations.md`: nothing now. The coordinator adds the History line for `inbox.0002` when it runs it.

### Task 7: the whole check

- [ ] `uv run python manage.py test apps.inbox apps.operators apps.billing apps.notifications` and then the full suite: all pass, with the existing skipped count unchanged.
- [ ] `uv run python manage.py makemigrations --check --dry-run` clean; `uv run python manage.py check` clean; `uv run python manage.py check --deploy` shows only the dev-setting warnings it showed before.
- [ ] `uv run python -m ruff check .` (or the repository's lint command) clean.
- [ ] By hand, on a scratch SQLite database with made-up names and numbers, a local server, `HQ_STAFF_ORIGIN` and `HQ_HOST` set to local names, `SMS_INBOX_ENABLED=1` and an access row with `--send`:
  1. `SMS_CONTACTS_ENABLED` unset: each of the twelve routes answers 404.
  2. `SMS_CONTACTS_ENABLED=1`: add a group; add a contact (`0917…`); the same number written `+63 917…` answers 409 with the first contact's id; the list shows the number masked; `open` shows it whole and one `contact_open` entry exists; rename, edit and delete.
  3. Upload a file of about 750 lines of made-up data with bad lines mixed in: the check lists refused lines by number and reason only; the save with the shown count stores them; a second save of the same file answers 409 `changed`.
  4. With `inbox.0002` **not** applied (migrate `inbox` back to `0001`): every contacts route answers 404 and `sms_inbox_delete` still works. This is the proof of C17.
  5. `sms_inbox_delete` for a made-up number held as a contact, without and with `--confirm`: the counts, and the number in neither output.
- [ ] How the staff session is made locally is reported with the result, as in plan 4.

## 6. What the pages of plan 7 need to know

- The twelve routes, their bodies and answers (task 4), all `POST`, with the staff origin and the HQ cookie.
- The Contacts menu item is shown when `contacts/status/` answers 200. Add, edit, delete, upload and the group buttons are shown when `change` is true.
- A name in a list is shown as sent; the number is masked; the full number comes only from `contacts/open/`.
- Contacts have no opted-out mark and the upload check no do-not-text line until plan 8.
- The upload is two steps with the file sent twice (C8). The page keeps the file in memory between them and sends `expected_new` from the check. A 409 `changed` sends the user back to the check.
- Refused lines come as line number and reason; the page lists them and offers its own download if it wants one (C9).
- The errors: `invalid_request` (`fields`), `already_exists` (409, `fields.contact` or `fields.group` holds the id to link to), `changed` (409), `forbidden` (403), `not_found` (404), `invalid_cursor`, `invalid_file`, `file_too_large` (413), `group_limit`, `unauthenticated` and `second_step_required` (401). The words to show are in the answer's `message`.

## 7. The gate for this plan, and what still needs Coach Ed's approval

### 7.1 The gate

This plan does not return to Coach Ed ("build until plan 9"). Once the coordinator has read it, the NPC coordinator has reviewed it for privacy and the Santé/onboarding coordinator has checked the overlap, the coordinator merges it and says when to build. That covers: building tasks 1 to 7 on a build branch, including writing `inbox.0002_contacts`; a build PR reviewed by the NPC coordinator before merge; merged by the coordinator on green CI after the overlap check; that merge reaching staging with `SMS_CONTACTS_ENABLED` unset. The coordinator then runs the one migration on staging under the standing approval.

For the NPC coordinator in particular: 3.3 (a name is stored with a number, kept until deleted, searchable), C2 (the separate switch), C8 and C9 (nothing held between the check and the save; refused lines returned without their values), C12 (which finds are audited), C16 and task 5 (what a deletion request now removes).

For the billing coordinator: section 3.1, the four lines in `staff_views.py` and the changed assertions in its test file.

### 7.2 Not asked, each still its own approval

| Item | Whose, and when |
|---|---|
| `inbox.0002_contacts` on staging | The coordinator, after the merge, under the standing approval for a migration the plan specifies |
| `SMS_CONTACTS_ENABLED=1` on any shared environment | Coach Ed, per environment, and only after the NPC and privacy wording covers a stored name with a number (design 14 points 16 and 17) |
| Storing any contact in a shared environment | The same |
| `sms_inbox_access grant` for a named person | Coach Ed names who, and whether they may change contacts |
| Uploading a real file of people | Not by this build. The file is Coach Ed's own (design 19.3) |
| `sms_inbox_delete --confirm` on a shared environment | Whoever Coach Ed has answer a person's deletion request, per request |
| Anything on production, the migration included | Coach Ed |
| Plans 7 to 9 | Each its own plan-only PR, gated like this one |

## 8. Review, overlap and order

- **Open backend PRs when this plan was written** (`gh pr list`, 2026-11-11, `main` at `e01c979`): none.
- **Shared files:** `apps/operators/staff_views.py` (four lines) and `apps/billing/tests/test_staff_finance.py` (section 3.1); `apps/operators/audit.py` (nine lines); `config/settings.py` (one line); `docs/runbooks/sms-gateway.md` and `retention.md`. Plan 8 (Bulk SMS) will also edit `audit.py`, `staff_views.py` and `apps/notifications`. If another PR touching any of them is open when the build PR is ready, the first ready merges first and the other updates from `main` and keeps both sides' lines. The builder lists the open PRs again before the build PR.
- **Order on an environment:** deploy dark; apply `inbox.0002`; then, each with its own approval, the NPC and privacy wording, `SMS_CONTACTS_ENABLED`, and the access grant.

## 9. What was verified, and what was not

Verified by reading at `e01c979`: that the staff route table matches exact paths with one method and `_for_staff` passes the staff user of a session that passed both steps; that three tests count every staff route (the two in section 3.1 and the inbox's own); that `Conversation` holds the owner columns and the CHECK reused here; that `normalise_ph_mobile` and `mask_mobile` are the shared helpers; that `audit.record` refuses an undeclared action and a personal value on a `KEPT` one; that `apps.common.pagination.page` orders by primary key and takes a public-id cursor of a stated prefix; that `sms_inbox_delete` runs in one transaction and counts before it deletes.

Not verified: none of the code in this plan has been run. That `makemigrations` writes exactly two `CreateModel` operations and nothing for `Conversation` after the `_OWNER` change. Django's behaviour for a functional `UniqueConstraint(Lower("name"))` with a `condition` on SQLite and on PostgreSQL (both support it; the builder confirms on SQLite and reads the SQL for PostgreSQL). `ignore_conflicts` with the many-to-many add on PostgreSQL. A request body near 2.4 MB through the real server and Railway's proxy (the proxy's own limit is not read here). Anything in a browser, on staging or on production.
