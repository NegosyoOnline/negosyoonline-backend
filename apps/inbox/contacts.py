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
from apps.common.phones import SHORTENED_REASON, is_shortened_by_excel, normalise_ph_mobile
from apps.inbox.models import Contact, ContactGroup, DoNotText, InboxAccess
from apps.inbox.switch import SMS, contacts_on, contacts_tables_exist, dnt_tables_exist, inbox_on
from apps.operators import audit

NAME_MAX = 80
GROUP_NAME_MAX = 60
QUERY_MIN = 2
MAX_GROUPS = 500
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
    "shortened": SHORTENED_REASON,
}


@dataclass(frozen=True)
class ContactScope:
    actor: object
    contacts: QuerySet
    groups: QuerySet
    can_change: bool


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


def _opted_out(numbers):
    """The numbers among `numbers` on the do-not-text list. Empty, with no query, while the
    table does not exist (inbox.0003 not applied)."""
    numbers = list(numbers)
    if not numbers or not dnt_tables_exist():
        return set()
    found = set()
    for start in range(0, len(numbers), CHUNK):
        found.update(
            DoNotText.objects.filter(platform=SMS, mobile__in=numbers[start:start + CHUNK])
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


def _clean(data):
    first = _text(data.get("first_name"), maximum=NAME_MAX, required=True)
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


def list_contacts(scope, *, group=None, query=None, cursor=None, limit=None):
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
    with transaction.atomic():
        items, following = page(
            contacts.prefetch_related("groups"), limit=limit, cursor=cursor or "", prefix="cont"
        )
        if number:
            audit.record(scope.actor, "contact_find", target_type=CONTACT, detail={"found": len(items)})
    listed = _opted_out(contact.mobile for contact in items)
    return [_row(contact, opted_out=listed) for contact in items], following


def open_contact(scope, contact_id):
    contact = (
        scope.contacts.filter(public_id=contact_id).prefetch_related("groups").first()
        if is_public_id(contact_id, "cont") else None
    )
    if contact is None:
        return None
    audit.record(scope.actor, "contact_open", target_type=CONTACT, target_id=contact.public_id)
    return _row(contact, full=True, opted_out=_opted_out([contact.mobile]))


def add_contact(scope, data):
    _may_change(scope)
    first, last, mobile = _clean(data)
    groups = _groups_from(scope, data.get("groups"))
    with transaction.atomic():
        existing = scope.contacts.filter(mobile=mobile).first()
        if existing:
            raise _already(existing)
        try:
            with transaction.atomic():
                contact = Contact.objects.create(
                    owner_kind="hq", first_name=first, last_name=last, mobile=mobile, created_by=scope.actor
                )
        except IntegrityError:
            raise _already(scope.contacts.get(mobile=mobile))
        contact.groups.set(groups)
        audit.record(
            scope.actor, "contact_add", target_type=CONTACT, target_id=contact.public_id,
            detail={"groups": len(groups)},
        )
    saved = Contact.objects.prefetch_related("groups").get(pk=contact.pk)
    return _row(saved, opted_out=_opted_out([saved.mobile]))


def edit_contact(scope, contact_id, data):
    """The changed contact, or None when there is no such contact."""
    _may_change(scope)
    first, last, mobile = _clean(data)
    groups = _groups_from(scope, data.get("groups"))
    with transaction.atomic():
        contact = (
            scope.contacts.select_for_update().filter(public_id=contact_id).first()
            if is_public_id(contact_id, "cont") else None
        )
        if contact is None:
            return None
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
                other = Contact.objects.filter(owner_kind="hq", mobile=mobile).exclude(pk=contact.pk).first()
                if other is None:
                    raise
                raise _already(other) from None
            contact.groups.set(groups)
            audit.record(
                scope.actor, "contact_edit", target_type=CONTACT, target_id=contact.public_id,
                detail={"changed": changed, "groups": len(groups)},
            )
    saved = Contact.objects.prefetch_related("groups").get(pk=contact.pk)
    return _row(saved, opted_out=_opted_out([saved.mobile]))


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
        audit.record(
            scope.actor, "contact_delete", target_type=CONTACT, target_id=contact.public_id,
            detail={"groups": groups},
        )
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
                group = ContactGroup.objects.create(owner_kind="hq", name=name)
        except IntegrityError:
            raise _name_taken(scope.groups.get(name__iexact=name))
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
        audit.record(
            scope.actor, "contact_group_remove", target_type=GROUP, target_id=group.public_id,
            detail={"contacts": held},
        )


# The CSV upload ---------------------------------------------------------------------------


def _data_lines(text):
    """(line number, row) for each data line. The header row and blank lines are dropped here;
    columns after the third are never looked at again."""
    reader = csv.reader(io.StringIO(text.removeprefix("﻿")))
    for number, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        if number == 1 and len(row) >= 3 and not any(character.isdigit() for character in row[2]):
            continue
        yield number, row


def read_file(text):
    """Sort the file's lines. Reads no table. Returns (people, refused, repeated, lines, shortened):
    people {mobile: (first, last)} with the first line winning; refused [(line, reason)];
    repeated, the lines whose number came earlier; lines, every data line; shortened, the
    refused lines whose mobile Excel turned into scientific notation."""
    if not isinstance(text, str):
        raise _invalid(csv="Choose a CSV file.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ApiError("file_too_large", "The file is over 1 MB.", status=413)
    people, refused, repeated, lines, shortened = {}, [], 0, 0, 0
    try:
        for number, row in _data_lines(text):
            lines += 1
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
            if is_shortened_by_excel(row[2]):
                refused.append((number, REASONS["shortened"]))
                shortened += 1
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
    return people, refused, repeated, lines, shortened


def _in_contacts(scope, mobiles):
    """The numbers among `mobiles` that are already contacts."""
    mobiles, found = list(mobiles), set()
    for start in range(0, len(mobiles), CHUNK):
        found.update(scope.contacts.filter(mobile__in=mobiles[start:start + CHUNK]).values_list("mobile", flat=True))
    return found


def _upload_group(scope, group_id):
    if not isinstance(group_id, str):
        raise _invalid(group="Choose a group.")
    return _group_by_id(scope, group_id)


def check_upload(scope, text, group_id):
    """What a save would do. Saves nothing and writes no audit entry."""
    _may_change(scope)
    _upload_group(scope, group_id)
    people, refused, repeated, lines, shortened = read_file(text)
    existing = _in_contacts(scope, people)
    return {
        "lines": lines,
        "new": len(people) - len(existing),
        "existing": len(existing),
        "already_opted_out": len(_opted_out(people)),
        "repeated": repeated,
        "refused": [{"line": number, "reason": reason} for number, reason in refused[:MAX_REFUSED_SHOWN]],
        "refused_count": len(refused),
        "shortened": shortened,
    }


def save_upload(scope, text, group_id, expected_new):
    _may_change(scope)
    if isinstance(expected_new, bool) or not isinstance(expected_new, int):
        raise _invalid(expected_new="Check the file again.")
    with transaction.atomic():
        group = _upload_group(scope, group_id)
        group = scope.groups.select_for_update().get(pk=group.pk)
        people, refused, repeated, lines, shortened = read_file(text)
        existing = _in_contacts(scope, people)
        new = [mobile for mobile in people if mobile not in existing]
        if len(new) != expected_new:
            raise ApiError(
                "changed",
                "The file or the list of contacts is not as it was when it was checked. Check it again.",
                status=409,
            )
        Contact.objects.bulk_create(
            [
                Contact(
                    owner_kind="hq", first_name=people[mobile][0], last_name=people[mobile][1],
                    mobile=mobile, created_by=scope.actor,
                )
                for mobile in new
            ],
            ignore_conflicts=True,
        )
        everyone = list(people)
        for start in range(0, len(everyone), CHUNK):
            group.contacts.add(*scope.contacts.filter(mobile__in=everyone[start:start + CHUNK]))
        counts = {
            "lines": lines, "added": len(new), "existing": len(existing),
            "refused": len(refused), "repeated": repeated, "shortened": shortened,
        }
        audit.record(scope.actor, "contact_upload", target_type=GROUP, target_id=group.public_id, detail=counts)
    return counts
