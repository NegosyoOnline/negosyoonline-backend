---
description: Save builder state and hand over through its Supervisor
argument-hint: "<CLICKUP_TASK_ID>"
---

Require the explicit task ID in $ARGUMENTS; do not guess. Read/follow docs/runbooks/role-handover.md using Builder role and 5 KB state target.
Preserve task sources/acceptance, branch/head, preview commands/ports, actual verification, unfinished work, exact approval scope/environment and Supervisor ID. Resume Sonnet 5.5 medium unless Supervisor specified otherwise.
Session builder: send Supervisor “Handover written, ready to be cleared”, absolute state path and unchanged resume prompt; stop. Do not clear self.
Sub-agent builder: safe stop and return same state contents in final report; Supervisor continues or replaces it, no clear.
Fallback if delivery unavailable: save state and provide clear/paste prompt through Supervisor/Manager where reachable.
