# ADR-0021: Enforce the merge gate with a repository ruleset, not classic branch protection

**Status:** Accepted
**Date:** 2026-09-03
**Issues:** [#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34)
**Code:** GitHub repository settings (not version-controlled) — ruleset `branch_main_protection`

## Context

[#34](https://github.com/sujayjangam/unified-ledger-pipeline/issues/34) required that a pull
request whose `check-PR-before-merge` status check fails cannot be merged into `main`, with a
literal verification criterion: `GET /repos/:owner/:repo/branches/main/protection` should no
longer return `404`.

GitHub currently exposes two separate systems for this: **classic branch protection rules**
(the older UI, under Settings → Branches) and **rulesets** (the newer UI, under Settings → Rules
→ Rulesets), which additionally support a **bypass list** — naming specific actors or roles
(e.g. "Repository admin") that are exempt from an otherwise-enforced rule. Classic protection's
equivalent is a single `enforce_admins` boolean with no finer-grained bypass.

This is a single-maintainer repo where the maintainer is also the repository admin. A hard
`enforce_admins: true` would leave no escape hatch for a legitimate emergency fix; the ruleset's
bypass list lets the "Repository admin" role bypass this specific rule without disabling the rule
itself or exempting anyone else.

The two systems turned out not to be API-equivalent: rulesets are surfaced only through
`GET /repos/:owner/:repo/rulesets`, not through the classic `.../branches/{branch}/protection`
endpoint — that endpoint reflects classic protection rules exclusively, active ruleset or not.
This was discovered live: after creating and activating the ruleset, `.../protection` still
returned `404`.

## Decision

Use a ruleset (`branch_main_protection`, target `main`) rather than classic branch protection:
- Required status check: `check-PR-before-merge`, with "require branches to be up to date"
  (`strict`) enabled.
- Bypass list: role `Repository admin`.
- Also enabled: block force pushes, restrict deletions.
- No required reviewers, signed commits, linear history, or CODEOWNERS — out of scope per #34.

#34's literal verification text (`.../branches/main/protection` returning non-`404`) is treated
as **written against the wrong endpoint** rather than as a hard requirement to satisfy verbatim.
The actual verification performed was behavioral, against a throwaway PR (#47): a failing check
produced `mergeStateStatus: BLOCKED` and `gh pr merge` was rejected with "the base branch policy
prohibits the merge" (naming the `--admin` override, confirming the bypass list is wired to admin
privilege as intended); fixing the failure flipped the same PR to `mergeStateStatus: CLEAN`. The
PR was closed unmerged, not merged, to keep the throwaway commits out of `main` history.

## Alternatives considered

Classic branch protection with `enforce_admins: true` (satisfies the literal `.../protection`
endpoint check, but removes the admin's own bypass — rejected per explicit request to keep one).
Classic branch protection with `enforce_admins: false` (satisfies the endpoint check, but the only
bypass control is all-or-nothing for admins — the ruleset's role-scoped bypass list is strictly
more precise for the same intent).

## Consequences

`GET /repos/:owner/:repo/rulesets` is the correct endpoint for scripts or future ADRs that need to
confirm this protection exists — `.../branches/main/protection` will keep returning `404` for this
repo indefinitely, and that is expected, not a regression to chase.

If GitHub ever deprecates one system in favor of the other, this record is what to check first.
