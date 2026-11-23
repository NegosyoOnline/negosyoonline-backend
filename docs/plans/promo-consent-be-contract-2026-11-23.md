# PROMO-CONSENT: backend contract (HQ owns the BE), v1, Mon 2026-11-23

Approval: Coach Ed, "Just build it.. again no need to be strict what to text to its customer/subscribers!" (Manager relay, Mon 2026-11-23).
Not strict: consent is a label and a filter only.
- The composer never refuses a text for lack of consent.
- Nothing appends a footer.
- A "Promotional" flag is optional and only a label.

Split agreed with SANTE (~12:2x):
- HQ owns all the BE and the owner dash filter FE.
- SANTE owns the checkout checkbox FE and the member toggle FE.

Base: BE main e59fa75. ClickUp subtasks under z8uq9mbreu: IDs to follow (the ClickUp daily limit is hit until ~Tue 05:00 HKT).

## Switch
`PROMO_SMS_CONSENT_ENABLED`, default off. Railway is not changed by this work.
- Off: every field below is absent from the answers, the checkout ignores `promo_sms_ok`, and the member preference routes answer 404 `not_found`.
- On: as below.

## Model (one migration, under the standing staging migration rule)
On `members.Member`:
- `promo_sms_ok` BooleanField default False
- `promo_sms_at` DateTimeField null: when the current value was last set
- `promo_sms_version` CharField(16) blank: the consent wording version shown when it was set to true, e.g. "2026-11-23"

Additive only. Existing members start at False.

## 1. Pack checkout (storefront, `POST /storefront/v1/checkout/place/`, multipart form)
- New optional form field `promo_sms_ok`: "1" means true. Omitted or any other value means false.
- When the order creates or updates the member:
  - true sets `promo_sms_ok=True`, `promo_sms_at=now`, `promo_sms_version=<current>`.
  - false never clears an existing true. The checkout can only opt in.
- Replays (the same `submit_key`) change nothing.
- The answer is unchanged.

### 1a. The checkout reads the flag first (v1.1, SANTE's request, agreed)
- The `POST /storefront/v1/checkout/contact/` answer gains three fields:
  - `promo_sms_consent_enabled: bool`
  - `promo_sms_version: str|null`: the current version when on, null when off
  - `promo_sms_text: str|null`: the exact checkbox wording when on, null when off
- The FE shows the checkbox only when `promo_sms_consent_enabled` is true, and renders `promo_sms_text` exactly as given.
- The wording lives in one BE constant, with `{store}` filled from the site's display name:
  "Send me promotions from {store} by text. Reply STOP anytime."
  Version "2026-11-23". Any change to that wording must bump the version, and a test pins the pair.
- `promo_sms_version` stored on a member is the version that was current when they said yes.

## 2. Member preferences (storefront member area, same style as `/member/address/`)
- `POST /storefront/v1/member/preferences/read/` body `{host, visitor_ip, member_token}`
  - 200 `{promo_sms_ok: bool, promo_sms_at: iso|null}`
- `POST /storefront/v1/member/preferences/` body `{host, visitor_ip, member_token, promo_sms_ok: bool}`
  - 200, same shape as read. It sets the value, `promo_sms_at=now`, and the version when true.
- Errors as for the other member routes: 401 or 403 with the existing session codes; 404 `not_found` when the switch is off.
- Held stores (H3): the member routes stay open, like the other member writes.

## 3. STOP
When a STOP reaches a store (`apps/inquiries/sms_inbox.store_incoming`, stop=True), every member of that site with that mobile gets `promo_sms_ok=False`, `promo_sms_at=now`. The store's opt-out list is unchanged.

## 4. Owner API (dash)
- `GET /v1/sites/{site_id}/members/` rows gain `promo_sms_ok: bool`.
- New query param `promo=1` returns only members with `promo_sms_ok=True`.
  - With `promo=1`, `q` may be empty, to list all agreed members, 20 per page, newest first.
  - The cursor param follows the existing list style, if any. Otherwise `page`.
- Nothing is masked further than today.

## 5. Not in scope
- No gate in the SMS composer or bulk send.
- No footer.
- No HQ-side change.
- No email consent.

## FE owners
- SANTE: the checkout checkbox (posts `promo_sms_ok=1`) and the member dashboard toggle (the two routes in section 2).
- HQ: the dash members filter or tag (section 4).

## v1.2 (Mon 2026-11-23, SANTE request, HQ routine decision)
Reason: the Pack checkout form is server-rendered before any `checkout/contact/` call, so a first-time buyer would never see the opt-in.
- `GET /storefront/v1/pack/?host=` gains the same three fields as `checkout/contact/`: `promo_sms_consent_enabled`, `promo_sms_version`, `promo_sms_text`. Same constant, version pin and `{store}` (this host's display name), from one function (`promo.contact_fields`). `checkout/contact/` is unchanged.
- Switch-off clarification: with `PROMO_SMS_CONSENT_ENABLED` off, the three fields are OMITTED (the keys are absent, not null) from `checkout/contact/` and `pack/`, and `promo_sms_ok` is omitted from owner member rows (and `promo=1` is ignored). The "null when off" wording in section 1a means absent.
- A later choice wins: a checkout yes that is applied at payment (Xendit) never overrides a STOP or a preferences change made after the order was placed.

---
Parent z8uq9mbreu. ClickUp subtask pending (MCP limit until ~04:40 Tue). Approval: Coach Ed, rulings 616135 / "Just build it.. again no need to be strict what to text to its customer/subscribers!" (Manager relay, Mon 2026-11-23).
