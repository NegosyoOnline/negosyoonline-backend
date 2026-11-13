# Breach and Incident Runbook

This turns the promises in the published Privacy notice (section 10, "Personal Data Breaches") into steps. It adds no promise. The notice says:

- If a breach is likely to put people's rights at real risk, we tell them and the National Privacy Commission (NPC) within 72 hours of learning of it.
- If it affects data we hold for a Subscriber's customers, we tell that Subscriber within 24 hours so it can notify them.

The Terms (data-processing section) repeat the 24-hour promise to Subscribers.

The incident runbook (`docs/runbooks/incident.md`) covers containment, secret rotation and putting a site on hold. This runbook covers the Privacy notice's breach notices.

Real Santé members are on staging (Coach Ed's ruling of 2026-11-13). So this runbook applies to staging data now. Production is not built yet.

Where a step says "not built", no tool, command or alert exists. Do it by hand and write down what you did.

## 1. How an incident is noticed

| Signal | Status |
| --- | --- |
| A person writes to support or the DPO | Exists: `support@negosyoonline.com`, `dpo@negosyoonline.com`, as the notice lists. |
| A staff member sees something wrong in HQ | Exists, by hand. |
| Admin sign-in log lines and the admin audit log (`OperatorAuditEntry`): wrong codes, pauses, identity changes | Exists. See "What the logs say" in `docs/runbooks/admin-two-step.md`. Someone has to read them. |
| Account audit events (sign-ins, other-device sign-outs, email and mobile changes) | Exists, kept 1 year. See `docs/runbooks/retention.md`, "Security records". |
| `/healthz/` check | Exists, but only at each deploy and by `scripts/verify_deploy.sh`. It is not monitoring and shows no breach. See `docs/runbooks/billing.md`. |
| Alerting on suspicious activity | Not built. |
| Log monitoring or log review on a schedule | Not built. |
| Staff alert when a proof of payment waits | Exists in `docs/runbooks/billing.md` (`BILLING_STAFF_ALERT_EMAIL`), but it is not a breach alert. |

So today an incident is noticed only when a person reports it or someone looks.

## 2. Who decides, and who is told first

- Coach Ed is the owner and decides.
- The DPO named in the notice (Ed Vincent Maceda Geronilla, `dpo@negosyoonline.com`) decides whether the breach is "likely to put rights at real risk" and signs off every notice below.
- Whoever first learns of the incident tells Coach Ed and the DPO at once, in writing. Do not wait to be sure. The clocks below start when we learn of it.
- Staff do not email people, Subscribers or the NPC themselves. They send the facts to the DPO.

Write down, at once, the time we learned of it. Both clocks start from that time.

## 3. First hour: contain it

Do the steps that fit. Changing Railway variables on staging or production needs Coach Ed's approval each time (`CLAUDE.md`). In an emergency, ask him first and do the step the moment he says yes. Never put a secret value in chat, a ticket or a commit.

| If | Step | Source |
| --- | --- | --- |
| The SMS phone or its token may be exposed | Rotate the token. The old token fails at once. | `docs/runbooks/sms-gateway.md`, "Rotating a token" (`manage.py sms_device rotate`) |
| The SMS phone is lost or stolen | Stop that phone. | `docs/runbooks/sms-gateway.md`, "Stopping one phone" (`sms_device disable`) |
| Texts are going to the wrong people, or you cannot tell | Stop all sending. | `docs/runbooks/sms-gateway.md`, "Stopping all sending" ("Sending stopped" on the HQ Settings page, SMS settings) |
| Incoming texts or the SMS inbox are involved | Turn the incoming switches off; withdraw inbox access. | `docs/runbooks/sms-gateway.md`, "Incoming texts: the three switches" (`sms_device incoming off`, `sms_inbox_access revoke`) |
| A staff identity may be misused | Disable it. This ends that person's sessions and keeps the row. Use `reset` to end sessions and clear a pause. | `docs/runbooks/admin-two-step.md` (`manage.py admin_identity disable`, `reset`) |
| The staff app's calls are in doubt | Delete `HQ_STAFF_ORIGIN` and redeploy; the HQ pages keep working. | `docs/runbooks/admin-two-step.md`, "The staff routes" |
| A secret or key may be exposed (an environment variable) | Replace the value in Railway and redeploy. Name the variable to Coach Ed; never write the value. | `docs/runbooks/incident.md`, "Rotating each secret". |
| A feature is the source | Switch off its flag (for example `MEMBERS_ENABLED`). Read the feature's runbook first: some flags are not turned off again without Coach Ed's ruling because disposal and export depend on them. | `docs/runbooks/retention.md`, `docs/runbooks/members.md` |
| A bad release is the source | Redeploy the previous build. | `docs/runbooks/admin-two-step.md`, "Release order"; `docs/runbooks/migrations.md` |
| One Subscriber account or one Santé member session is the source | An operator command to end all sessions of a Subscriber or a member: not built. A member's contact change or recovery revokes that member's sessions (`docs/runbooks/members.md`). | not built |
| Files in private media may be exposed | Not built: no command to revoke or rotate media access. See `docs/runbooks/storage.md` for what the ledger holds. | not built |

Keep the evidence. Do not run a retention, disposal or export command on the affected data until the DPO agrees: those commands delete or blank data (`docs/runbooks/retention.md`; staging data needs Coach Ed's approval for each of them).

## 4. Work out what happened

The DPO, with whoever can read the logs, writes down:

- what happened and when it started and ended (if known);
- which data: which kinds (for example name, email, mobile number, delivery address, payment details, date of birth), and whose;
- how many people, roughly, and which Subscriber's customers (Santé members, buyers) if any;
- which environment (staging or production), and whether the data was only exposed or also changed or lost;
- what has been done to stop it.

Count the people with what exists: the export command lists one account's data (`docs/runbooks/retention.md`, "An export request"); a fuller count across accounts is not built, so it is done by hand from the database with Coach Ed's approval.

If you do not know yet, say so in the notice and send an update. Do not guess.

## 5. The 24-hour clock: tell the Subscriber

Applies when the breach touches data we hold for a Subscriber's customers (for example Santé members of that Subscriber's site). Tell that Subscriber within 24 hours of learning of it, so it can notify them. The DPO sends it. Sending by email from the support address is by hand; no tool for it is built.

### Draft for the DPO: notice to an affected Subscriber

> Subject: Security incident affecting your customers' information
>
> We are telling you that we learned on [date and time] of a personal data breach that affects information we hold for your customers.
>
> What happened: [short plain description].
>
> What information: [kinds of data, and how many of your customers, if known].
>
> What we have done: [steps taken to stop it and protect the data].
>
> What you can do: you may need to tell your affected customers. We will help with your notice. [Any step we recommend, for example telling customers to watch for unexpected messages.]
>
> We will send an update when we know more. Contact: dpo@negosyoonline.com.
>
> NegosyoOnline IT Solutions

## 6. The 72-hour clock: tell the NPC and affected people

Applies if the DPO decides the breach is likely to put rights at real risk. Within 72 hours of learning of it, tell the NPC and the affected people. The notice quotes the law here and does not add more. Anything beyond this notice, such as the NPC's own form or portal and any further duty, is for the DPO to check with the NPC (privacy.gov.ph). This runbook gives no legal advice.

Where the data belongs to a Subscriber's customers, the Subscriber notifies them after our 24-hour notice (section 5). Agree with the Subscriber who sends what, and keep a copy.

### Draft for the DPO: notice to the NPC

> Subject: Personal data breach notification, NegosyoOnline IT Solutions
>
> We learned of the breach on [date and time]. This notice is sent within 72 hours of that.
>
> What happened: [description, dates, how it was found].
>
> Data and people: [kinds of data; number of people, or an estimate and why].
>
> What we have done: [containment and protection steps].
>
> What affected people can do: [steps we have told them].
>
> Contact: Ed Vincent Maceda Geronilla, Data Protection Officer, dpo@negosyoonline.com.
>
> NegosyoOnline IT Solutions, B3 L9 Santiago Villas, Catalunan Grande, Davao City, Philippines 8000.

### Draft for the DPO: notice to affected people

> Subject: A security incident that affects your information
>
> We are telling you that on [date] we learned of a security incident that affects your personal information.
>
> What happened: [short plain description].
>
> What information: [kinds of data].
>
> What we have done: [steps taken].
>
> What you can do: [for example watch for unexpected messages or sign-in codes you did not ask for, or contact us]. You can write to dpo@negosyoonline.com or support@negosyoonline.com. You may also raise a concern with the National Privacy Commission (privacy.gov.ph).
>
> NegosyoOnline IT Solutions

## 7. What is recorded afterwards

There is no breach register in the system. Not built. The DPO keeps one record per incident, outside the repository and with no secret values:

- the time we learned of it, and who told whom and when;
- the facts from section 4;
- the containment steps taken, with the date, who, and the runbook step used;
- copies of each notice sent, with date and recipient;
- Coach Ed's decisions;
- what was changed afterwards so it does not happen again, as a ClickUp task.

The actions taken through `admin_identity`, `sms_device` and the SMS policy ("Sending stopped" on the HQ Settings page, SMS settings) are already written to the operator audit log (`docs/runbooks/sms-gateway.md`, `docs/runbooks/admin-two-step.md`). Point to those entries in the record.

If the incident shows that the Privacy notice or the Terms say something that is not true, tell the DPO. Wording changes are the DPO's and Coach Ed's call.
