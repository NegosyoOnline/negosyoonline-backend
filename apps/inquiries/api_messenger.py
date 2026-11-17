"""The direct Messenger inbox's routes.

Owner routes (the session cookie and the site ownership of the other inquiry routes), under
/v1/sites/{site_id}/inquiries/messenger/:
    GET    start/        a browser navigation: 302 to the Facebook Login dialog with a signed state
    GET    connection/   {status, page_id, page_name, connected_at, pick_pending}
    GET    pages/        {items: [{id, name}]} from the server-side pick list (410 pick_expired)
    POST   connect/      {page_id} -> the same shape as connection/
    DELETE connection/   204
Public routes:
    GET    /v1/inquiries/messenger/callback/        the fixed OAuth redirect URI: 302 to the dashboard
    GET    /v1/webhooks/messenger/                  Meta's verification request
    POST   /v1/webhooks/messenger/                  Meta's events (X-Hub-Signature-256)
    POST   /v1/webhooks/messenger/deauthorize/      signed_request
    POST   /v1/webhooks/messenger/data-deletion/    signed_request -> {url, confirmation_code}
    GET    /v1/webhooks/messenger/data-deletion/{code}/

While INQUIRY_MESSENGER_ENABLED (with INQUIRIES_ENABLED) is off, or the Meta app values are not
set, every owner route answers 404. The webhook GET answers while the switch is off (Meta checks it
when the app is set up); a POST is signature-checked, then dropped. The two callbacks work whenever
the app secret is set: a deletion request must be honoured whatever the switch says."""
import hmac
import hashlib
import json
import logging
import re
import secrets

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect
from ninja import Router, Schema
from pydantic import ConfigDict

from apps.accounts import facebook
from apps.accounts.api import signed_in
from apps.accounts.models import Account
from apps.common.errors import ApiError
from apps.common.throttle import hit
from apps.inquiries import api_owner, crypto, messenger, messenger_inbox, outcomes
from apps.inquiries.models import InquiryConnection
from apps.inquiries.switch import messenger_on, stored_tables_exist
from apps.sites import templates
from apps.sites.models import Site
from apps.sites.services import site_for

logger = logging.getLogger(__name__)

STATE_SALT = "inquiries.messenger.state"
STATE_SECONDS = 600
WEBHOOK_MAX_BYTES = 1_000_000
CALLBACK_PATH = "/v1/inquiries/messenger/callback/"
DELETION_PATH = "/v1/webhooks/messenger/data-deletion/"
DELETION_SALT = "inquiries.messenger.data-deletion"
DELETION_MAX_BYTES = 16_384
CHALLENGE_RE = re.compile(r"^[0-9]{1,32}$")
DONE_PATH = "/settings/inbox/"


def _not_found():
    return ApiError("not_found", "Not found.", status=404)


def messenger_site(request):
    """The owner router's sign-in step: closed unless Messenger is on and configured."""
    if not messenger_on() or not messenger.configured():
        raise _not_found()
    return api_owner.inquiries_site(request)


owner = Router(tags=["inquiries"], auth=messenger_site)
public = Router(tags=["inquiries"], auth=None)


class ConnectIn(Schema):
    model_config = ConfigDict(extra="forbid")
    page_id: str


def _redirect_uri(request):
    return request.build_absolute_uri(CALLBACK_PATH)


def _nonce_key(nonce):
    return f"inq:fm:nonce:{nonce}"


# -- owner routes -----------------------------------------------------------------------------

@owner.get("/sites/{site_id}/inquiries/messenger/start/", response={302: None})
def start(request, site_id: str):
    hit("inq-write", request.account.pk, limit=api_owner.WRITE_LIMIT, window=api_owner.WINDOW)
    nonce = secrets.token_urlsafe(16)
    cache.set(_nonce_key(nonce), 1, STATE_SECONDS)
    state = signing.dumps({"s": request.inquiry_site.pk, "a": request.account.pk, "n": nonce}, salt=STATE_SALT)
    return HttpResponseRedirect(messenger.authorization_url(_redirect_uri(request), state))


@owner.get("/sites/{site_id}/inquiries/messenger/connection/")
def connection(request, site_id: str):
    hit("inq-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    api_owner._need_stored()
    return messenger_inbox.connection_out(request.inquiry_site, request.account)


@owner.delete("/sites/{site_id}/inquiries/messenger/connection/", response={204: None})
def disconnect(request, site_id: str):
    hit("inq-write", request.account.pk, limit=api_owner.WRITE_LIMIT, window=api_owner.WINDOW)
    api_owner._need_stored()
    messenger_inbox.disconnect(request.inquiry_site)
    cache.delete(messenger_inbox.pick_key(request.inquiry_site, request.account))
    return 204, None


def _pick(request):
    pick = cache.get(messenger_inbox.pick_key(request.inquiry_site, request.account))
    if not pick:
        raise ApiError("pick_expired", "Connect to Facebook again to pick a Page.", status=410)
    return pick


@owner.get("/sites/{site_id}/inquiries/messenger/pages/")
def pages(request, site_id: str):
    hit("inq-read", request.account.pk, limit=api_owner.READ_LIMIT, window=api_owner.WINDOW)
    return {"items": [{"id": p["id"], "name": p["name"]} for p in _pick(request)["pages"]]}


@owner.post("/sites/{site_id}/inquiries/messenger/connect/")
def connect(request, site_id: str, payload: ConnectIn):
    hit("inq-write", request.account.pk, limit=api_owner.WRITE_LIMIT, window=api_owner.WINDOW)
    api_owner._need_stored()
    site = request.inquiry_site
    pick = _pick(request)
    page = next((p for p in pick["pages"] if p["id"] == payload.page_id), None)
    if page is None:
        raise ApiError("not_in_pick_list", "That Page is not in your list.", status=400)
    taken = InquiryConnection.objects.filter(
        provider=messenger_inbox.PROVIDER, external_page_id=page["id"],
    ).exclude(site=site).exists()
    if taken:  # D5: a Page is connected to one site at a time
        raise ApiError("page_taken", "That Page is already connected to another site.", status=409)
    try:
        token = crypto.decrypt(page["token"])
        encrypted = crypto.encrypt(token)
    except crypto.TokenKeyError:
        raise ApiError("provider_down", "Facebook could not be reached. Try again.", status=502) from None
    result = messenger.subscribe_page(page["id"], token)
    if not result.ok:
        raise ApiError("provider_down", "Facebook could not be reached. Try again.", status=502)
    existing = messenger_inbox.connection_of(site)
    if existing and existing.external_page_id and existing.external_page_id != page["id"] \
            and existing.status == InquiryConnection.ACTIVE:
        messenger_inbox.unsubscribe_row(existing)  # best effort, with the old Page's own token, before it is overwritten
    try:
        row, _ = InquiryConnection.objects.get_or_create(site=site, provider=messenger_inbox.PROVIDER)
        row.status, row.token_encrypted = InquiryConnection.ACTIVE, encrypted
        row.external_page_id, row.label, row.meta_user_id = page["id"], page["name"][:60], pick.get("user_id", "")
        row.save()
    except Exception as error:  # noqa: BLE001 - the unique constraint on the Page decides a race
        from django.db import IntegrityError
        if not isinstance(error, IntegrityError):
            raise
        # no unsubscribe: the Page's subscription now belongs to the site that won the race
        raise ApiError("page_taken", "That Page is already connected to another site.", status=409) from None
    cache.delete(messenger_inbox.pick_key(site, request.account))
    return messenger_inbox.connection_out(site, request.account)


# -- the OAuth callback -----------------------------------------------------------------------

def _back(code=None):
    query = "?messenger=pick" if code is None else f"?messenger=error&code={code}"
    return HttpResponseRedirect(f"{settings.DASHBOARD_URL}{DONE_PATH}{query}")


@public.get("/inquiries/messenger/callback/", response={302: None})
def callback(request, code: str = "", state: str = "", error: str = ""):
    if not messenger_on() or not messenger.configured():
        raise _not_found()
    try:
        data = signing.loads(state, salt=STATE_SALT, max_age=STATE_SECONDS)
        # Bound to the browser that started it: a state link sent to someone else must not work.
        browser = signed_in(request)
        if browser is None or browser.pk != data["a"]:
            return _back("state_invalid")
        account = Account.objects.get(pk=data["a"])
        site = Site.objects.get(pk=data["s"])
        nonce_ok = cache.delete(_nonce_key(data["n"]))  # one use
    except (signing.BadSignature, Account.DoesNotExist, Site.DoesNotExist, KeyError, TypeError):
        return _back("state_invalid")
    if not nonce_ok:
        return _back("state_invalid")
    try:
        site_for(account, site.public_id, template=templates.SANTE)
    except ApiError:
        return _back("state_invalid")
    if error or not code:
        return _back("cancelled")
    token = messenger.exchange_code(code, _redirect_uri(request))
    if not token.ok:
        return _back("provider_down")
    granted = messenger.granted_permissions(token.value)
    if not granted.ok:
        return _back("provider_down")
    if not set(messenger.REQUIRED_PERMISSIONS) <= granted.value:
        return _back("permission_missing")
    who = messenger.user_id(token.value)
    pages = messenger.list_pages(token.value)
    if not who.ok or not pages.ok:
        return _back("provider_down")
    if not pages.value:
        return _back("no_pages")
    try:
        kept = [{"id": p["id"], "name": p["name"], "token": crypto.encrypt(p["access_token"])} for p in pages.value]
    except crypto.TokenKeyError:
        return _back("provider_down")
    cache.set(messenger_inbox.pick_key(site, account), {"pages": kept, "user_id": who.value}, messenger_inbox.PICK_SECONDS)
    return _back()


# -- the webhook ------------------------------------------------------------------------------

def _signature_ok(body, header):
    secret = settings.META_MESSENGER_APP_SECRET
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):].strip().lower())


@public.get("/webhooks/messenger/")
def webhook_verify(request):
    expected = settings.MESSENGER_VERIFY_TOKEN
    given = request.GET.get("hub.verify_token", "")
    challenge = request.GET.get("hub.challenge", "")
    ok = bool(expected) and request.GET.get("hub.mode") == "subscribe" \
        and hmac.compare_digest(given.encode(), expected.encode())
    if not ok:
        return HttpResponse("Forbidden", status=403, content_type="text/plain")
    if not CHALLENGE_RE.match(challenge):
        return HttpResponse("Bad request", status=400, content_type="text/plain")
    return HttpResponse(challenge, content_type="text/plain")


@public.post("/webhooks/messenger/")
def webhook_receive(request):
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > WEBHOOK_MAX_BYTES:
        return HttpResponse("Too large", status=413, content_type="text/plain")
    body = request.body
    if len(body) > WEBHOOK_MAX_BYTES:
        return HttpResponse("Too large", status=413, content_type="text/plain")
    if not _signature_ok(body, request.META.get("HTTP_X_HUB_SIGNATURE_256", "")):
        return HttpResponse("Forbidden", status=403, content_type="text/plain")
    if messenger_on() and stored_tables_exist():
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        messenger_inbox.receive(payload)
    return HttpResponse("EVENT_RECEIVED", content_type="text/plain")


# -- Meta's callbacks -------------------------------------------------------------------------

class DeletionOut(Schema):
    url: str
    confirmation_code: str


class DeletionStatusOut(Schema):
    status: str


def _signed_user(request):
    """The Facebook user id of Meta's signed_request, or an ApiError. 404 until the secret is set."""
    if not settings.META_MESSENGER_APP_SECRET or not stored_tables_exist():
        raise _not_found()
    if len(request.body) > DELETION_MAX_BYTES:
        raise ApiError("payload_too_large", "The request is too large.", status=413)
    try:
        return facebook.parse_signed_request(
            request.POST.get("signed_request", ""), secret=settings.META_MESSENGER_APP_SECRET,
        )
    except facebook.StaleSignedRequest:
        raise ApiError("stale_request", "The request is too old or from the future.") from None
    except facebook.BadSignedRequest:
        raise ApiError("invalid_signature", "The request is not signed by Meta.") from None


@public.post("/webhooks/messenger/deauthorize/")
def deauthorize(request):
    messenger_inbox.deauthorize(_signed_user(request))
    return {}


@public.post("/webhooks/messenger/data-deletion/", response=DeletionOut)
def data_deletion(request):
    user_id = _signed_user(request)
    messenger_inbox.erase_user(user_id)  # the same answer whether or not we knew the user
    code = signing.dumps(secrets.token_hex(8), salt=DELETION_SALT)
    return {"url": request.build_absolute_uri(f"{DELETION_PATH}{code}/"), "confirmation_code": code}


@public.get("/webhooks/messenger/data-deletion/{code}/", response=DeletionStatusOut)
def data_deletion_status(request, code: str):
    try:
        signing.loads(code, salt=DELETION_SALT)
    except signing.BadSignature:
        raise ApiError("not_found", "No such request.", status=404) from None
    return {"status": "completed"}
