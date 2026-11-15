---
description: Save bounded Manager state and resume through Asst. Manager
---

Use existing context to save state; do not re-explore history at the context limit.
Maintain docs/session-state/manager-now.md by replacement (max 2,500 words): current Supervisor/Release Builder IDs and states, ready branches with repo/task/head/date, current PR/check/deployment evidence, exact pending approval scope/environment, unlogged rulings, verification owed and next 5–10 actions. Preserve the marching instruction verbatim from the already-loaded consolidated description. Resolve stale pending entries against manager-decisions.md before saving.
Update manager-decisions.md when decisions changed. Preserve exact words/source/date/scope/environment, supersession and affected tasks; do not invent approvals. Save historical detail separately. Never rewrite the task map for handover.

Write a pointer docs/session-state/manager-<sid8>.md under 1 KB: role, absolute checkout/current-state/decision-register paths, model, Asst. Manager ID, latest ruling-description version and one exact next action/source section. Do not chain earlier snapshots. At normal context, run the read-only scripts/manager_context.py validator; at an emergency save first and mark validation owed. No exploratory reads merely to fill fields.

Finish pending replies and at-limit Supervisor handovers. Send Asst. Manager a message beginning HANDOVER PROMPT followed by the short prompt from docs/runbooks/manager-resume-prompt.txt, substituting absolute paths and IDs already known. Retries reuse that same prompt unchanged. Asst waits one minute, then sends every 30 seconds, max 60 until “I'm IN.”
Clear self and end the turn immediately with no queued messages; old conversation never acknowledges “I'm IN.” If clear is refused preserve files; respect runtime permissions. Fallback: give Coach Ed /clear and ready-to-paste prompt.
No new model, authority, release or approval workflow is introduced by handover.
