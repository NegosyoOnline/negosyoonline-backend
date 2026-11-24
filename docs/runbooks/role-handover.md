# Shared role handover procedure

Preserve the existing Builder/Supervisor hierarchy and approval gates. Manager has its own command. This file replaces duplicated file-format rules, not role duties.

## Save at a safe point

At context warning or earlier: finish/describe partial work, leave no command or half-written file running, commit approved task work or describe unsaved state, start no new work. Use already-established context; do not re-explore at exhaustion. One state write is the target; any required parent message follows. At normal context validate before limit. Never omit unfinished obligations to fit a budget.

Absolute path: <session folder>/docs/session-state/<folder basename>-<sid8>.md. sid8 from session/scratchpad ID; if unavailable use basename-HHMM. Use own session folder even for cross-repository work; name actual edited checkout/repo/branch separately. Write creates missing directories if supported. Files are git-ignored, never force-added, disposable with worktree pruning; preserve anything needed before cleanup. No secrets/tokens/environment values. Specs/plans/runbooks belong in their proper docs folders.

## State sections

- Role/task/model; Resume here: absolute session and actual work paths, repository/branch/head/ready status/release PR, local server commands/ports/running state, parent IDs, ruling-description version and first next action.
- Task: objective, deliverable/acceptance/scope sources and unread items. Supervisor also includes builder register/reports, dependency/overlap order, settled/open decisions and approval evidence.
- Done: actual commits/PRs/files/checks and environment; reported versus independently verified.
- Next: ordered actions/owners; required approvals and safe work continuing meanwhile.
- Key files: exact paths/sections, no recursive discovery chains.
- Open decisions: who owes what, settled choices distinguished from pending execution.
- Verification owed: tests/browser/staging not done, temporary files/services and removal conditions.

Approval continuity: preserve exact words or labelled report, source/date, action, scope, environment, holds/revocation and execution state. Valid unrevoked approval survives restart only for the same action/scope/environment. A state file does not create approval; missing evidence requires source recovery, not invention. Builder still cannot run Supervisor-only migrations; production excluded from standing approvals. Re-read the assigned task before building; Done does not replace actual source/code verification.

## Resume prompt and delivery

Print/save absolute path plus plain fenced prompt: “Resume in <absolute checkout> as <role> for task <ID>, model <approved model>. Read <absolute state path>, CLAUDE.md, the role brief and start command. Read the official local rulings log /Users/edgeronilla/Dev/negosyoonline/negosyoonline-backend/docs/session-state/rulings-log.md in full; z8uq9med6x is a frozen archive and its 43 deleted comments are local archive references. Re-read task/required sources; retain exact approvals, verify Done as needed and continue first Next. Report to <parent ID>.”

Role command determines delivery: Builder session through Supervisor; Supervisor through Manager; sub-agent returns state, no clear. Confirm state path/task before any authorized idle clear, follow supported hook/permission rules, send unchanged prompt and record result. For Supervisor recovery with a Manager, docs/runbooks/supervisor-clear-recovery.md governs: Manager owns clear/prompt delivery, including authorized Supervisor self-clear fallback; never assign Coach Ed clear/paste. Other role routes retain their own command's fallback. Preserve exact refusals and never bypass permissions. With no parent, provide saved prompt to Coach Ed. Preview/approval/ACCEPTED cleanup gates remain unchanged.
