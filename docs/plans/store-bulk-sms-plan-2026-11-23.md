# BULK SMS for stores: plan packet (HQ Supervisor, Mon 2026-11-23)

Source: Coach Ed, "no more task to build? because lets proceed with sms bulk sms" (Mon 2026-11-23 ~13:1x, Manager relay).

Built on what exists. No TWC contacts or TWC data are used: a store texts only its own members.

## RULED (Coach Ed, Mon 2026-11-23 ~13:1x HKT, Manager relay, exact): BUILD GO
> D1: DO NOT LIMIT OUR SUBSCRIBER WHO THEY WANT TO TEXT! haiz!!!!
> D2: yes
> D3: 2000 a day
> D4: no link this is prohibited

Applied (Manager reading, routine; plan otherwise as written):
- D1: **"All my members" selected first.** No consent nudges, warnings or extra confirm wording. "Agreed to promo texts" is only an optional filter. Standard exclusions stay (STOP list, unverified, duplicates).
- D2: the store's own paired phone only.
- D3: **default bulk cap 2,000 a day per store** (HQ-editable). Pace 18-25 s and window 8:00-20:00 Manila unchanged (12 h at avg 21.5 s ≈ 2,009/day, so 2,000 is just reachable). Do not change pace/window without the Manager.
- D4: no web links (existing rule kept).
- Order: PROMO-CONSENT BE → bulk BE → bulk FE. STORE_BULK_SMS_ENABLED off. One additive migration. Ed's local preview before release. Production turn-on is a separate ask. NEVER re-ask D1-D4.
Lines below marked "Recommended"/"default 300"/"See D1" are superseded by this section.

## What already exists (main e59fa75)
- **HQ Bulk SMS** (apps/inbox/bulk.py, switch `SMS_BULK_ENABLED`): NegosyoOnline staff send one announcement to a list, from NegosyoOnline's own phone. It sends one text at a time, 18 to 25 s apart, adds " Reply STOP to opt out", has a daily room, can be stopped, and has a batch history. **It stays as it is.**
- **SMS-OWNER** (released): an owner texts one member at a time from the dash Inbox.
  - Caps: 50 a day and 20 an hour per store; 1,000 a day for all stores.
  - Rules: one SMS, 160 plain characters, no web links. Texts start with the store's name.
  - STOP puts the number on the store's own opt-out list. Replies come back to the store's Inbox.
- **SMS line** (released, switched off): a store pairs its own Android phone and SIM, and its texts go out from it. `SMS_SITE_LINES_ENABLED` stays off until the 598401 proof passes (GO Wed Nov 25 02:00), then turns on under the existing L2 ruling.
- **PROMO-CONSENT v1.1** (agreed with SANTE; build starts at the ClickUp reset): the member's `promo_sms_ok`, checkout opt-in, member toggle, STOP clears it, owner members filter.
- **H3** (Tue round): a held store answers `409 site_held` to every owner text.

## The plan
1. **Who sends:** the store owner, from the dash Inbox, under a new button "Text many members". HQ keeps its existing Bulk SMS. Nothing new is built for HQ beyond one cap field (point 5).

2. **Audience:** only the store's own members (`Member` rows of that site) with a verified mobile.
   - The owner picks **"Members who agreed to promo texts"** (the PROMO-CONSENT filter) or **"All my members"**. "All my members" is selected first (D1 ruled).
   - Always left out: numbers on the store's STOP list, unverified mobiles, duplicates.
   - The owner sees how many will get the text and how many are left out, with the reason for each group. No list of numbers is shown or downloaded.
   - No upload of outside numbers, no CSV, no TWC data.

3. **Compose, preview and test:**
   - The SMS-OWNER text rules apply: one SMS, plain characters, no web links (D4), the store's name added in front. The counter shows the room left after the name.
   - **No forced STOP footer** (Coach Ed, 616135). The owner may write one.
   - An optional "Promotional" label (PROMO-CONSENT). It is only a label.
   - A preview shows the exact text as it will arrive.
   - **Send a test to my own phone** sends one text to the owner's account mobile and counts as one.
   - Confirm: "Send to N members from your phone?" with a tick box. One batch at a time per store.

4. **Which phone sends:** the store's own paired phone only (D2). A store without one sees "Pair your phone in Settings → SMS Gateway to text many members."

5. **Limits and rate per store** (D3):
   - One text every 18 to 25 s (about 150 an hour).
   - Sending only between 8:00 and 20:00 Manila time. A batch waits overnight and goes on the next morning.
   - A daily bulk cap per store, **default 2,000** (D3 ruled), separate from the 50-a-day one-to-one cap. HQ can change it on the existing SMS caps fields.
   - These texts do not use NegosyoOnline's 2,000 a day or the 300 kept for codes.
   - The owner can **Stop** a batch at any time.
   - A held (H3) or billing-ended store: a new batch is refused (`site_held` / billing-only), and a running batch ends its remaining texts with that reason.

6. **Cost and who pays:**
   - **Included in the PHP 2,499 subscription. No new NegosyoOnline charge, and no per-message or per-order billing.**
   - The texts go out on the owner's own SIM, so the carrier charges the owner's own load or plan. NegosyoOnline pays nothing per text.
   - The dash says so in one line: "Texts go out from your own phone and SIM. Your carrier's normal SMS rates apply."
   - A new charge would only arise if bulk used NegosyoOnline's number (D2, not recommended).

7. **Delivery and audit:**
   - Batch history in the dash: date, count, sent, not sent (with reasons), stopped. For each recipient: masked mobile, member name and state.
   - The text is kept with the batch for 180 days (as for Inbox SMS), then deleted. Numbers are never in logs.
   - Audit entries without the text or numbers: `store_bulk_confirmed`, `store_bulk_stopped`, `store_bulk_ended`.
   - Replies and STOPs come back to the store's Inbox as today. A STOP also clears `promo_sms_ok`.

8. **Switch:** `STORE_BULK_SMS_ENABLED`, **off by default** everywhere.
   - It works only together with `INQUIRY_SMS_ENABLED`, `SMS_GATEWAY_ENABLED` and `SMS_SITE_LINES_ENABLED`.
   - Off: the routes answer 404 and the button is hidden.
   - Turning it on in production is a separate later ask.

9. **Who builds:** all HQ (the dash Inbox SMS is HQ's). SANTE is not involved beyond the PROMO-CONSENT data.
   - BE: batch model, audience count, test send, confirm, paced feed through the store's line, stop, history, cap field, the held and billing ends.
   - FE: dash Inbox "Text many members" (audience, compose, preview, test, confirm, progress, history), plus the bulk cap field on HQ Subscribers SMS caps.
   - **Merge:** HQ's PROMO-CONSENT "dash members filter" piece is folded into this audience picker, since the dash has no full Members page.

10. **Migrations:** one, additive: `StoreBulkBatch`, `StoreBulkRecipient`, `SmsPolicy.store_bulk_daily_limit`. It follows the PROMO-CONSENT migration and runs on staging under the standing rule.

11. **Estimate:**
    - BE 3 builder-days, FE 2.5.
    - About 3 to 4 calendar days in parallel, after the PROMO-CONSENT BE (first, about 1 day).
    - Local preview for Ed before release.

12. **Dependencies:**
    1. PROMO-CONSENT BE first.
    2. Bulk BE on top of it.
    3. Bulk FE on the bulk BE contract.
    - It can only be **used** once `SMS_SITE_LINES_ENABLED` is on, after the 598401 PASS. Building does not wait for that.

## Decisions for Coach Ed (only the new ones)

**D1: which audience option is selected first?**
- **Recommended:** "Members who agreed to promo texts" first, with "All my members" one tap away. Nothing is blocked.
- **Risk:** with "all" first, stores text members who never agreed by default. That is the store's duty under the new Terms, but carrier complaints block the store's own SIM.
- Reply: **"BULK D1: as recommended"** or **"BULK D1: all members first"**.

**D2: which phone sends?**
- **Recommended:** only the store's own paired phone. Zero cost for NegosyoOnline, and our number and code room stay safe.
- **Risk:** stores without an Android phone can't send bulk. NegosyoOnline's number instead would cost us per text, need a new charge (outside SaaS-only), and put sign-in codes at risk.
- Reply: **"BULK D2: own phone only"**.

**D3: the limits.**
- **Recommended:** 300 a day per store, one text every 18 to 25 s, 8:00 to 20:00 Manila. HQ can change the 300 for all stores.
- **Risk:** carriers can block a SIM that sends many texts fast. Higher numbers raise the owner's risk.
- Reply: **"BULK D3: as recommended"**, or the numbers he wants.

**D4: web links in store texts.**
- **Recommended:** keep the existing no-links rule. Carriers filter link texts from personal SIMs, and it blocks phishing through our stores.
- **Risk:** promotions can't carry a shop link.
- Reply: **"BULK D4: keep no links"** or **"BULK D4: allow links"**. "Allow links" means only the store's own site address, about +0.5 BE day.

All four as recommended: **"BULK SMS plan: all recommended"**.

## ClickUp (if Ed creates it by hand before the reset)
- Title: **BULK SMS for stores: owners text many members from their own phone**
- List: "3 Privacy, NPC, SMS and HQ", parent z8uq9mbreu.
- Description:
  > Owners send one text to many of their own members from the dash Inbox. The text goes out from the store's own paired phone, paced, with a daily cap, only 8:00 to 20:00, and can be stopped. The audience is the store's verified members, optionally only those who agreed to promo texts (PROMO-CONSENT). The store's STOP list is always left out. No forced STOP footer. Included in the subscription, no new charge. Switch STORE_BULK_SMS_ENABLED off by default. Plan: HQ side-notes hq-plan-store-bulk-sms-2026-11-23.md. Build starts on Coach Ed's OK.

Parent z8uq9mbreu. ClickUp subtask pending (MCP limit until ~04:40 Tue). Approval: Coach Ed's D1-D4 ruling, Mon 2026-11-23 ~13:1x HKT, Manager relay (quoted in the RULED section); plan otherwise approved as written.
