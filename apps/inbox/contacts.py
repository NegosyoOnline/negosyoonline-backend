"""Contacts and groups: a name kept with a Philippine mobile number until HQ staff delete it.

Every function starts from a `ContactScope` and reaches a row no other way. A list never shows
a full number; `open_contact` does, and writes the audit entry in the same transaction. No audit
entry, log line or error holds a name, a group's name or a number.
"""
import csv
import io
import unicodedata
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet
from django.db.models.functions import Lower

from apps.common.errors import ApiError
from apps.common.ids import is_public_id
from apps.common.masking import mask_mobile
from apps.common.pagination import page
from apps.common.phones import normalise_ph_mobile
from apps.inbox.models import Contact, ContactGroup, DoNotText, InboxAccess
from apps.accounts.services import record_event
from apps.inbox.switch import (
    SMS, contacts_on, contacts_tables_exist, dnt_tables_exist, inbox_on, store_contacts_on,
)
from apps.operators import audit

NAME_MAX = 80
GROUP_NAME_MAX = 60
QUERY_MIN = 2
MAX_GROUPS = 500
DEFAULT_PAGE, MAX_PAGE = 20, 100
MAX_STORE_CONTACTS = 20_000
MAX_GROUPS_PER_CONTACT = 50
MAX_CSV_BYTES = 1_048_576
MAX_REFUSED_SHOWN = 200
CHUNK = 500
CONTACT = "contact"
GROUP = "contact_group"
REASONS = {
    "columns": "This line has fewer than three columns.",
    "first_name": "The first name is too long or has odd characters.",
    "last_name": "The last name is too long.",
    "mobile": "The mobile number is not a Philippine mobile.",
}


# What a store's audit entry is called, for each action an HQ staff member's is called by its own name.
STORE_EVENTS = {
    "contact_open": "store_contact_opened",
    "contact_find": "store_contact_found",
    "contact_add": "store_contact_added",
    "contact_edit": "store_contact_edited",
    "contact_delete": "store_contact_deleted",
    "contact_upload": "store_contact_uploaded",
    "contact_group_add": "store_group_added",
    "contact_group_rename": "store_group_renamed",
    "contact_group_remove": "store_group_removed",
}


@dataclass(frozen=True)
class ContactScope:
    """The rows one actor may reach. `owner_kind` is "hq" (a staff user) or "site" (a store owner's
    account, for one site). Every create, count and audit entry takes the owner from here."""

    actor: object
    contacts: QuerySet
    groups: QuerySet
    can_change: bool
    owner_kind: str = "hq"
    site: object = None
    max_contacts: int | None = None  # None: no cap (HQ)
    nameless: bool = False  # a CSV line may be a number alone (a store's)

    @property
    def is_site(self):
        return self.owner_kind == "site"

    def owner(self):
        return {"owner_kind": self.owner_kind, "site": self.site}

    def creator(self):
        return {"created_by_account": self.actor} if self.is_site else {"created_by": self.actor}

    def store_event(self, action, target_id="", detail=None):
        """A store's audit entry, in the caller's transaction, and True; for HQ nothing and False (the
        caller then writes the staff entry). Ids and counts only, never a name or number."""
        if not self.is_site:
            return False
        fields = dict(detail or {})
        if target_id:
            fields["target"] = target_id
        record_event(self.actor, STORE_EVENTS[action], site=self.site.public_id, **fields)
        return True


def site_scope(account, site):
    """What a store owner's account may reach on `site`, or None while the store-contacts switch
    is off or the tables are not there. The caller has already found the site as the account's."""
    if not store_contacts_on():
        return None
    return ContactScope(
        actor=account,
        contacts=Contact.objects.filter(owner_kind="site", site=site),
        groups=ContactGroup.objects.filter(owner_kind="site", site=site),
        can_change=True,
        owner_kind="site",
        site=site,
        max_contacts=MAX_STORE_CONTACTS,
        nameless=True,
    )


def contact_scope(actor):
    """What this actor may reach, or None: while either switch is off, while the tables are not
    there, and for anyone without an inbox access row."""
    if not (inbox_on() and contacts_on() and contacts_tables_exist()):
        return None
    if actor is None or not (actor.is_authenticated and actor.is_active and actor.is_staff):
        return None
    # Only can_send is read: a model query would select can_bulk, a column that exists only
    # once inbox.0003 is applied, and the code is released before it.
    can_send = InboxAccess.objects.filter(operator=actor, platform=SMS).values_list("can_send", flat=True).first()
    if can_send is None:
        return None
    return ContactScope(
        actor=actor,
        contacts=Contact.objects.filter(owner_kind="hq"),
        groups=ContactGroup.objects.filter(owner_kind="hq"),
        can_change=can_send,
    )


def _may_change(scope):
    if not scope.can_change:
        raise ApiError("forbidden", "You may not change contacts.", status=403)


def _invalid(**fields):
    return ApiError(
        "invalid_request", "Some values are missing or invalid.",
        fields={name: [message] for name, message in fields.items()},
    )


def _text(value, *, maximum, required):
    """The stripped text, or None when it is not acceptable."""
    if not isinstance(value, str):
        return None if required or value is not None else ""
    value = value.strip()
    if len(value) > maximum or (required and not value):
        return None
    if any(unicodedata.category(character).startswith("C") for character in value):
        return None
    return value


def _opted_out(numbers, scope=None):
    """The numbers among `numbers` on the do-not-text list (for a store's scope, that store's own
    STOP list). Empty, with no query, while the table does not exist (inbox.0003 not applied)."""
    numbers = list(numbers)
    if scope is not None and scope.is_site:
        return _store_opted_out(scope.site, numbers)
    if not numbers or not dnt_tables_exist():
        return set()
    found = set()
    for start in range(0, len(numbers), CHUNK):
        found.update(
            DoNotText.objects.filter(platform=SMS, mobile__in=numbers[start:start + CHUNK])
            .values_list("mobile", flat=True)
        )
    return found


def _store_opted_out(site, numbers):
    """The numbers among `numbers` that sent this store STOP (SMS-OWNER's per-store list)."""
    from apps.inquiries.models import InquirySmsOptOut

    found = set()
    for start in range(0, len(numbers), CHUNK):
        found.update(
            InquirySmsOptOut.objects.filter(site=site, mobile__in=numbers[start:start + CHUNK])
            .values_list("mobile", flat=True)
        )
    return found


def _row(contact, *, full=False, opted_out=()):
    groups = sorted(contact.groups.all(), key=lambda group: group.name.lower())
    return {
        "id": contact.public_id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "mobile": contact.mobile if full else mask_mobile(contact.mobile),
        "groups": [{"id": group.public_id, "name": group.name} for group in groups],
        "opted_out": contact.mobile in opted_out,
    }


def _group_row(group, count):
    return {"id": group.public_id, "name": group.name, "count": count}


def _group_by_id(scope, group_id):
    group = scope.groups.filter(public_id=group_id).first() if is_public_id(group_id, "grp") else None
    if group is None:
        raise ApiError("not_found", "Not found.", status=404, event="object_not_found", reason="missing")
    return group


def _groups_from(scope, ids):
    if ids is None:
        return []
    if not isinstance(ids, list) or len(ids) > MAX_GROUPS_PER_CONTACT or not all(isinstance(i, str) for i in ids):
        raise _invalid(groups="Choose groups from the list.")
    ids = list(dict.fromkeys(ids))
    found = list(scope.groups.filter(public_id__in=ids))
    if len(found) != len(ids):
        raise _invalid(groups="Choose groups from the list.")
    return found


def _clean(data, first_required=True):
    first = _text(data.get("first_name"), maximum=NAME_MAX, required=first_required)
    last = _text(data.get("last_name"), maximum=NAME_MAX, required=False)
    problems = {}
    if first is None:
        problems["first_name"] = "Enter a first name of up to 80 characters."
    if last is None:
        problems["last_name"] = "Enter a last name of up to 80 characters."
    mobile = None
    try:
        mobile = normalise_ph_mobile(data.get("mobile") if isinstance(data.get("mobile"), str) else "")
    except ValueError as problem:
        problems["mobile"] = str(problem)
    if problems:
        raise _invalid(**problems)
    return first, last, mobile


def _already(existing):
    return ApiError(
        "already_exists", "That number is already a contact.", status=409,
        fields={"contact": existing.public_id},
    )


def _filtered(scope, group, query):
    """(the contacts in `group` that match `query`, the number searched for or None)."""
    contacts = scope.contacts
    if group:
        contacts = contacts.filter(groups=_group_by_id(scope, group))
    number = None
    if query is not None:
        if not isinstance(query, str):
            raise _invalid(query="Enter a name or a full mobile number.")
        query = query.strip()
    if query:
        try:
            number = normalise_ph_mobile(query)
        except ValueError:
            number = None
        if number:
            contacts = contacts.filter(mobile=number)
        elif QUERY_MIN <= len(query) <= NAME_MAX:
            contacts = contacts.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
        else:
            raise _invalid(query="Type at least 2 letters or a full number.")
    return contacts, number


def list_contacts(scope, *, group=None, query=None, cursor=None, limit=None):
    contacts, number = _filtered(scope, group, query)
    with transaction.atomic():
        items, following = page(
            contacts.prefetch_related("groups"), limit=limit, cursor=cursor or "", prefix="cont"
        )
        if number:
            if not scope.store_event("contact_find", detail={"found": len(items)}):
                audit.record(scope.actor, "contact_find", target_type=CONTACT, detail={"found": len(items)})
    listed = _opted_out((contact.mobile for contact in items), scope)
    return [_row(contact, opted_out=listed) for contact in items], following


def list_contacts_by_name(scope, *, group=None, query=None, cursor=None, limit=None):
    """(rows, next cursor, count of every match): ordered by first name, last name, then id, ignoring
    case. The cursor is the public id of the last row returned."""
    contacts, number = _filtered(scope, group, query)
    contacts = contacts.annotate(first_key=Lower("first_name"), last_key=Lower("last_name"))
    total = contacts.count()
    limit = max(1, min(limit if limit is not None else DEFAULT_PAGE, MAX_PAGE))
    ordered = contacts.order_by("first_key", "last_key", "pk")
    if cursor:
        last = ordered.filter(public_id=cursor).first() if is_public_id(cursor, "cont") else None
        if last is None:
            raise ApiError("invalid_cursor", "That page marker is not valid.")
        ordered = ordered.filter(
            Q(first_key__gt=last.first_key)
            | Q(first_key=last.first_key, last_key__gt=last.last_key)
            | Q(first_key=last.first_key, last_key=last.last_key, pk__gt=last.pk)
        )
    with transaction.atomic():
        items = list(ordered.prefetch_related("groups")[: limit + 1])
        more = len(items) > limit
        items = items[:limit]
        if number:
            if not scope.store_event("contact_find", detail={"found": len(items)}):
                audit.record(scope.actor, "contact_find", target_type=CONTACT, detail={"found": len(items)})
    listed = _opted_out((contact.mobile for contact in items), scope)
    rows = [_row(contact, opted_out=listed) for contact in items]
    return rows, (items[-1].public_id if more else None), total


def open_contact(scope, contact_id):
    contact = (
        scope.contacts.filter(public_id=contact_id).prefetch_related("groups").first()
        if is_public_id(contact_id, "cont") else None
    )
    if contact is None:
        return None
    if not scope.store_event("contact_open", contact.public_id):
        audit.record(scope.actor, "contact_open", target_type=CONTACT, target_id=contact.public_id)
    return _row(contact, full=True, opted_out=_opted_out([contact.mobile], scope))


def _lock_site(scope):
    """Serialise a store's writes that count rows, so two at once cannot pass the cap together."""
    if scope.is_site:
        type(scope.site).objects.select_for_update().filter(pk=scope.site.pk).first()


def _room(scope, adding):
    if scope.max_contacts is not None and scope.contacts.count() + adding > scope.max_contacts:
        raise ApiError(
            "contact_limit", f"A store can keep up to {scope.max_contacts:,} contacts.",
            fields={"contacts": [f"A store can keep up to {scope.max_contacts:,} contacts."]},
        )


def add_contact(scope, data):
    _may_change(scope)
    first, last, mobile = _clean(data)
    groups = _groups_from(scope, data.get("groups"))
    with transaction.atomic():
        _lock_site(scope)
        existing = scope.contacts.filter(mobile=mobile).first()
        if existing:
            raise _already(existing)
        _room(scope, 1)
        try:
            with transaction.atomic():
                contact = Contact.objects.create(
                    first_name=first, last_name=last, mobile=mobile, **scope.owner(), **scope.creator()
                )
        except IntegrityError:
            raise _already(scope.contacts.get(mobile=mobile))
        contact.groups.set(groups)
        if not scope.store_event("contact_add", contact.public_id, {"groups": len(groups)}):
            audit.record(scope.actor, "contact_add", target_type=CONTACT, target_id=contact.public_id, detail={"groups": len(groups)})
    saved = Contact.objects.prefetch_related("groups").get(pk=contact.pk)
    return _row(saved, opted_out=_opted_out([saved.mobile], scope))


def edit_contact(scope, contact_id, data, partial=False):
    """The changed contact, or None when there is no such contact. With `partial`, a field that is
    missing from `data` keeps its value (a PATCH); otherwise all of them are replaced."""
    _may_change(scope)
    if not partial:
        first, last, mobile = _clean(data)
        groups = _groups_from(scope, data.get("groups"))
    with transaction.atomic():
        contact = (
            scope.contacts.select_for_update().filter(public_id=contact_id).first()
            if is_public_id(contact_id, "cont") else None
        )
        if contact is None:
            return None
        if partial:
            merged = {
                "first_name": contact.first_name, "last_name": contact.last_name, "mobile": contact.mobile,
                **{key: data[key] for key in ("first_name", "last_name", "mobile") if key in data},
            }
            # a number-only contact (from a CSV file) stays without a name until one is given
            first, last, mobile = _clean(merged, first_required=bool(contact.first_name) or "first_name" in data)
            groups = (
                _groups_from(scope, data["groups"]) if "groups" in data else list(contact.groups.all())
            )
        other = scope.contacts.filter(mobile=mobile).exclude(pk=contact.pk).first()
        if other:
            raise _already(other)
        changed = [
            name for name, value in (("first_name", first), ("last_name", last), ("mobile", mobile))
            if getattr(contact, name) != value
        ]
        if set(contact.groups.values_list("pk", flat=True)) != {group.pk for group in groups}:
            changed.append("groups")
        if changed:
            contact.first_name, contact.last_name, contact.mobile = first, last, mobile
            try:
                with transaction.atomic():
                    contact.save()
            except IntegrityError:  # another contact took the number between the check and the save
                other = Contact.objects.filter(mobile=mobile, **scope.owner()).exclude(pk=contact.pk).first()
                if other is None:
                    raise
                raise _already(other) from None
            contact.groups.set(groups)
            if not scope.store_event("contact_edit", contact.public_id, {"changed": changed, "groups": len(groups)}):
                audit.record(scope.actor, "contact_edit", target_type=CONTACT, target_id=contact.public_id, detail={"changed": changed, "groups": len(groups)})
    saved = Contact.objects.prefetch_related("groups").get(pk=contact.pk)
    return _row(saved, opted_out=_opted_out([saved.mobile], scope))


def delete_contact(scope, contact_id):
    """True when deleted, None when there is no such contact."""
    _may_change(scope)
    with transaction.atomic():
        contact = (
            scope.contacts.select_for_update().filter(public_id=contact_id).first()
            if is_public_id(contact_id, "cont") else None
        )
        if contact is None:
            return None
        groups = contact.groups.count()
        contact.delete()
        if not scope.store_event("contact_delete", contact.public_id, {"groups": groups}):
            audit.record(scope.actor, "contact_delete", target_type=CONTACT, target_id=contact.public_id, detail={"groups": groups})
    return True


def list_groups(scope):
    groups = scope.groups.annotate(count=Count("contacts")).order_by(Lower("name"), "pk")[:MAX_GROUPS]
    return [_group_row(group, group.count) for group in groups]


def _group_name(data):
    name = _text(data.get("name"), maximum=GROUP_NAME_MAX, required=True)
    if name is None:
        raise _invalid(name="Enter a name of up to 60 characters.")
    return name


def _name_taken(existing):
    return ApiError(
        "already_exists", "A group with that name already exists.", status=409,
        fields={"group": existing.public_id},
    )


def add_group(scope, data):
    _may_change(scope)
    name = _group_name(data)
    with transaction.atomic():
        if scope.groups.count() >= MAX_GROUPS:
            raise ApiError("group_limit", "There are as many groups as there can be.")
        existing = scope.groups.filter(name__iexact=name).first()
        if existing:
            raise _name_taken(existing)
        try:
            with transaction.atomic():
                group = ContactGroup.objects.create(name=name, **scope.owner())
        except IntegrityError:
            raise _name_taken(scope.groups.get(name__iexact=name))
        if not scope.store_event("contact_group_add", group.public_id):
            audit.record(scope.actor, "contact_group_add", target_type=GROUP, target_id=group.public_id)
    return _group_row(group, 0)


def rename_group(scope, group_id, data):
    _may_change(scope)
    name = _group_name(data)
    with transaction.atomic():
        group = _group_by_id(scope, group_id)
        group = scope.groups.select_for_update().get(pk=group.pk)
        existing = scope.groups.filter(name__iexact=name).exclude(pk=group.pk).first()
        if existing:
            raise _name_taken(existing)
        group.name = name
        group.save()
        if not scope.store_event("contact_group_rename", group.public_id):
            audit.record(scope.actor, "contact_group_rename", target_type=GROUP, target_id=group.public_id)
        count = group.contacts.count()
    return _group_row(group, count)


def remove_group(scope, group_id):
    _may_change(scope)
    with transaction.atomic():
        group = _group_by_id(scope, group_id)
        group = scope.groups.select_for_update().get(pk=group.pk)
        held = group.contacts.count()
        group.delete()
        if not scope.store_event("contact_group_remove", group.public_id, {"contacts": held}):
            audit.record(scope.actor, "contact_group_remove", target_type=GROUP, target_id=group.public_id, detail={"contacts": held})


# The CSV upload ---------------------------------------------------------------------------


def _data_lines(text, nameless=False):
    """(line number, row) for each data line. The header row and blank lines are dropped here;
    columns after the third are never looked at again. With `nameless`, a line of one column is
    a number alone."""
    reader = csv.reader(io.StringIO(text.removeprefix("﻿")))
    for number, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        if number == 1 and len(row) >= 3 and not any(character.isdigit() for character in row[2]):
            continue
        if number == 1 and nameless and len(row) == 1 and not any(character.isdigit() for character in row[0]):
            continue
        yield number, row


def read_file(text, nameless=False):
    """Sort the file's lines. Reads no table. Returns (people, refused, repeated, lines):
    people {mobile: (first, last)} with the first line winning; refused [(line, reason)];
    repeated, the lines whose number came earlier; lines, every data line."""
    if not isinstance(text, str):
        raise _invalid(csv="Choose a CSV file.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ApiError("file_too_large", "The file is over 1 MB.", status=413)
    people, refused, repeated, lines = {}, [], 0, 0
    try:
        for number, row in _data_lines(text, nameless):
            lines += 1
            if nameless and len(row) == 1:
                row = ["", "", row[0]]  # a number alone: the contact has no name
            if len(row) < 3:
                refused.append((number, REASONS["columns"]))
                continue
            first = _text(row[0], maximum=NAME_MAX, required=False)
            last = _text(row[1], maximum=NAME_MAX, required=False)
            if first is None:
                refused.append((number, REASONS["first_name"]))
                continue
            if last is None:
                refused.append((number, REASONS["last_name"]))
                continue
            try:
                mobile = normalise_ph_mobile(row[2])
            except ValueError:
                refused.append((number, REASONS["mobile"]))
                continue
            if mobile in people:
                repeated += 1
                if not any(people[mobile]) and (first or last):
                    people[mobile] = (first, last)  # the first line with a name gives the name
            else:
                people[mobile] = (first, last)
    except csv.Error:
        raise ApiError("invalid_file", "The file could not be read as a CSV file.")
    return people, refused, repeated, lines


def _in_contacts(scope, mobiles):
    """The numbers among `mobiles` that are already contacts."""
    mobiles, found = list(mobiles), set()
    for start in range(0, len(mobiles), CHUNK):
        found.update(scope.contacts.filter(mobile__in=mobiles[start:start + CHUNK]).values_list("mobile", flat=True))
    return found


def _upload_group(scope, group_id):
    if group_id is None and scope.is_site:
        return None  # a store may save a file's numbers with no group
    if not isinstance(group_id, str):
        raise _invalid(group="Choose a group.")
    return _group_by_id(scope, group_id)


def check_upload(scope, text, group_id):
    """What a save would do. Saves nothing and writes no audit entry."""
    _may_change(scope)
    _upload_group(scope, group_id)
    people, refused, repeated, lines = read_file(text, scope.nameless)
    existing = _in_contacts(scope, people)
    _room(scope, len(people) - len(existing))
    return {
        "lines": lines,
        "new": len(people) - len(existing),
        "existing": len(existing),
        "already_opted_out": len(_opted_out(people, scope)),
        "repeated": repeated,
        "refused": [{"line": number, "reason": reason} for number, reason in refused[:MAX_REFUSED_SHOWN]],
        "refused_count": len(refused),
    }


def save_upload(scope, text, group_id, expected_new):
    _may_change(scope)
    if isinstance(expected_new, bool) or not isinstance(expected_new, int):
        raise _invalid(expected_new="Check the file again.")
    with transaction.atomic():
        _lock_site(scope)
        group = _upload_group(scope, group_id)
        if group is not None:
            group = scope.groups.select_for_update().get(pk=group.pk)
        people, refused, repeated, lines = read_file(text, scope.nameless)
        existing = _in_contacts(scope, people)
        new = [mobile for mobile in people if mobile not in existing]
        if len(new) != expected_new:
            raise ApiError(
                "changed",
                "The file or the list of contacts is not as it was when it was checked. Check it again.",
                status=409,
            )
        _room(scope, len(new))
        Contact.objects.bulk_create(
            [
                Contact(
                    first_name=people[mobile][0], last_name=people[mobile][1],
                    mobile=mobile, **scope.owner(), **scope.creator(),
                )
                for mobile in new
            ],
            ignore_conflicts=True,
        )
        everyone = list(people)
        for start in range(0, len(everyone) if group is not None else 0, CHUNK):
            group.contacts.add(*scope.contacts.filter(mobile__in=everyone[start:start + CHUNK]))
        counts = {
            "lines": lines, "added": len(new), "existing": len(existing),
            "refused": len(refused), "repeated": repeated,
        }
        if not scope.store_event("contact_upload", group.public_id if group else "", counts):
            audit.record(scope.actor, "contact_upload", target_type=GROUP, target_id=group.public_id if group else "", detail=counts)
    return counts
