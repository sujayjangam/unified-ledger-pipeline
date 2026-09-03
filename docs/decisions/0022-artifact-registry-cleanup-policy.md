# ADR-0022: Cap Artifact Registry to the 3 most recent container images, no age condition

**Status:** Accepted
**Date:** 2026-09-03
**Issues:** none (found via a billing investigation, not a filed issue)
**Code:** GCP repository config (not version-controlled) — `cloud-run-source-deploy` cleanup policy

## Context

A $0.19 August charge led to checking the Cloud Billing report, which showed Artifact Registry at
~30¢ for the month against Cloud Run's ~6¢ — the opposite of what the container-hosting service
would suggest. The `cloud-run-source-deploy` repository (holding the Docker images Cloud Build
produces on every push to `main`, per [ADR-0012](0012-github-actions-over-cloud-scheduler.md)'s
"deploy trigger is GCP-side and invisible from this repo") had **122 image versions, 3.5 GB, and
no cleanup policy** — every deploy since May 2026 had left its image behind permanently. The
initial suspicion was the `pg_dump` backup workflow, since its cadence (every 6 hours) had
recently increased in frequency of *commits* triggering redeploys; that was ruled out directly —
the GCS backup bucket is 440 KiB across 82 files, several orders of magnitude too small to be a
30¢ line item, and is an entirely separate GCP service (Cloud Storage) from Artifact Registry.

The real driver was deploy frequency, not backup frequency: this project's CI/test-suite work
(#31-#34) produced an unusually high rate of merges to `main` in a short window, each triggering a
fresh Cloud Build image push with nothing ever cleaning up the old ones.

There is a real failure mode to avoid: Cloud Run scales to zero when idle and re-pulls the
**currently active revision's** image from Artifact Registry to start a new instance. A cleanup
policy aggressive enough to delete that image would leave the bot unable to cold-start until the
next deploy rebuilds it — independent of whether GitHub can always rebuild a fresh image
eventually, since that requires a person to notice and push a fix, not something the running
service does for itself.

## Decision

A two-part cleanup policy on `cloud-run-source-deploy`:
- **Keep** the 3 most-recently-created image versions, unconditionally.
- **Delete** everything else, with no age condition (`condition: {"tagState": "ANY"}` — no
  `olderThan` value, matching by tag state alone).

3 was chosen over the minimum-safe value of 1 (Cloud Run only strictly needs the currently active
revision's image to survive) to leave a margin for the brief window during a deploy where the
outgoing and incoming revisions can both be receiving traffic, without relying on Artifact
Registry as a rollback mechanism — that role is explicitly GitHub's (revert the commit, redeploy)
in this project, per direct instruction, not Cloud Run's traffic-split revision history.

Dropping the 30-day age condition (present in an earlier draft of this policy) was deliberate: at
this project's current deploy cadence, "most recent 3" and "most recent 3 within 30 days" behave
identically in the common case, but the age condition adds a failure mode with no corresponding
benefit — a quiet month with fewer than 3 deploys would still correctly keep whatever exists, so
the age condition was pure complexity without changing behavior in the case that matters
(protecting the active revision) or the case being optimized for (bounding storage growth).

Verified before going live: the policy JSON was applied with `--dry-run` first and accepted by
GCP's schema validation (Artifact Registry's dry-run mode logs to Cloud Logging on its own
schedule rather than returning a synchronous preview, so that log wasn't waited on); separately,
the currently-deployed Cloud Run revision's image tag was confirmed to be the 3rd-most-recent by
creation time — inside the keep-3 window — before the policy was flipped live.

A BigQuery billing export dataset (`billing_export`, `asia-southeast1`) was also created during
this investigation so future charges are queryable by SKU directly, rather than requiring the
Cloud Console's Billing Reports UI. Linking it as the actual export target is a Console-only step
(no public API exists for it) and was handed to the user to complete; the dataset itself has no
meaningful cost until export data lands in it. This does not backfill historical data — it would
not have shortened this investigation had it existed in August.

## Alternatives considered

Age-based policy only (`olderThan: 30d`, no keep-count floor) — rejected because it doesn't
guarantee the active revision survives a quiet period with no recent deploys; a keep-count floor
is required for the safety property regardless of what age condition (if any) is layered on top.
Keeping 5 images (the first draft) — rejected as more margin than the stated rollback strategy
(git, not Cloud Run revision history) needs. Keeping 0 — rejected outright: breaks the
scale-from-zero cold start for the active revision, which is a functional bug, not a cost
optimization.

## Consequences

Artifact Registry storage is now capped at 3 image versions regardless of deploy frequency, so
this cost line stops growing — future deploy bursts (like the one that caused this) no longer
compound. The cost of that: a rollback beyond the 3 most recent deploys means git-revert +
redeploy (a few minutes, a fresh Cloud Build run) rather than Cloud Run's instant traffic-split
back to an existing revision. Given this project's low deploy-to-incident ratio and single
maintainer, that tradeoff was made deliberately rather than defaulting to Artifact Registry's
no-cleanup default, which is the actual cause of this 30¢ charge and would recur indefinitely
otherwise.
