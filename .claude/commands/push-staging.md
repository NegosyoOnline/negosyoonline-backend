---
description: Ship the current backend change to staging through a PR, then report whether staging deployed
argument-hint: "[check]"
---

Ship the current backend change to staging and report the result to Coach Ed.

Staging deploys automatically when a PR merges to `main`. Production is never part of this command.

## Hard limits (never do these here)

- No `v*` tag, no push to the `production` branch, no migrations, no seed data, no Railway variable or repo setting changes.
- Never print or commit secrets. Do not bypass branch protection.
- Do not poll CI with timers, loops, `gh ... --watch` or repeated status reads. Read status once per step and stop.

## If the argument is `check`

Skip to "Verify and report" using the most recent merged backend PR (`gh pr list --state merged --limit 1`).

## Otherwise: ship it

1. Inspect `git status` and the diff. If there is nothing to ship, say so and stop. Confirm the change is what Coach Ed asked for and contains no secrets.
2. If on `main`, create a feature branch. Commit with a clear message ending with the required Co-Authored-By line. Push the branch.
3. Run the targeted tests and `uv run python manage.py check --deploy` if code changed. Stop and report if they fail.
4. Open a ready (not draft) PR into `main` with a short summary and test plan, ending with the required Generated-with line.
5. Bind the PR with `mcp__ccd_pr__get_status` (use `bind_pr` if it is not reported), then enable auto-merge with `mcp__ccd_pr__set_auto_merge` (squash). Coach Ed has standing permission for this on backend PRs.
6. Read the PR status once. If it already merged, continue below. If CI is still running, stop and tell Coach Ed the PR link, that GitHub will merge it when CI is green, and to run `/push-staging check` afterwards.

## Verify and report

1. Read the merged PR's merge commit with `gh pr view <n> --json state,mergeCommit`. If it is not merged yet, say so and stop.
2. Run `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <first 12 chars of the merge commit>`.
3. Read the latest `Verify staging deploy` run once with `gh run list --workflow "Verify staging deploy" --limit 1`.
4. Report plainly: PR link, merge commit, the commit staging reports, and whether the verify run is green. If staging does not report the commit, say it failed and what was observed. Never claim success without the `OK:` line.
