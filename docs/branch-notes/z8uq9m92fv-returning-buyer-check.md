# z8uq9m92fv returning-buyer check

Authority: Manager routine decision of 23 Nov (a bug against the approved design). No migration, settings or wording change.

## Cause
`apps/pack/checkout.py` `check_contact` (POST /storefront/v1/checkout/check/): `match_existing` (line ~110) accepts a
typed pair that is the same member with no benefits and no live order, but its result was discarded (line ~166). The
per-field loop then flagged each field through `if paired or benefits_active(member) or live_order(member) is not None`
(line ~177), so a returning buyer's own mobile and email both came back "already registered" while contact/ accepted them.

## Fix
`check_contact` keeps the member `match_existing` returns when paired and it did not raise. The loop does not flag a field
whose member is that returning member; it still goes through `members.require_free_elsewhere`. Unchanged: benefits or a
live order still refuse; a different member's mobile/email still flags; a single field of a non-abandoned member with no
benefits or live order is not judged until the pair is typed; abandoned members behave as before. Docstring and comment updated.

## Tests
`apps/pack/tests/test_checkout.py` `ReturningBuyerCheckTests` (6): own pair answers {} and contact/ accepts it; own mobile
with another member's email flags; own email with another member's mobile flags; single field not judged; own pair with a
live order flags; own pair with active benefits flags. The first fails without the fix.

## Checks
- apps.pack: 675 tests OK (5 skipped)
- full suite `manage.py test --parallel`: 5990 tests OK (74 skipped)
- `manage.py check`: no issues; `makemigrations --check --dry-run`: no changes

## Other
ClickUp subtask pending (MCP limit).
Paired FE branch: feat/z8uq9m92fv-checkout-fixes (the phone layout part); no FE change is needed for this bug.
