"""A store's own contacts and groups, under /v1/sites/{site_id}/contacts/ (STORE-CONTACTS, z8uq9mj9q4):

    GET    .../status/                  -> {contacts: true, count, max}
    GET    .../?group=&q=&cursor=       -> {results: [{id, first_name, last_name, mobile_masked, groups, opted_out}],
                                            next, count}   (count: every match; ordered by name, case ignored)
    GET    .../{id}/                    -> the row and the full `mobile`; writes the audit entry `store_contact_opened`
    POST   .../                         {first_name, last_name?, mobile, groups?}  -> 201 the row
    PATCH  .../{id}/                    the same fields, all optional  -> the row
    DELETE .../{id}/                    -> 204
    GET    .../groups/                  -> [{id, name, count}]
    POST   .../groups/                  {name}  -> 201
    PATCH  .../groups/{id}/             {name}
    DELETE .../groups/{id}/             -> 204 (the contacts stay)
    POST   .../upload/check/            {text, group?} -> {new, already, refused: [{line, reason}]}
    POST   .../upload/save/             {text, group?, expected_new} -> 201 the counts

Closed (404, status/ too) while STORE_CONTACTS_ENABLED is off or the tables are not there. The rows are
the signed-in owner's own site's: another site's id is 404, an HQ row is never reachable here and HQ
staff routes never reach these rows. A list never shows a full number. Nothing here logs or audits a
name or a number, and an uploaded file is read from the request and never stored.
"""
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.common.errors import ApiError
from apps.common.masking import mask_mobile
from apps.inbox import contacts
from apps.inbox.switch import store_contacts_on
from apps.inquiries.api_owner import inquiries_site


def contacts_site(request):
    """The router's sign-in step: closed while the store-contacts switch is off, then the
    inquiries owner step (the account, and the site, as the account's own)."""
    if not store_contacts_on():
        raise ApiError("not_found", "Not found.", status=404)
    return inquiries_site(request)


router = Router(tags=["store-contacts"], auth=contacts_site)


class ContactIn(Schema):
    model_config = ConfigDict(extra="forbid")
    first_name: str
    last_name: str | None = None
    mobile: str
    groups: list[str] | None = None


class ContactPatch(Schema):
    model_config = ConfigDict(extra="forbid")
    first_name: str | None = None
    last_name: str | None = None
    mobile: str | None = None
    groups: list[str] | None = None


class GroupIn(Schema):
    model_config = ConfigDict(extra="forbid")
    name: str


class UploadIn(Schema):
    model_config = ConfigDict(extra="forbid")
    text: str
    group: str | None = None


class UploadSave(UploadIn):
    expected_new: int


def _scope(request):
    scope = contacts.site_scope(request.account, request.inquiry_site)
    if scope is None:  # switched off between the sign-in step and here
        raise ApiError("not_found", "Not found.", status=404)
    return scope


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def _list_row(row):
    row = dict(row)
    row["mobile_masked"] = row.pop("mobile")
    return row


# Static paths come before the {id} paths, so "groups" and "status" are never read as an id.


@router.get("/sites/{site_id}/contacts/status/")
def status(request, site_id: str):
    scope = _scope(request)
    return {"contacts": True, "count": scope.contacts.count(), "max": scope.max_contacts}


@router.get("/sites/{site_id}/contacts/groups/")
def groups_list(request, site_id: str):
    return contacts.list_groups(_scope(request))


@router.post("/sites/{site_id}/contacts/groups/", response={201: dict})
def groups_add(request, site_id: str, payload: GroupIn):
    return 201, contacts.add_group(_scope(request), payload.model_dump())


@router.patch("/sites/{site_id}/contacts/groups/{group_id}/")
def groups_rename(request, site_id: str, group_id: str, payload: GroupIn):
    return contacts.rename_group(_scope(request), group_id, payload.model_dump())


@router.delete("/sites/{site_id}/contacts/groups/{group_id}/", response={204: None})
def groups_remove(request, site_id: str, group_id: str):
    contacts.remove_group(_scope(request), group_id)
    return 204, None


@router.post("/sites/{site_id}/contacts/upload/check/")
def upload_check(request, site_id: str, payload: UploadIn):
    answer = contacts.check_upload(_scope(request), payload.text, payload.group)
    return {"new": answer["new"], "already": answer["existing"], "refused": answer["refused"]}


@router.post("/sites/{site_id}/contacts/upload/save/", response={201: dict})
def upload_save(request, site_id: str, payload: UploadSave):
    return 201, contacts.save_upload(_scope(request), payload.text, payload.group, payload.expected_new)


@router.get("/sites/{site_id}/contacts/")
def listing(request, site_id: str, group: str | None = None, q: str | None = None,
            cursor: str | None = None, limit: int | None = None):
    rows, following, total = contacts.list_contacts_by_name(
        _scope(request), group=group or None, query=q, cursor=cursor or "", limit=limit,
    )
    return {"results": [_list_row(row) for row in rows], "next": following, "count": total}


@router.post("/sites/{site_id}/contacts/", response={201: dict})
def add(request, site_id: str, payload: ContactIn):
    row = contacts.add_contact(_scope(request), payload.model_dump())
    return 201, _list_row(row)


@router.get("/sites/{site_id}/contacts/{contact_id}/")
def open_one(request, site_id: str, contact_id: str):
    row = contacts.open_contact(_scope(request), contact_id)
    if row is None:
        raise _not_found()
    return {**row, "mobile_masked": mask_mobile(row["mobile"])}


@router.patch("/sites/{site_id}/contacts/{contact_id}/")
def edit(request, site_id: str, contact_id: str, payload: ContactPatch):
    data = payload.model_dump(exclude_unset=True)
    for name in ("first_name", "mobile"):
        if name in data and data[name] is None:
            raise ApiError(
                "invalid_request", "Some values are missing or invalid.",
                fields={name: ["Enter a value."]},
            )
    if "last_name" in data and data["last_name"] is None:
        data["last_name"] = ""
    row = contacts.edit_contact(_scope(request), contact_id, data, partial=True)
    if row is None:
        raise _not_found()
    return _list_row(row)


@router.delete("/sites/{site_id}/contacts/{contact_id}/", response={204: None})
def remove(request, site_id: str, contact_id: str):
    if contacts.delete_contact(_scope(request), contact_id) is None:
        raise _not_found()
    return 204, None
