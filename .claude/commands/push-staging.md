---
description: Ship the current backend change to staging through a PR, wait for the Railway deploy, then report whether staging deployed
argument-hint: "[check]"
---

Ship the current backend change to staging and report the result to Coach Ed without being asked again.

Staging deploys automatically when a PR merges to `main`. Production is never part of this command.

## Hard limits (never do these here)

- No `v*` tag, no push to the `production` branch, no migrations, no seed data, no Railway variable or repo setting changes, no `railway redeploy`/`up`/`down`.
- Never print or commit secrets. Do not bypass branch protection.
- Do not poll GitHub CI (no timers, loops, `gh ... --watch`, repeated status reads). The only waiting allowed is the Railway wait script below, run once in the background.

## If the argument is `check`

Skip to "Verify and report" using the most recent merged backend PR (`gh pr list --state merged --limit 1`).

## Otherwise: ship it

1. Inspect `git status` and the diff. If there is nothing to ship, say so and stop. Confirm the change is what Coach Ed asked for and contains no secrets.
2. If on `main`, create a feature branch. Commit with a clear message ending with the required Co-Authored-By line. Push the branch.
3. Run the targeted tests and `uv run python manage.py check --deploy` if code changed. Stop and report if they fail.
4. Railway guard: `railway status` must show Project `negosyoonline-backend` and Environment `staging`. Anything else, stop and tell Coach Ed. Then record the newest staging deployment id as BEFORE_ID:
   `railway deployment list -p negosyoonline-backend -e staging -s api --limit 1 --json` (the `id` of the first row).
5. Open a ready (not draft) PR into `main` with a short summary and test plan, ending with the required Generated-with line.
6. Bind the PR with `mcp__ccd_pr__get_status` (use `bind_pr` if it is not reported), then enable auto-merge with `mcp__ccd_pr__set_auto_merge` (squash). Coach Ed has standing permission for this on backend PRs.
7. Start the wait in the background, as a script file and never an inline `bash -c`:
   `bash scripts/wait_railway_deploy.sh <BEFORE_ID>` with `run_in_background: true`.
   It returns only when Railway has a newer staging deployment that finished, so it also covers CI and the merge. Tell Coach Ed the PR link and that you are waiting on the deploy, then end the turn. You are notified when the script exits. Do not announce a result before that.

## When the wait script exits (or on `check`)

- Exit 0 `SUCCESS <id> <commit>`: go to "Verify and report".
- Exit 2 (FAILED/CRASHED/REMOVED/SKIPPED): report the failure with the deployment id. Pull the failing lines with `railway logs -p negosyoonline-backend -e staging -s api -d -n 100 <id>` (read-only) and do not redeploy.
- Exit 3 (about 20 minutes, no finished deployment): read the PR state once and report honestly that it is still in progress: CI stuck or GitHub Actions down (see githubstatus.com), merge pending, or Railway still building. Tell Coach Ed to run `/push-staging check` later. Never claim a verdict you do not have.

## Verify and report

1. Read the merged PR's merge commit with `gh pr view <n> --json state,mergeCommit`. If it is not merged yet, say so and stop.
2. Confirm Railway's deployment is for that commit: in `railway deployment list -p negosyoonline-backend -e staging -s api --limit 5 --json`, the row whose `meta.commitHash` equals the merge commit must have status SUCCESS. No matching row means Railway has not deployed it: say so, never substitute the newest row.
3. Run `scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <first 12 chars of the merge commit>`. This is the proof the live app reports the commit.
4. Read the latest `Verify staging deploy` run once with `gh run list --workflow "Verify staging deploy" --limit 1`.
5. Report plainly: PR link, merge commit, Railway deployment id and status for that commit, the commit staging reports, and whether the verify run is green. If any of them disagree or fail, say it failed and what was observed. Never claim success without Railway SUCCESS for the merge commit and the `OK:` line.
