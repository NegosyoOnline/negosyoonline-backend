# Manager context SOP

Authorized by Coach Ed, 2026-11-15. Workflow and ruling authority are unchanged; this file controls loading and durable state.

## Source ownership

- Standing rulings: full current DESCRIPTION of ClickUp z8uq9med6x and later explicit rulings. Local mirror manager-rulings-consolidated.md is a cache only.
- Current work: docs/session-state/manager-now.md, maximum 2,500 words; replace stale facts, do not append an endless log.
- Settled/open business decisions: docs/session-state/manager-decisions.md, maximum 1,500 words of active entries; exact evidence and limits.
- Task locator: manager-task-index.md, maximum 5,000 words; search relevant rows only.
- History: dated snapshots, manager-task-map.md and docs/runbooks/rulings-archive/ (rulings-source.json, rulings-inventory.json; committed); targeted lookup only, outside ordinary startup.

## Startup

Apply the Manager brief's visible-message gate before every unsolicited commentary/final reply, including startup, scheduled check-ins and Supervisor/Asst. Manager messages. Only a new owner decision, ready owner test or urgent owner action warrants visible output. Save routine results internally; no file-read narration, takeover announcement, timer confirmation, “nothing needs your action” or final status wrap-up. Send internal acknowledgments normally. Internal handover prompts do not request an owner report. Answer explicit owner questions/requested reports and preserve detailed action packets, approvals, checks and cadence.

1. Automated resume: acknowledge Asst. Manager “I'm IN” first, repeat once on duplicate. Confirm existing Manager identity/main checkout; safely update main as brief specifies.
2. Read named pointer, manager-now.md, manager-decisions.md, manager brief and this SOP directly once. Do not follow old state chains or load historical indexes/archives in full.
3. Read ClickUp task with include description, avoiding a truncated task summary. Check current comments; zero is valid. The 43 incorporated comments through 1100430000523941 were explicitly deleted by Coach Ed; IDs refer to local archive, never an online boundary to search for.
4. Process all unincorporated comments/replies with pagination. Compare against description version/incorporated IDs. If none are new, emit count only when tool filtering is supported. A runtime without filtering may expose a full page; report the limitation. Save checkpoint only after success. Do not assume task updated time proves absence of comment changes.
5. Before asking anything, reconcile current pending items against settled decisions. Use exact source lookup only for a conflict/unclear approval. Mark missing evidence honestly; never widen authority.
6. Record startup checkpoint internally: files/sections loaded, description version, new comments/replies and reconciliation result, actual context count if observable or “not observable.” Report/pause only during an explicitly requested measurement test; otherwise continue approved work quietly.

No browser or portfolio exploration is part of startup. Refresh PR/session state when needed for the first action rather than replaying prior work.

## Ruling maintenance

Preserve exact source evidence before consolidation. Retain every surviving clause, partial exception and attribution to Coach Ed versus Manager interpretation. Map each source to consolidated sections. Verify read-back, source-ID coverage and exact marching instruction before advancing version. Source-ID coverage is structural evidence, not proof of semantic completeness. Read relevant originals for disputes.

Before release/major scope changes compare current description/new comments/replies with saved evidence and hashes. Deleted originals are checked locally, not refetched. Never automatically delete future comments. Incorporation/deletion needs the applicable explicit authority. Missing source or ambiguous clause prevents relying on that clause, not unrelated approved work.

## Decision continuity

Record decision ID/topic, settled/open/superseded status, exact words or labelled paraphrase, date/source, affected task/Supervisor, action/environment limits, outstanding execution and supersession. A settled choice can have pending execution; do not ask the choice again. Record a materially changed risk as a new issue referencing the prior decision. One-off approvals never become standing rulings. When source is only a Supervisor report, label it reported. Preserve old evidence before moving closed entries out of the active register.

## Efficient operations

Safety envelope approved by Coach Ed, 2026-11-15: reduce repeated output without risking performance or continuity. Keep tools available, required verification, check-in cadence, ownership and the one-minute/30-second/max-60 handover acknowledgement protocol unchanged. No tool disconnection, delegation change or approval reduction is authorized by optimization alone.

Use compact internal change reports containing: task/owner, changed result, evidence (repository/head/environment/date and verification performed), blocker or explicit none, next action, and approval needed or explicit none. Include new risks, failed checks and unresolved obligations even if the report becomes longer. Unchanged reports need not repeat background; save the last verified state and retain evidence pointers. Absence of a new report never means a check passed. Manager independently verifies release identity/health, sensitive actions and disputed results as before.

Before shortening state or a policy, preserve source evidence and map each removed clause to the retained source, including exceptions and supersession. Keep exact decision sources, approval limits, unfinished obligations and next actions. If context savings hide a blocker, reopen a settled decision, lose an approval boundary or impair verification, restore the relevant detail from the archive and investigate before further trimming. Never prioritize a word limit over an unresolved obligation; flag temporary budget overruns internally and preserve the facts.

Evaluate over a normal working cycle: record context at startup and after check-ins when observable, repeated-read/output counts, and any lost/reopened decision or missing obligation. Do not claim success from size or source-ID checks alone. Report measurements only when requested under the notification rule.

Keep existing check-in cadence and Supervisor ownership. Ask for deltas: milestone, blocker, ready branch or changed decision. Read full task/state only for a relevant uncertainty or idle session with unfinished work. Scope tool results to fields, lines and required page regions. Avoid full browser dumps/navigation boilerplate; use supported targeted tools and screenshots as needed. Do not load unrelated schemas. Identical handover retries carry no extra history. Owner notifications only for decisions, ready tests or urgent incidents requiring his action, using the self-contained detailed packet requirements in the Manager brief. Suppress unchanged questions and routine completion/status/handovers; direct requested reports remain allowed.

## Handover and validation

Follow .claude/commands/handover-manager.md. Preserve marching instruction verbatim in current state; short pointer and constant resume prompt. Live Manager owns state; another audit must not overwrite concurrent live updates.

Run .venv/bin/python scripts/manager_context.py. Run it in the Manager's main checkout: docs/session-state/ is gitignored, so the state-budget checks fail in other worktrees; the ruling archive check works anywhere. Default validation is read-only. --build-index and --build-rulings are explicit generation actions; do not run generation at startup or during ordinary validation. Archive absence, hash mismatch or missing ruling IDs fails validation. No production access or app deployment is required for these docs checks.

Acceptance: refreshed clear baseline and paused startup breakdown; selected files only; ruling/decision continuity checks. Word limits do not guarantee a token reduction. Existing approval, visual-review, release and production gates remain in force.
