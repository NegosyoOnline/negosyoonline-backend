---
description: Save Supervisor state and hand over through Manager
argument-hint: "<CLICKUP_TASK_ID>"
---

Require explicit task ID in $ARGUMENTS; do not guess. Follow docs/runbooks/role-handover.md using Supervisor role and 6 KB state target.
Include coordination record: approved scope/source, builders/IDs/reports/status, dependencies/overlap, exact approvals, resolved and open decisions, ready branches/release PRs/staging verification, previews and next action.
Resume Opus 5.5. With Manager send “Handover written, ready to be cleared”, state path and exact prompt; stop without clearing self. Manager verifies and clears idle session, then resumes it.
Only with no Manager may an existing Supervisor Helper receive prompt before self-clear/end-turn; never create a new helper. Otherwise preserve state and provide manual fallback through Manager if present.
After resume notify Manager internally, ask builders to resend unanswered questions sent during clear and continue Next.
