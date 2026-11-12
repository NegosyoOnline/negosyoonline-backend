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

Write exactly these sections, whole file under 8 KB (the marching instruction alone is about 1.5 KB):

    # Session state: manager

    Role: manager

    ## Resume here
    - Session folder: <absolute path>
    - Manager brief: <absolute session folder>/.claude/briefs/manager.md
    - Task map: <absolute path>, last full study <date>, last refresh <date>
    - This manager: <session id>. Model: Fable 5.2 (`claude-fable-5-2`); the next manager runs on it (Coach Ed, 2026-11-13)
    - First move: <the one thing the next session does first>

    ## Register
    - Supervisors: title, session id, ClickUp task, repo areas owned, status, what each waits on.
    - Supervisors' builders: only what a Supervisor has told the manager (title, owning Supervisor, state). The manager starts no builder and instructs none (Coach Ed, 2026-11-13); Supervisors run their builders as their own sub-agents. Supervisors run on Opus 5.5 (`claude-opus-5-5`) and keep it across handovers.

    ## PR queue
    Per repository: the open PR and who holds the slot, then the queue in order.

    ## Marching instruction (Coach Ed, 2026-11-13)
    This section holds Coach Ed's marching instruction verbatim, exactly as in `.claude/briefs/manager.md` section 3. Copy the whole text below, unchanged, into every state file (he asked: "save this to your memory so all future managers especially when we do a handover command"). A state file without it is incomplete.

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

    ## Rulings
    A pointer: the link of the rulings log, ClickUp task z8uq9med6x, and the date of the last entry read. Below it only rulings given in this session that are not yet logged, each quoted with its date and the Supervisors told.

    ## Owner decisions pending
    Each with its Supervisor, the exact decision, and whether it is covered by a ruling of his (name it) or needs his word, asked of him in the Manager session (he does not go to a Supervisor's session; Coach Ed, 2026-11-13). Batch related ones.

    ## Plan
    The order of work and the assignments to Supervisors, and any change Coach Ed made to them.

    ## Next
    Bullets, in order.

    ## Verification still owed
    Anything reported by a Supervisor that the manager has not checked against `gh`, ClickUp or staging.

Rules for the content:

- Write only what this conversation or its sources established. No ruling, approval or status from memory of what was probably said.
- An approval is recorded with its exact action, scope and environment. The manager holds none itself.
- Name the repository for every path, branch and PR.

## Finish

Print exactly two things:

1. The file path, and that it is safe to `/clear`.
2. A ready-to-copy resume prompt in a plain fenced block (not tagged `bash`; it is a prompt, not a command):

       I'm IN: before any other tool call, SendMessage "I'm IN" to the Asst. Manager (the session titled "Asst. Manager"); send it once more if another copy of this prompt arrives.
       Resume in <absolute session folder> as the manager of all Supervisor sessions.
       Read <absolute state-file path>, then read and follow
       <absolute session folder>/.claude/briefs/manager.md. Run on Fable 5.2 (claude-fable-5-2).
       The marching instruction in the state file binds you; it is verbatim in the brief, section 3.
       Read the rulings log, the comments on ClickUp task z8uq9med6x, newest first, then the task map before re-reading ClickUp. Do not re-explore anything already recorded in the register.

Then hand over automatically (manager brief, section 8; Coach Ed, 2026-11-13, comment 1100430000508710): SendMessage the Asst. Manager a message whose first line is "HANDOVER PROMPT", followed by the resume prompt; then `clear_session` "self" and end the turn at once, with no message queued (a clear is refused while a message is queued, and dropped if a new message arrives before the turn ends). The Asst. Manager resends the prompt every 10 seconds until it gets "I'm IN" (60 sends at most).

Fallback, when the Asst. Manager is not running or the clear is refused: say plainly `/clear`, then paste this. Coach Ed's paste is then required.
