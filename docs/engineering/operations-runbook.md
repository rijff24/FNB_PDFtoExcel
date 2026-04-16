# Operations Runbook

Owner: Engineering  
Last reviewed: 2026-04-15

## Pre-deploy checklist

- Confirm required env vars are present.
- Confirm auth providers and allowlists are correct.
- Confirm Document AI processor and region config.
- Confirm Redis and Firestore connectivity.

## Deploy flow

1. Build image
2. Push image to Artifact Registry
3. Deploy Cloud Run service
4. Apply/verify runtime env vars

See detailed command flow in [`deployment.md`](deployment.md).

## Post-deploy smoke checks

- `/` loads and sign-in is available.
- `/review` flow works end-to-end from upload preview.
- `/billing` loads and returns data.
- `/admin` loads for admin users.
- Export/download operations work.

## Rollback criteria

Rollback if any occurs:

- login blocked for valid users
- extraction failures spike
- billing endpoint failures
- severe UI regressions in review/admin

## Rollback action

- Redeploy previous known-good image.
- Re-run smoke checklist.
- Notify beta users if visible impact occurred.

## Daily beta operations

- Review error tracking in admin.
- Review reconciliation variance status.
- Check support queue for recurring issues.
- Log top issues and mitigation status.
