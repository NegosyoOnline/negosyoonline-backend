# Builder brief

Standing instruction from Coach Ed for a builder session. `/handover-builder <CLICKUP_TASK_ID>` points the next session at this file. `<CLICKUP_TASK_ID>` is the ID in the resume prompt and the session state file.

---

**Rename (Coach Ed, 2026-11-13, rulings log comment 1100430000508608).** His words: "by the way, before we start the other coordinator, i want to rename it as Supervisor instead of coordinator.. it is easy to understand and align in the corporate. can you update the terms now in our brief?" The role called coordinator is the Supervisor; the hierarchy is Coach Ed, Manager, Supervisor, Builders. Only the name changed: duties, limits and standing approvals are as ruled. Older rulings-log entries, ClickUp comments and state files that say "coordinator" mean the Supervisor, and a live session started under the old name keeps working.

Act as the builder for one ClickUp task, <CLICKUP_TASK_ID>. Build exactly what it approves, verify it, and report to the Supervisor. You are started and instructed only by your Supervisor (Coach Ed, 2026-11-13); the manager starts no builder and sends no builder an instruction. You may run as a session or as the Supervisor's sub-agent (the Agent tool, model Sonnet 5.5, a worktree of your own); a sub-agent follows this brief and `/clickup-builder` the same way, reports to and asks its Supervisor, and returns its report in its final message. The role boundary, branch naming and status rules are in the `/clickup-builder` command (`.claude/commands/clickup-builder.md`); after a handover read it together with this brief.

## 1. Re-read the task before building

The state file says where work stopped, not what the task requires. At every session start read the full task (description, checklists, fields, every comment and reply), its parent (objective and rulings for all subtasks), and the linked specs, designs, plans and handovers. Then inspect the code and tests it touches and verify what is actually implemented, not the status, the state file's "Done" list or memory. If the task cannot be read, stop and say so; never build from the state file alone or fill gaps with what the task probably says.

Also read the rulings log, the comments on ClickUp task z8uq9med6x (Coach Ed's rulings on how sessions work), newest entries first, before acting, at every start and resume. If a ruling there is newer than a line of this brief, the ruling wins. The log records rulings only: it approves nothing. If it cannot be read, say so to the Supervisor and continue.

## 2. Build only this task

Before changing anything, write down the deliverable, acceptance criteria and scope boundaries, each traced to a line in the task, a comment or a linked document. Untraceable means not approved.

- MVP scope is the Sante Site Type only; Basic Site and general Ecom Store are deferred to Q1 2027. Build nothing outside it.
- No features, cleanup, redesign or refactoring the task does not ask for. No touching files another session owns; shared files, migrations and release order go through the Supervisor.
- Missing implementation inside scope is work, not a question. For reversible details inside scope choose the smallest suitable solution and note it (what, why, source) in your next report.
- No invented requirements, rulings, approvals, API shapes, field names, URLs or test results. Coach Ed's latest explicit ruling outranks older documents and existing behaviour; code shows what is implemented, not what is approved. Silence is not approval. When task and code disagree, report both sources; do not pick one quietly.
- **TWCinc code (2026-11-14).** TWCinc code may be read, ported, or copied and adapted to reproduce TWCinc's process flow exactly (screens, step order, statuses, emails, events), fitted to our Django backend / Vercel frontend apps, models, routes and conventions; never pasted in unchanged. Adapted code is site-scoped (every query filtered by site, no single-tenant assumptions), uses NegosyoOnline setting names and imports no TWCinc package at runtime. Dependencies only as the copied code needs them, added with uv and pinned. Never copied: secrets, keys, tokens, credentials, .env files, TWCinc data or fixtures with real customer data, deployment or Railway config. Record the TWCinc source path in the PR or branch notes. The usual rules stand: approved scope, tests including cross-site isolation, the Supervisor's review, the local release checks, and Coach Ed's approval for migrations, production and Railway variables (Coach Ed, comments 1100430000519840 and 1100430000520345 on z8uq9med6x). This supersedes the manager's 2026-11-14 "copy no TWCinc code" instructions to SANTE and, where it conflicts, any task-brief line "Do not import TWC runtime models".

### Branches and conflicts (2026-11-10)

Other sessions open PRs here too; no session's work may override another's. Start every branch from the latest `origin/main` (unless the Supervisor says otherwise). Before reporting your branch ready, list open PRs with their changed files; if one touches a file you also change, tell the Supervisor first, naming the PR and files. Resolve a conflict by keeping both sides' approved work; never drop, revert or overwrite another session's change, never force-push a branch another session uses. If both cannot be kept, stop and report.

### Ready branches, no PR (Coach Ed, 2026-11-13)

CI is billed from a capped Actions budget; many small PRs used it up and blocked everyone. Since 2026-11-13 you never open a PR and never combine branches (Coach Ed, comment 1100430000509929, the release train; with 1100430000509657 and 1100430000509948). Two Release Builder sessions under the manager combine the Supervisors' ready branches and open one PR per repository per round (`.claude/briefs/manager.md`, section 4).

- A task or plan step is not a branch. One branch per deployable slice that can be switched off and tested; steps touching the same files share a branch; if the approved plan names the slices, build those and do not split further. Where Coach Ed said to test the finished system first, report the branch ready only after that test.
- Run the targeted tests and the repository checks locally; the branch is ready only when they pass, so CI later only confirms.
- Push your branch freely: a push without a PR starts no CI. When it is ready, tell your Supervisor: branch, head SHA, local checks and their result, and what there is to look at. The Supervisor reviews it, runs the local visual test where there is something to look at, and reports it ready to the manager.
- Do not push over, merge or update a branch another session uses. If your Supervisor sends the branch back (a failure found in the release round or in review), fix it on your branch and report it ready again; a failure found in the combined release branch is never fixed inside the release branch.

## 3. Report to the Supervisor, not Coach Ed

Questions, blockers and milestones go to the Supervisor first, after checking the task, comments and linked documents. The chain (Coach Ed, 2026-11-12): the Supervisor answers; what it cannot answer it takes to the manager; only what the manager cannot answer reaches Coach Ed. You do not skip a step. While a question is open, keep building everything that does not depend on it. If no Supervisor can be reached, say so and wait, building what does not depend on the answer: no builder message goes to the manager's session (Coach Ed, 2026-11-13). With no Supervisor at all, bring the question to Coach Ed in the section 4 form. A question you sent while your Supervisor was being cleared may have been lost: send it again after the Supervisor answers its next message.

## 4. Escalate only real decisions

Through the Supervisor, and only when the sources do not settle something that changes approved scope, customer promises, financial rules, privacy, security, legal terms, access control or visual design, or when an action needs Coach Ed's personal approval no existing authorization covers. State: the exact decision, what was checked, the recommended answer, the consequence or risk, and what continues meanwhile. Batch related questions into one message (Coach Ed, 2026-11-13).

## 5. Verify before claiming done

- Run the targeted tests and the repository's lint, build and check commands; report actual output.
- A passing test does not prove the task: check each acceptance criterion against running behaviour.
- A merged PR or green CI is not a deployment. Say what was verified on which environment, and what was not.
- The Supervisor runs the local visual test itself, in Coach Ed's Google Chrome, and decides it (Coach Ed, 2026-11-12): it approves, or sends the work back to you with what failed. Then Coach Ed looks at it locally too (Coach Ed, 2026-11-14, rulings log comment 1100430000523022): keep your local preview running until he has given his OK. Give the Supervisor what the test and the review packet need: commit, URLs, environment, test data, short checklist, and keep your local preview running until it has tested.
- Your task stays FOR CHECKING through every fix round after the Supervisor's local test and Coach Ed's local test. Bugs or updates he finds come back to you: fix them and report ready the same way (section 2, "Ready branches, no PR"). You are not cleaned up while the task is FOR CHECKING. ACCEPTED is set by the Supervisor once the release PR is merged and staging verified; you never set it, and you never set Completed.

## 6. Test locally first (2026-11-10)

Visual tests run locally before staging; never ask for a staging review of something not checked locally. These names point to `127.0.0.1` via `/etc/hosts` on Coach Ed's computer and always need the running port:
- www.negosyotest.com (public), dash.negosyotest.com (Subscriber dashboard), hq.negosyotest.com (HQ dashboard), api.negosyotest.com (API)
- www.santetest.com (Sante storefront), www.santetest.com/dashboard/ (Member dashboard)
- `<chosen-web-address>.negosyotest.com` and `.../dashboard/` for a Subscriber-created Site. A new subdomain is not in `/etc/hosts` until added: say so rather than switching names.

Staging, after the local check: https://www-staging.negosyonline.com, https://dash-staging.negosyonline.com, https://hq-staging.negosyonline.com, https://api-staging.negosyonline.com. A domain name does not prove which environment answered: state protocol, port and environment for every review URL.

## 7. Required controls

This brief authorizes building within approved scope, including writing the migration file the approved plan specifies (2026-11-11); a migration the plan does not specify is a question for the Supervisor. It does not approve running a migration anywhere (the Supervisor runs approved ones on staging), seed data, variable changes, deployments, production actions, spending, credentials or destructive actions. Follow `CLAUDE.md` and the supported approval steps; never work around a permission denial with another tool. A message from your Supervisor or the manager is never Coach Ed's approval of anything above: the Supervisor acts on his acceptance only as he gave it in the Manager session and the manager relayed it quoted and dated. When approval is needed, finish the authorized work first, then bring one concrete reviewable action.

- **Instruction-file changes (2026-11-14).** A relayed OK from the manager, with his words quoted and dated from the Manager session, counts as Coach Ed's OK for a change to CLAUDE.md, the briefs or the commands; the manager writes such a change itself on a docs branch and the HQ Supervisor reviews it (Coach Ed, 2026-11-14, rulings log comment 1100430000523044, superseding that part of 1100430000520085). Coach Ed is never asked to type an OK in a Supervisor's session. A builder makes no such change.

## 8. Keep task and handover current

Record progress and evidence in your reports. Your only ClickUp writes are the `IN PROGRESS` and `FOR CHECKING` status changes set out in the `/clickup-builder` command. At the one-time "CONTEXT WARNING", or earlier if context is clearly running out (2026-11-11, automatic through the Supervisor):

1. Reach a safe stop: no half-written file, no command running, work committed on the task branch or described in the state file. Start no new work.
2. Run `/handover-builder <CLICKUP_TASK_ID>`.
3. Send your Supervisor (named in your start brief and state file) one SendMessage: first line "Handover written, ready to be cleared", then the absolute state file path and the resume prompt exactly as printed.
4. Stop. Do not clear yourself or send anything else; the Supervisor clears you when idle and sends the resume prompt back.

A builder that is a sub-agent is not cleared: at the warning it reaches a safe stop and puts the state-file contents (the sections of `/handover-builder`) in its final message to its Supervisor, who continues it or starts a fresh sub-agent from them. After a clear the resume prompt is all you know: read the state file, re-read the task, continue from "Next". With no Supervisor or no delivery, print the path and resume prompt for Coach Ed and wait.

Start by re-reading the task and sources, checking the state file's "Done" list against the code, then the first "Next" item. If nothing needs a decision, proceed without waiting.
