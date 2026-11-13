# Buyer portal z8uq9mbk0m: decisions for Coach Ed (through the Manager)

Companion to `2026-11-13-buyer-portal-z8uq9mbk0m.md` (the plan; section numbers below refer to it). Read against `origin/main` at `02dde96`. Each block: the decision, what was checked, one recommendation, the risk, what is built meanwhile, and words he can say. Blocks A to C are the portal; D, E and F are the three wording items.

Settled by the Manager on 2026-11-13 (relayed by the Supervisor): the Manager holds no O2 draft, so block E proposes one; the Data Deletion fix (block F) is general and minimal and corrects only the false statement.

What the Manager may rule alone under the standing rule (routine, inside approved scope): the plan's design in block A (route exception, sale gate), because Coach Ed's L38, L42, L45 and the 2026-11-13 ruling already settle it. What is his: anything touching privacy and deletion (B, C), legal or customer wording (C to F), and a migration (B4).

---

## A. Build the portal as planned, and when it ends

**Decision.** Approve the plan's design (plan sections 3 to 6) and one consequence of the new backend's clocks.

**What I checked.** `members/api.py:27-31` and `pack/api.py:40-45` answer 404 for an unserved site, so paid buyers lose sign-in and My Orders today. The plan adds a separate router `apps/pack/api_portal.py` (a named exception, nothing loosened), a billing read `deletion_point`, a code-constant sale gate and a banner. No migration, no variable, no policy page.

The new backend's billing differs from the legacy task: suspension also starts the data clocks (`services.py:384-390`, `plan.py:23-24`). So the portal of a suspended site ends at its point too: 90 days after suspension for a site that has paid, 30 days (L75, "day 44") for one that never paid, and no end while no clock exists.

**Recommendation.** A1: approve the design. A2: confirm the portal ends at the billing deletion point for suspended and cancelled sites alike (the task said a suspended site has no end; that was legacy).

**Risk.** If A2 is not confirmed and the legacy rule is kept, a suspended site's buyers would see data after the Subscriber's data is due for deletion, which contradicts L42's "until the deletion point". The Live4More case: its trial ends 2026-11-25 17:07 Manila; unpaid, its buyers' portal would close about 30 days later, once collection is live.

**Built meanwhile.** All of PR 1 (backend portal) and the frontend pages, on the recommended answers. Nothing here is irreversible: no data is deleted.

**Words he could say.** "Approved: build the buyer portal as planned, and it closes at the deletion date for suspended and cancelled sites."

---

## B. What happens to buyer data at the deletion point

**Decision.** B1 to B4 together: the purge scope for the MVP.

**What I checked.**
- B1: the new backend already has an age-based erase of date of birth and usernames, 90 days after the last status change, behind `DOB_ERASE_ENABLED` (`apps/pack/retention.py`, ruling D3, never depends on the subscription). L52 (2026-11-03) says retire any registration-age purge and use one clock, the deletion date. D3 is the later ruling and is the one you just approved to turn on on staging (2026-11-13 batch).
- B2: the legacy task's purge is very large (a run table, a holds table, a Finance resolver, Django admin gates, Xendit key clearing). The new backend has no Django admin and no Xendit. The plan's section 8 keeps the privacy part and defers the rest.
- B3: the legacy sale gate waited on the export constant `PROOF_EXPORT_READY_FROM`. The export has no images today, and the extension task z8uq9mbu0p is not built.
- B4: the buyer email needs a record that says it was sent once. `EmailMessage` has no key, so one small table is needed.

**Recommendation.**
- B1: keep D3 as it is (D3 supersedes L52's retirement for the new backend), and add the service-end purge for everything else, anchored on the deletion date, for every site whether served or not.
- B2: build the purge for date of birth, usernames, delivery addresses (order and member), buyer name on orders, the event free text, checkout tokens, session and audit IP addresses, and the proof senders' details. Defer the holds table, the Finance resolver and admin gates. A legal or fraud hold in the meantime means turning `PACK_PURGE_ENABLED` off.
- B3: do not tie the Pack sale to the export constant. The constant guards only the deletion of proof images: they are deleted only when the export includes them and existed for the site's whole 30-day window, else they stay private and inaccessible (L72, fail closed). Nothing about images is promised until then.
- B4: approve writing `apps/pack/migrations/0005_buyer_notice.py` (one additive table, `PackBuyerNotice`) and running it on staging by the Supervisor, as the standing migration approval allows for an approved plan. Production never.

**Risk.** Without B1 the same data is erased on two different clocks and the Terms-facing story is unclear. Without B3 no Pack sale could open until the export extension ships, blocking Live4More's launch. If the holds table is deferred and a real legal hold appears, the only tool is switching the purge off for every site; the risk is accepted at launch volume and revisited when a hold is real. Proof images may stay in the private store longer than 90 days until the export has them; they are not reachable by any buyer, Subscriber or route.

**Built meanwhile.** Nothing of the purge or the notice. PR 1 does not depend on B. The purge waits on this block plus the switch `PACK_PURGE_ENABLED`, which stays off until he says per environment.

**Words he could say.** "Approved B1 to B4: keep the date-of-birth erase, build the day-90 purge as planned, do not tie the sale to the export, and the buyer-notice table migration may be written and run on staging."

---

## C. The day-76 buyer notice (email and banner)

**Decision.** The exact wording of the transactional email to a paid buyer 14 days before the portal closes, and the banner's second sentence.

**What I checked.** L43, L46, L50, L51 settle channel, timing and the rule that the text never promises what the purge does not do. The task's proposed text named proofs and was for the legacy purge. The new text drops proofs (their deletion is conditional, B3) and uses the new purge's field list (B2).

**Proposed email.** Subject: "Your {store name} order view closes on {date} at {time}."
Body: "{store name} stopped taking orders on {service end date}. You can still sign in to see your Membership Pack order, payment status and delivery status until {time} Philippine time on {date}. After that this view closes. On or shortly after that time, the personal registration and delivery details held for your order (such as your date of birth, usernames and delivery address) are deleted or anonymised. Records that must be kept for legal, payment, accounting, fraud or audit purposes are kept and are not available to you, the store or this page. Please save anything you need before {time} on {date}."
Banner, first sentence only until the purge ships: "This store stopped taking orders on {service end date}. You can view your order here until {time} Philippine time on {date}. After that this page closes." The second sentence (the deletion line, same as the email's) is added only when the purge is on.

**Recommendation.** Approve this text. It says "registration and delivery details", never "your data".

**Risk.** A customer promise that the purge must keep. The email is not enabled before the purge (`PACK_BUYER_NOTICE_ENABLED` stays off until the purge runs). Policy pages are untouched.

**Built meanwhile.** The banner without the deletion line is in PR 1.

**Words he could say.** "Approved: the buyer notice text as written in block C."

---

## D. L70: the text on the Billing-only screen of a suspended or ended site (G4)

**Decision.** The wording of the notice at the bottom of the Billing-only screen when a site is suspended or its service has ended.

**What I checked.** G4 comment 1100430000509626, O2: general and minimal, no export window, no deletion date, no Xendit sentence, a support contact for data requests. The export has no images today, so no proof-image promise. Xendit is not built, so no key to revoke (the task's L69 sentence is dropped). The screen already shows the last date to pay from the Billing overview, so a date here would add a second place to keep right.

**Proposed text (one short paragraph).** "To ask for a copy of your store data, or for any other request about your data, email support@negosyoonline.com from your account email. What happens to your data after the service ends is set out in our Terms of Service and Privacy Policy."

**Recommendation.** Approve as written. Shown only when the site is suspended or ended, in the dash billing screen.

**Risk.** Minimal: it repeats what the Terms say and promises no period or deletion.

**Built meanwhile.** Nothing; the G4-R builder is told the text is pending. Waits on A (O1 in the Manager's note).

**Words he could say.** "Approved: the Billing-only screen text in block D."

---

## E. Service-end email for a cancelled subscription (G4b z8uq9mbbkj)

**Decision.** The text of the one email the owner gets when the subscription reaches service end by cancellation, or by the end of a trial with no payment method. The Manager holds no draft, so this is a proposal to approve.

**What I checked.** O2: general and minimal, no export window, no deletion date, no Xendit sentence, the support contact for data requests. It must not promise anything the export does not do, so it mentions no images and no periods. The send record (once per subscription and `service_end_at`) is G4b's own migration question, not this block.

**Proposed email.** Subject: "Your NegosyoOnline subscription has ended."
Body: "Hello {owner name}, the subscription for {site name} has ended, and the site is now off. You can still sign in and open Billing; if you want the site back, pay from the Billing page, which shows the last date to do so. To ask for a copy of your store data, or for any other request about your data, email support@negosyoonline.com from your account email. What happens to your data after the service ends is set out in our Terms of Service and Privacy Policy. NegosyoOnline."

**Recommendation.** Approve as written. Same sentence about data requests as block D.

**Risk.** Minimal, as in D. If it said a deletion date and the purge was not yet running, it would promise what is not performed; it does not.

**Built meanwhile.** Nothing; G4-R's builder waits for the text.

**Words he could say.** "Approved: the service-end email in block E."

---

## F. Data Deletion page (P10.1 z8uq9mb22t)

**Decision.** The smallest wording change that makes the Data Deletion page true under the 2026-11-02 ruling: a paid subscriber's deletion request is a cancellation at the end of the paid term, with no refund; service continues until then; the 30-day export and 90-day clocks start at service end.

**What I checked.** `apps/site/public/data-deletion.html` (frontend `origin/main`), the live page https://www-staging.negosyonline.com/data-deletion. Two sentences are false for a paid subscriber: step 3 under "How to ask" ("complete the deletion within 30 calendar days") and the first sentence under "What deleting your account ends" ("Deleting your account ends the subscription of every website on it"). The Terms (`terms.html` "Your data after the service ends") and the Cancellation page already state the clocks, so the fix points to them and repeats nothing else.

**Proposed change, two edits.**
1. Step 3 under "How to ask". From: "We will reply within 1 business day to confirm the request, complete the deletion within 30 calendar days, and tell you when it is done." To: "We will reply within 1 business day to confirm the request, complete the deletion within 30 calendar days (for a website in a paid billing cycle or annual term, see “What deleting your account ends” below), and tell you when it is done."
2. First sentence under "What deleting your account ends". From: "Deleting your account ends the subscription of every website on it, and no further renewal is charged." To: "Your request cancels the subscription of every website on your account, and no further renewal is charged. If a website is in a paid billing cycle or annual term, the cancellation takes effect at the end of that cycle or term: the website stays on until then and there is no refund for the unused time. The period to download your store data and the deletion or anonymisation then count from the end of the service, as set out in our Terms of Service."

The rest of the page is unchanged. If wanted, one small word fix in "What we delete" ("are removed with your account" becoming "are removed with your account at the times described here") is optional and left out of the proposal.

**Recommendation.** Approve the two edits. The page lives in the frontend site app; one small frontend change. After it, `docs/runbooks/support-and-exit.md` section 3 (the "30 calendar days" row) gets the same pointer.

**Risk.** Without it the public page contradicts the ruling and the Terms. With it, no new promise is made: it points to the Terms.

**Built meanwhile.** Nothing; a legal page change needs his word.

**Words he could say.** "Approved: the Data Deletion page edits in block F."
