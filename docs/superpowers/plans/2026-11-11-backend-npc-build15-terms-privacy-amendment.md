# NPC Build 15: Terms and Privacy amendment plan (task z8uq9me88a)

**Status: a plan only. Nothing is edited, built, committed to the frontend, migrated, seeded or live.** Written 2026-11-11 by the builder for the coordinator and Coach Ed. The page edits are a separate build task, opened after Coach Ed approves this plan.

- Task: https://app.clickup.com/t/z8uq9me88a (parent z8uq9mbreu). No comments on the task.
- Pages read: frontend `origin/main` at `2b84de4` (fetched 2026-11-11): `apps/site/public/privacy.html`, `apps/site/public/terms.html`. The shared frontend checkout is 27 commits behind `origin/main` and still shows the older SMS sentence; the lines below are from `origin/main`. Read only; nothing in the frontend repository was touched.
- Code read: backend `origin/main` `52d8b6d` (inbox models, storing, retention, settings, billing models, proofs, runbooks, SMS plans 4, 6, 8).
- Siblings compared: Builds 11, 12, 13, 14 (notes folder). Build 8 is known only through Build 14's quotation of it (ruling 2026-11-07: "the NPC wording is the launch state"; "The Effective Date and version stay unchanged until launch day"); I did not find the Build 8 file.

## 1. Rulings applied (not reopened)

| Ruling | Source |
|---|---|
| Bare compliance: general, minimal, fix false statements, do not itemise features | Coach Ed 2026-11-11, task |
| Words: admin, subscribers, the subscriber's customers. Never merchant, member, shopper, client | Parent, task |
| Launch Promo terms stay out of the legal pages | Coach Ed 2026-11-10, task |
| `SMS_BULK_ENABLED` and `SMS_CONTACTS_ENABLED` stay off until the wording is live and he approves | Task |
| Do-not-text list has no removal path; Privacy gets one sentence: "If you text STOP we keep your number so we do not text you again." | Coordinator brief; bulk plan C17 |
| Incoming texts may be stored only after the wording is amended | Inbox storage plan R1 |
| Bulk texts to numbers that never wrote to us: the announcement exception, only after the wording covers it | Bulk plan R11 |
| Effective Date and policy version stay unchanged until launch day | Build 8, as quoted in Build 14 section 6 |

## 2. What the pages say now, and what is false

Privacy (`privacy.html`), line numbers on `origin/main`:

| Line | Section | Now | Verdict after the inbox, contacts, bulk SMS and bank transfer |
|---|---|---|---|
| 33 | 3 Information We Collect, SMS item | "...the mobile number a text was sent to, why it was sent, whether it was sent, and when. We do not keep the text of the message on our servers; the phone that sends the text keeps a copy for up to one year." | **False** once the inbox stores text (code: `ConversationMessage.body`, kept 365 days) |
| 33 | same list | no item for names kept with numbers | **Incomplete**: `Contact` stores first name, last name, mobile |
| 34 | after list 3 | "...we keep only the payment result and reference needed for your billing records." | **False for a bank transfer**: `BankTransferPayment` also keeps the proof file, the transfer date and the bank's reference number |
| 37 | 4 How We Use Information | "Send transactional text messages (SMS) for Subscribers and to Subscribers, such as sign-in codes, order and sign-up updates, and security notices. These are not marketing messages." | **False once bulk SMS is on**: the announcement text goes to numbers that never wrote to us |
| 56 | 8 Retention list, SMS item | "Text-message (SMS) records: 1 year, then deleted. We do not keep the text of the message on our servers; the phone that sends the text keeps a copy for up to one year." | **False** (same sentence, second place). The "1 year" is true: the inbox rule deletes messages 365 days after storing (`apps/inbox/retention.py`) |
| 56 | same list | no line for the do-not-text list | **Incomplete**: the list is kept with no deletion path (bulk plan C17) |

Terms (`terms.html`):

| Section | Now | Verdict |
|---|---|---|
| Billing Cycles, Renewal, and Failed Payments | "...your subscription renews automatically, and the payment for each new cycle is collected from your payment method..." (monthly and annual) | **Not true for a bank transfer**: nothing is collected; a Subscriber pays and sends proof, and an admin confirms (billing runbook, B2a). Launch payment is bank transfer (memory: launch payment is bank transfer; Xendit disapproved) |
| Subscription, Fees, and Usage Charges | "The standard ... Monthly Plan is PHP 2,499 per month ... unless a different written plan applies." | **Already covers a promotional price.** True as it stands |
| Everything else read (SMS allowance "free transactional SMS notifications (up to 6 SMS messages per processed order)", Data Processing, Third-Party Services) | | True. Order and sign-in code texts are still not stored: `SmsMessage` never holds the text (`apps/notifications/models.py:51`), the inbox is the admin's own (`owner_kind` HQ in `storing.py:27`), not a subscriber's customers' data |

## 3. The lines (sentences to remove or add)

Each "Old" is exact text from `origin/main`; each "New" replaces exactly that text and nothing else on its line. Every edit stays inside one HTML line, so no line number moves. "Gates" names what the line unlocks; section 4 has the table.

### L1 and L2. Stored texts, contact names and numbers (`privacy.html` line 33). Removes the false sentence, adds the one general line

- Old: `<li>Text-message (SMS) records: the mobile number a text was sent to, why it was sent, whether it was sent, and when. We do not keep the text of the message on our servers; the phone that sends the text keeps a copy for up to one year.</li>`
- New: `<li>Text-message (SMS) records: the mobile number, why a text was sent, whether it was sent, and when. If you text us, or we text you from our inbox, we also keep the text of the message.</li><li>Names and mobile numbers in our contact lists, kept until we delete them.</li>`
- Trace: L1 removes task item 1 (first of two places). The inbox keeps `body` for received, reply and bulk texts (inbox plans; `models.py` `ConversationMessage.body`). Contacts: contacts plan R17, "kept until a staff member deletes it, no job removes it". "From our inbox" keeps the line true: sign-in code and order texts are still not stored.
- Words: "we" is used because the page speaks as NegosyoOnline; no forbidden word.
- Gates: `SMS_INBOX_ENABLED` (L1 and the first sentence of L2), `SMS_CONTACTS_ENABLED` (the second `<li>`).

### L1 again and L3. The retention line (`privacy.html` line 56). Removes the false sentence in the second place, adds the STOP sentence

- Old: `<li>Text-message (SMS) records: 1 year, then deleted. We do not keep the text of the message on our servers; the phone that sends the text keeps a copy for up to one year.</li>`
- New: `<li>Text-message (SMS) records, including the text of the message: 1 year, then deleted. If you text STOP we keep your number so we do not text you again.</li>`
- Trace: 365-day inbox rule (`apps/inbox/retention.py`, `MESSAGE_LIFETIME`); bulk batches also 365 days (bulk plan task 8); do-not-text list "not touched by any rule" (bulk plan C17). The STOP sentence is Coach Ed's own wording, used word for word.
- Gates: `SMS_INBOX_ENABLED` (the 1-year, including the text); `SMS_BULK_ENABLED` (the STOP sentence, because only the bulk build creates the list).
- Decision D-A below (the gateway-phone copy).

### L4. Staff may text numbers that never wrote to us (`privacy.html` line 37, section 4)

- Old: `<li>Send transactional text messages (SMS) for Subscribers and to Subscribers, such as sign-in codes, order and sign-up updates, and security notices. These are not marketing messages.</li>`
- New: `<li>Send transactional text messages (SMS) for Subscribers and to Subscribers, such as sign-in codes, order and sign-up updates, and security notices. We may also text announcements about NegosyoOnline to mobile numbers we hold, including numbers that have never texted us. Each of these texts says how to opt out.</li>`
- Trace: bulk plan R11 (announcement exception, after the wording is amended), R12 (admin supplies the numbers), C9 (the server adds " Reply STOP to opt out" to every bulk text, so "says how to opt out" is true). The deleted sentence "These are not marketing messages." would be false.
- Words: "announcements", not "marketing", is the word the ruling uses (R11); the page does not describe the texts as marketing or as not marketing.
- Gates: `SMS_BULK_ENABLED`.
- Legal question this plan cannot settle (D-B below).

### L5. Payment by bank transfer with proof of payment

Two pages, one sentence each, both general.

**Privacy, `privacy.html` line 34** (the paragraph that ends "...not when your browser returns to our website.")

- Old (end of the paragraph): `A payment counts as successful only when our payment provider confirms it to us directly, not when your browser returns to our website.</p>`
- New: `A payment counts as successful only when our payment provider confirms it to us directly, not when your browser returns to our website. If you pay by bank transfer, we also keep the proof of payment you send us, with the transfer date and the bank's reference number.</p>`
- Trace: `BankTransferPayment` (`transferred_on`, `transfer_reference`, `proof_key`; rows are never deleted or blanked); proofs are private files with no URL (`apps/billing/proofs.py`). The retention line already sets "Billing and tax records ... at least 5 years", which covers it: no new retention line (D-C).
- Gates: `BILLING_BANK_TRANSFER_ENABLED`.

**Terms, `terms.html`, section "Billing Cycles, Renewal, and Failed Payments"**, one new paragraph after the first two paragraphs (the monthly and the annual one)

- New (added, nothing removed): `<p>If you pay by bank transfer instead, the payment is not collected automatically. Send us proof of payment; the payment counts, and the cycle or term starts, when we confirm that we received the full amount.</p>`
- Trace: bank-transfer runbook: only a confirmation settles a cycle, the amount received must equal the amount due (`billing_transfer_confirmed_in_full`), no automatic debit; Terms "renews automatically ... collected" is otherwise untrue for the launch method. "Cycle starts when confirmed" follows the B2a plan (a confirmed payment after a suspension starts a new cycle at the payment); paying during the trial keeps the trial days (B2a question 10, built as recommended, pending Coach Ed): the sentence says "starts ... when we confirm" only about a cycle that the payment starts, which is true in both readings. If Coach Ed answers B2a question 10 differently, this line is rechecked.
- Gates: `BILLING_BANK_TRANSFER_ENABLED`.

### L6. Promotional offers: no edit (recommended), optional general line

- Terms already say "unless a different written plan applies", and the Launch Promo is a written plan of its own. Nothing on either page is false. **Recommendation: add nothing.** This also keeps to the ruling that Launch Promo terms stay out of the legal pages.
- If Coach Ed wants a line anyway, the smallest is, in Terms "Subscription, Fees, and Usage Charges" after the first paragraph: `<p>We may offer a promotional plan or price from time to time. Its terms are shown when it is offered.</p>` It names no promo, price or period.
- Gates: `BILLING_LAUNCH_PROMO_ENABLED` is not gated by this line under the recommendation; under the option it is gated by it.

### Not edited, checked and left (to keep the change minimal)

| Place | Why left |
|---|---|
| Privacy section 7, carrier item "(sign-in codes and order-status texts)" | The list says the carrier "delivers our text messages"; the brackets are examples, not a limit. Not false. Itemising bulk there would be a feature list |
| Privacy section 7, provider list ("Pancake, the Unified Inbox", "Xendit, subscription payment processing") | Provider facts are Phase 6 (z8uq9mbuqu). Xendit was disapproved at launch (appeal) and bank transfer is the launch method, so the Xendit entry may be stale; that is a provider-register call, not wording for this plan. Reported, not edited |
| Privacy section 8 "Billing and tax records ... at least 5 years" against the ruling R-6 "10 years" | Existing difference between page and ruling (Build 14 did not list it). Not made worse by this plan. Reported, not edited |
| Terms "Payment Requires Attention for 7 days, then suspended" for a Subscriber paying by transfer | The B2a lifecycle handles a proof under review (runbook: a proof holds a trial past its end for 7 days and no more). Wording that explains this would itemise the feature. Not edited |
| Privacy sections 5 and Terms Data Processing (the subscriber's customers) | The inbox is the admin's own (`owner_kind` HQ); no subscriber's customer data is stored there |
| Effective Date (`October 26, 2026`, four pages) and backend `POLICY_VERSION` | Unchanged until launch day (Build 8 ruling). Decision D-D |

## 4. Which line unlocks which switch

A switch is turned on only after the line is live on the page **and** Coach Ed approves that switch; the pages go live only after his review.

| Switch (backend setting) | Lines that must be live first | Plan that builds it |
|---|---|---|
| `SMS_INBOX_ENABLED` (incoming and staff texts are stored) | L1 (both places), L2 first sentence | Inbox plan 3 (R1 "amend first"); the retention rule already runs where the tables exist |
| `SMS_CONTACTS_ENABLED` | L2 second item (needs `SMS_INBOX_ENABLED` and its lines too) | Plan 6 (contacts) |
| `SMS_BULK_ENABLED` | L4, L3 (the STOP sentence), and everything `SMS_INBOX_ENABLED` needs; also the filings the bulk plan lists | Plan 8 (bulk) |
| `BILLING_BANK_TRANSFER_ENABLED` | L5 (both pages) | B2a |
| `BILLING_LAUNCH_PROMO_ENABLED` | none under the recommendation (the Terms already allow a different written plan); the optional L6 line if Coach Ed chooses it | B2a |

`SMS_BULK_ENABLED` and `SMS_CONTACTS_ENABLED` stay off now; `SMS_BULK_ENABLED` does not exist in code yet (plan 8 only).

## 5. Consistency with Builds 8, 11, 12, 13, 14

| Build | What it settled | How this plan treats it |
|---|---|---|
| 8 | Page wording is the launch state; three provider edits; Effective Date and version unchanged until launch day | Same: no date change here. The Build 8 file was not found; I rely on Build 14's quotation |
| 11 | SMS wording in the filing documents: "The system does not store the text of the messages it sends", the gateway phone keeps each sent text (H1 to H6 and 28 edits in the legacy drafts) | **This plan makes those sentences false once the inbox is on.** They live in the legacy drafts and filing text, not on the pages. This plan edits none of them; the filing text (Phase 3 and the Store SMS Notifications system, NPC Build 12 direction) needs its own amendment before `SMS_INBOX_ENABLED`. Flagged for the coordinator |
| 12 | The phone keeps a copy of each sent text for up to one year; no word on how copies are removed | L1 removes the "we do not keep the text" half. The phone clause is removed with it, see D-A. Build 12's other lines (PIA 5, Part 1 and 2 drafts) are legacy documents, not touched |
| 13 | Put the Build 12 sentence on the pages (line 33 and 56) | Done and live in the pages' `origin/main`; **L1 supersedes it by design**, because the inbox makes it untrue. Build 13's Decision 2 stands: the Effective Date does not change |
| 14 | The pages carry every ruled wording; two Part 2 sentences held (provider backups, sales and payment facts) | Neither is added or touched. Nothing here makes either one necessary. Its note that the live page is the newer text is respected: only live lines are edited |

## 6. Decisions for Coach Ed

One question each; my recommendation first.

- **D-A. The gateway-phone clause.** L1 and L3 delete the whole Build 13 sentence, including "the phone that sends the text keeps a copy for up to one year". Recommended: delete it. The page then says the text is kept 1 year, and the phone's own copy no longer needs a separate mention on the page (the filing keeps it, per Build 12). Alternative: keep only that clause. It stays true but adds a sentence nobody asked for. Risk of deleting: none on the page; the filing still says the phone keeps a copy.
- **D-B. Announcements to numbers that never wrote to us.** L4 is true to the code and to R11. Whether texting such numbers is lawful (the Data Privacy Act basis, the telecom rules on unsolicited texts, and whether admin's numbers carry consent) is a legal call. This plan states the facts only. Recommended: Coach Ed or counsel confirms before `SMS_BULK_ENABLED`. Meanwhile L4 can ship with the inbox lines because nothing sends until the switch is on.
- **D-C. Proof of payment retention.** The proof file has no deletion rule in code ("rows are kept"). L5 relies on the existing "Billing and tax records ... at least 5 years" line, so the proof is kept at least as long as it. If he wants a separate period for the proof file, that is a retention-rule build, not wording.
- **D-D. Effective Date and policy version.** Recommended: unchanged until launch day (Build 8). The README rule (`apps/site/README.md:48`) still says to update both when the text changes; it disagrees with the ruling, and Builds 13 and 14 already flagged it.
- **D-E. L6 (promotional offers).** Recommended: no edit. Alternative: the one optional line in L6.

## 7. Checks for the page-edit build (separate task, after approval)

- Re-fetch `origin/main` of the frontend; each "Old" string must be found exactly once on its line, or stop and report.
- `pnpm --dir apps/site lint`, `test`, `build`; local check on the dev server first, then staging; production is not covered.
- Served-HTML check: the string "We do not keep the text of the message" appears 0 times in `/privacy`; the exact STOP sentence appears once; "These are not marketing messages" appears 0 times (if L4 ships); the word forbidden list (merchant, member, shopper, client) is not introduced.
- Ordering: L1, L2 and L3 can ship together with `SMS_INBOX_ENABLED`; L4 before `SMS_BULK_ENABLED`; L5 before `BILLING_BANK_TRANSFER_ENABLED`. One page-edit PR with all lines is simplest, since no switch turns on before the pages are live.

## 8. Not checked

- The Build 8 file itself.
- The filing text and PIA wording (Build 11 and 12 documents live in the legacy folder; outside this plan).
- Whether `SMS_BULK_ENABLED` is present in code: it is not, plan 8 is a plan only.
- Staging or production pages: I read `origin/main` of the frontend, not the served pages.
