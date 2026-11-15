---
description: Route approved ready work through release train, or verify staging
argument-hint: "[check]"
---

Production excluded. No tags/production branch, migrations, seeds, variables/settings, manual redeploy/up/down, secrets or branch-protection bypass.

Normal mode: inspect requested change/status and follow the existing role's ready-branch/release procedure. Feature builders report branch/head/checks to Supervisor; Supervisor reviews/local-tests/routes owner packet and reports ready to Manager; only Release Builders combine/push/open the one round PR after all required local CI checks. This command creates no separate PR, merge, build or deployment approval. Manager runs its existing train. Do not unilaterally commit unrelated work. No routine owner progress/completion announcements; explicit /push-staging request may receive its requested result.

check mode (read-only): identify the intended merged PR/commit, not merely newest deployment. Verify:
1. PR merged and exact merge SHA.
2. Railway project negosyoonline-backend/environment staging/service api; matching deployment meta.commitHash is SUCCESS.
3. scripts/verify_deploy.sh https://api-staging.negosyonline.com/healthz/ <12-character merge prefix> reports OK for that commit.
4. Latest relevant Verify staging deploy run, read once.
Disagreement means unverified/failed, never substitute a different commit. Capture relevant failure logs without secrets; no redeploy permission inferred.
Use supported background deployment wait if needed, scripts/wait_railway_deploy.sh <validated BEFORE_ID>, once; failure/crash/timeout is reported honestly internally or as requested. Do not create CI polling loops/timers or rerun CI to refresh. Existing release workflow controls CI recovery. Report intended environment, PR/merge SHA, deployment ID/status, observed health SHA and outstanding checks; only claim deployed when matching SUCCESS and health OK exist.
