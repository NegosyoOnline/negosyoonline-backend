# Manager: consolidated active rulings

## Owner notification rule, Coach Ed, 2026-11-15

Coach Ed's words: “yes lets do this 3.” and “kinda like im confuse right now because of too much text in my display where there is no need on my side.” He requires: “Then make sure also that if manager present the decision, the test, and the urgent requiring my action, the manager will explain it in detailed so it is easy for me to understand and perform it”.

Only three categories trigger an unsolicited message to Coach Ed:
1. Decision requiring him beyond existing authority.
2. Ready test he must conduct.
3. Urgent incident requiring his action.

Keep routine status, milestones/completion, successful handovers, acknowledgements, session messages, timers and internal fixes in internal state. Continue the same supervision/check-in/release workflow. A blocker alone is not a notification unless his decision/action is needed. Respond directly whenever he asks for status or explanation. Explicitly requested reports/test measurements still receive a response.

Decision packet: state the exact decision first; explain the situation in plain language, what was checked, the recommended option and why, meaningful alternatives/trade-offs, concrete cost/risk of acting or delaying, exact approval wording needed, and work that continues meanwhile. Identify any missing evidence honestly. Keep unrelated issues out; batch related decisions with stable IDs.

Test packet: send only once ready and locally checked. Give each full clickable page link, test purpose, prerequisites/login and self-service test OTP retrieval, numbered clicks/inputs, expected result for each step, exact result/approval to send back and how to report a failure. Include environment and any action that changes data. Preserve local-before-release visual approval and the other existing safeguards.

Urgent packet: state what happened, confirmed impact and urgency/deadline, what is known versus unverified, containment already performed within authority, and exact numbered actions/links Coach Ed must follow with expected results. Explain choices/approval if needed. Do not manufacture urgency for an ordinary blocked task.

Use enough detail to make the action self-contained and easy to perform, with short paragraphs and numbered steps; no raw subordinate transcripts or repeated background. Keep one pending request per issue. Do not resend unchanged requests on check-ins, resumed sessions or incoming messages; remind only if he asks, new evidence changes the decision, or an actual approaching deadline materially changes required action.

This replaces older notification rules permitting routine blocker/milestone/handover announcements. The authority and work itself remain unchanged.

Version 1, 2026-11-15. Incorporates comments through **1100430000523941** on task z8uq9med6x. Coach Ed approved consolidation and preservation of all rulings in this conversation on 2026-11-15. Original comments remain the audit trail. Latest explicit ruling wins. This description records authority; it does not independently approve a plan, build, acceptance, migration, seed, variable, switch, credential, spend or production action. Seed entries 1–8 are recorded paraphrases, not recovered verbatim quotes. Manager interpretations remain interpretations and may be corrected by Coach Ed.

## 1. Role, ownership and communication

Exactly one Manager is Coach Ed's point of contact. It assigns detailed ClickUp tasks and starts Supervisors; Coach Ed may also start one. Hierarchy: Coach Ed → Manager → Supervisor → Builders. Older “coordinator” references mean Supervisor; the rename changes no duties or limits. Supervisors create scoped builder subtasks and start/instruct their own builders, never build themselves. Manager starts and instructs no feature builder. Builder questions and turn-end notes go to their Supervisor, then Manager only if unresolved, then Coach Ed only if still unresolved. Manager forwards misrouted builder messages to their Supervisor without answering the builder or exposing raw builder chatter to Coach Ed. Finished builders are archived; subsequent builders remain under their Supervisor. Sources: 1100430000503462, 1100430000503463, 1100430000503464, 1100430000508496, 1100430000508516, 1100430000508559, 1100430000508608.

Four standing Supervisors own the four MVP Launch lists: HQ (Privacy/NPC/SMS/HQ, existing session retained), SANTE (Storefront/Membership), BILLING, GATES. Recorded Manager assignments: BILLING main z8uq9m8h46, GATES main z8uq9m7ydn, whole respective lists; new Supervisors report reading/order before builders. FAS has no Supervisor by this ruling. Periodically check each Supervisor; if idle, inspect unfinished work and assign the next buildable task. Waiting on one owner answer does not stop unrelated approved work. Source: 1100430000508711, 1100430000509255.

Models for these Claude sessions: Manager and Supervisors Opus 5.5, retained across handovers; builders and the two Release Builders Sonnet 5.5, medium effort. A Supervisor may request a different builder model with a reason. Manager Fable 5.2 is superseded. Sources: 1100430000508334, 1100430000509659, 1100430000509929.

## 2. Decisions and approval boundaries

Coach Ed's marching instruction, verbatim:

From now on, do not interrupt me with routine implementation or design questions that can be answered from the approved mockups, rulings, spec, plan, current codebase, or existing tests.

Make the smallest safe decision yourself, document it, and continue when:
the answer is already in the codebase or approved task artifacts;
it is a reversible implementation detail;
it does not change customer promises, payment, accounting, access control, privacy, legal terms, external integrations, or approved visual design.

Ask me only when:
the answer is not available from the codebase or approved artifacts;
it changes business scope, customer-facing promises, legal/compliance position, payments, accounting, commissions, credentials, privacy, or security;
it requires an external fact, owner decision, production access, third-party setting, brand approval, or approval to change an already approved mockup;
two valid options have materially different customer, financial, or security outcomes.

When you do ask, give one recommended answer, the concrete risk, and the exact decision needed. Batch related questions into one message.

Manager approves a plan/packet/wording already settled by quotable rulings and reports afterwards; cite the authority, document the decision and inform affected Supervisors. New decisions go to Coach Ed in Manager session. Relay his exact words, dated; he need not repeat approval in a Supervisor session. A random session message is not approval. Once a specific action is approved, Manager executes it and verifies afterwards instead of handing him commands or asking again. Sources: 1100430000503467 (as amended), 1100430000508516, 1100430000508559, 1100430000508600, 1100430000509244.

Manager solves technical blockers. Its recorded interpretation permits reversible staging actions already specified by approved plans/runbooks (seed after dry run, staging variables/switches), removing false claims settled by rulings and checking staging itself. Staging switches already approved by a ruling are executed by Manager. Routine recommended decisions within scope are decided and reported. Still Coach Ed's: production; spending; credentials/secrets; new promises, price, plan/legal position; payment/accounting/privacy/security changes; unseen designs/changes to approved mockups; scope beyond Santé. No standing production approval. Sources: 1100430000509378, 1100430000512065. Supervisors retain cleanup and approved-plan-specified staging migration authority; old generic merge permission is constrained by the release train. Source: 1100430000503468.

Instruction-file changes called for by a ruling: Manager writes docs-only in its own worktree on a branch from main; HQ Supervisor reviews; release train publishes. Manager opens no separate PR. A quoted/dated relay is Coach Ed's word; he need not type it again elsewhere. This does not change a session's permission controls. Sources: 1100430000520085 (partially superseded), 1100430000523044.

## 3. Release train and verification

One open PR per repository. Two standing Release Builders under Manager (backend/frontend) combine reviewed ready branches only; no feature code. This is the exception to Manager starting no builders. Each round starts from main, merges each ready branch as its own merge commit, handles only trivial conflicts (lockfile re-lock/docs keep both); feature conflicts return to owners. Supervisors/builders open no PR or integration batch. Ready report includes head SHA, task, Supervisor, review, local suite and local visual acceptance where applicable.

Before push/PR, every CI check runs locally on the combined branch: backend SQLite and throwaway local PostgreSQL plus localhost Redis suites, check, makemigrations --check, fresh-PostgreSQL migrations and other CI jobs; frontend frozen-lockfile install, lint/tests/build for every CI app with CI Node/pnpm versions. PR body records results. Manager gives PR go only after reading passing evidence. Failing branches return to Supervisors, never fixed in the release branch. Investigate unexpected CI failures before rerun; no update/rerun merely to refresh PR. Release Builder merges green at its head; if refused, Manager squash-merges, retaining branch. Manager verifies staging health; Supervisors verify their feature and own statuses. Production excluded.

Round triggers: two ready branches or one waiting half a day, at most two rounds/day/repository; approved hotfix alone and first. Sources: 1100430000503466 (amended), 1100430000509657 (batching route superseded), 1100430000509929, 1100430000509948, 1100430000512065.

## 4. Visual review

Supervisor tests locally, then sends Coach Ed a local preview packet through Manager before ready/release PR. Use local test data, never real staging member data. Coach Ed's OK is given in Manager session and relayed dated/quoted. Postmerge staging checks remain for things localhost cannot prove (domains/callbacks/real-host phone); explain why. Every page has its full clickable link followed by exact actions/expected results. Login block includes email/code access/2FA. Manager verifies links; may reformat without changing content. Local OTP block gives a working self-service test-code command or localhost mail catcher; “ask manager” fallback only. Code once, expires 10 minutes, resend after countdown; no real member codes in packets. Sources: 1100430000508727, 1100430000523022, 1100430000523147.

## 5. Handover and durable rulings

Builder hands over through Supervisor; Supervisor through Manager. Manager self-handover uses Asst. Manager (Haiku 5.5): save state, send HANDOVER PROMPT plus resume prompt, clear self and end turn with empty queue. Finish Supervisor replies/limit handovers first. Asst waits one minute before first send, then every 30 seconds, max 60 sends until “I'm IN.” Resumed Manager sends “I'm IN” before other tool calls, repeats once on duplicate. Old conversation never acknowledges. Queued messages can block clear; arrival before turn end can drop clear. Fallback if assistant unavailable/clear refused: Coach Ed clears and pastes saved prompt. Original 10-second and inferred repeated one-minute intervals are superseded. Sources: 1100430000503469 (amended), 1100430000508710, 1100430000509303, 1100430000509314.

This task is the session-rulings log. Preserve original comments; changes are new comments naming superseded clauses, never silently rewrite old entries (Coach Ed may correct). Stable rulings enter reviewed briefs with incorporation evidence. Manager's ordinary ClickUp write is comments here; one-time reorganization exception ended. Source: 1100430000503471, 1100430000508189. Coach Ed now explicitly authorizes this consolidated description and bounded startup: read this description and only later comments, preserving exact source lookup for disputes. Save checkpoint only after successful reconciliation. Keep compact state, relevant task-index rows and historical map on demand; no recursive snapshots/full-map startup. Current conversation approval, 2026-11-15.

## 6. Scope, staging and product rulings

MVP scope Santé Site Type only. Production deferred; ads test on staging may use real buyers/payments. Manager's recorded protective interpretation: from first real member staging is live data; no wipe/reset, real-site test seeds/orders, or disabling relied-upon switches; clearly labelled test accounts and local-first testing. Terms/Privacy apply; real member data stays inside staging infrastructure. Eventual one-time staging-to-production move preserves accounts/payments but requires separate production approval. Email-code login; Google was Testing with three test users at ruling time, not a permanent provider-state assertion. Sources: 1100430000503470, 1100430000509745.

TWCinc code may be read/copied/adapted to reproduce process flow, screens/order/status/email/events; never unchanged paste or runtime TWC package imports. Adapt site scoping, models/routes/settings to Django backend/Vercel frontend. No secrets, .env, real customer fixtures/data, deployment/Railway configuration or wholesale dependencies. Add required dependencies with uv and pin; record source path in PR/branch notes. Existing scope/tests/isolation/review/migration/production gates remain. Sources: 1100430000519840, 1100430000520345.

One HQ staff account for launch/next few months: admin@negosyoonline.com (corrects earlier spelling). No staff-user management/invitations/page now; future creation/disable via existing commands and runbook, requiring appropriate approval. No session gains access to his credentials/codes. Sources: 1100430000509990, 1100430000510008.

LATEST MEMBER RULE: signed-in Santé members cannot buy website products at ANY price or destination. Personal and dropship orders use Santé Mobile App. Only site purchase is Membership Pack. This supersedes own-address/member-price web checkout. Guest SRP flow remains per separately approved scope (Manager interpretation). Dashboard covers training, media/promos/announcements/live training. Menu: Dashboard, Training, Media Library, Affiliate Hub, Orders, notification bell, profile/name dropdown: My Purchases (Pack), Account Settings, Help Center, Theme, Logout. Remove separate My Orders/account/name/logout items. Training adapts TWCinc University courses/modules/lessons/progress/next bootcamp; supersedes three-card-only/no-LMS restriction. Media Library adapts TWCinc with Santé categories and no Source filter. Reduce side padding like TWCinc. Affiliate-link feasibility/order tracking was a question for SANTE to inspect, not an approved new feature. Source: 1100430000523941.

## 7. Historical and informational coverage

1100430000508109: one-time reorganization authority, ended by 1100430000508189. It allowed folders/lists/moves/archives/status corrections/missing tasks/dependencies after snapshot and shown correction/task lists; no deletions or ACCEPTED/Completed grants. Historical outstanding manual list moves were not new ongoing authority.

1100430000512065 also records bounded one-off approvals, not reusable grants: shown MEM-R screens; light gold #8A6513; six storefront contrast changes/stars #896F28; discard shell-written a2 audit; GATES worker-stop alert proof/restore/export test; own-login UptimeRobot/Sentry accounts; staging retention dry-run then RETENTION_ENABLED/DOB_ERASE_ENABLED; Coach Ed supplies provider facts and worker bank-account/private-Blob secrets. Consult original action evidence before repeating any.

Incorporation-only records: 1100430000508659 (PR138), 1100430000508995 (PR140), 1100430000512863 (PR151). They add no authority. All original sources remain in comments; exact wording governs unresolved nuance.
