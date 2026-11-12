---
description: Hand over the manager session: write its state file and a resume prompt that restores the manager role
---

Hand over this manager session. Write its state file, then print a resume prompt that makes the next session the manager.

The role instructions are in `.claude/briefs/manager.md`. Do not read or copy that file now: the resume prompt points the next session at it.

## Cost rule

This command is run when the context is already large, where every tool call re-bills the whole conversation. One `Write` is the entire budget. Do not run Bash, `git`, `mkdir`, or re-read files to fill it in: everything it needs is already in this conversation. The task map (`docs/session-state/manager-task-map.md`) is already on disk; do not rewrite it here.

## Where it goes

`<session folder>/docs/session-state/manager-<sid8>.md`, with the path rules of `/handover` (absolute path, `<sid8>` from the scratchpad path, git-ignored, never force-added, no secrets, tokens or environment values).

## Contents

Write exactly these sections, whole file under 6 KB:

    # Session state: manager

    Role: manager

    ## Resume here
    - Session folder: <absolute path>
    - Manager brief: <absolute session folder>/.claude/briefs/manager.md
    - Task map: <absolute path>, last full study <date>, last refresh <date>
    - This manager: <session id>
    - First move: <the one thing the next session does first>

    ## Register
    - Coordinators: title, session id, ClickUp task, repo areas owned, status, what each waits on.
    - Builders the manager started for a coordinator: title, session id, owning coordinator.

    ## PR queue
    Per repository: the open PR and who holds the slot, then the queue in order.

    ## Rulings
    A pointer: the link of the rulings log, ClickUp task z8uq9med6x, and the date of the last entry read. Below it only rulings given in this session that are not yet logged, each quoted with its date and the coordinators told.

    ## Owner decisions pending
    Each with its coordinator, the exact decision, and whether it needs his word in that coordinator's session.

    ## Plan
    The order of work and the assignments to coordinators, and any change Coach Ed made to them.

    ## Next
    Bullets, in order.

    ## Verification still owed
    Anything reported by a coordinator that the manager has not checked against `gh`, ClickUp or staging.

Rules for the content:

- Write only what this conversation or its sources established. No ruling, approval or status from memory of what was probably said.
- An approval is recorded with its exact action, scope and environment. The manager holds none itself.
- Name the repository for every path, branch and PR.

## Finish

Print exactly two things:

1. The file path, and that it is safe to `/clear`.
2. A ready-to-copy resume prompt in a plain fenced block (not tagged `bash`; it is a prompt, not a command):

       Resume in <absolute session folder> as the manager of all coordinator sessions.
       Read <absolute state-file path>, then read and follow
       <absolute session folder>/.claude/briefs/manager.md.
       Read the rulings log, the comments on ClickUp task z8uq9med6x, newest first, then the task map before re-reading ClickUp. Do not re-explore anything already recorded in the register.

Say plainly: `/clear`, then paste this. The paste is required: nothing sits above the manager to send it.
