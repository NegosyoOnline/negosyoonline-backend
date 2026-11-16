---
description: Save Supervisor state and hand over through Manager
argument-hint: "<CLICKUP_TASK_ID>"
---

Require explicit task ID in $ARGUMENTS; do not guess. Follow docs/runbooks/role-handover.md using Supervisor role and 6 KB state target.
Include coordination record: approved scope/source, builders/IDs/reports/status, dependencies/overlap, exact approvals, resolved and open decisions, ready branches/release PRs/staging verification, previews and next action.
Resume Opus 5.5. With Manager save state plus exact prompt in a sibling .resume.txt file, send “Handover written, ready to be cleared” with both absolute paths, then end the turn. Manager owns clear and prompt delivery. If parent clear is refused, Manager may direct supported self-clear after verifying both files; follow docs/runbooks/supervisor-clear-recovery.md. This exception overrides the normal stop-without-self-clear rule.
Only with no Manager may an existing Supervisor Helper receive prompt before self-clear/end-turn; never create a new helper. With Manager, recovery stays Manager-owned; never give Coach Ed a clear/paste assignment. Preserve exact tool refusals and use the recovery runbook.
After resume notify Manager internally, ask builders to resend unanswered questions sent during clear and continue Next.
