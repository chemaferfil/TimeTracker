# Render Migration Runbook: Oregon -> Frankfurt

This runbook covers the safe migration of the TimeTracker Render stack from Oregon to Frankfurt:

- existing web service
- existing PostgreSQL database
- supporting cron job / scheduled automation

The goal is to move production traffic with a short cutover window, while keeping rollback simple.

## Scope

Current live setup as confirmed from Render:

- Web service: `TimeTracker`
- Database: `TimeTracker_db`
- Current region: `oregon`

Target state:

- Web service in `frankfurt`
- Postgres in `frankfurt`
- All environment variables recreated on the new service
- Old Oregon resources kept intact until verification is complete

## Safety Rules

- Do not delete or suspend the Oregon resources until the Frankfurt stack has been verified.
- Do not reuse any hardcoded database URL in the application.
- Do not commit secrets into the repository.
- Use a new backup file for each migration attempt.

## What To Copy

Create the new Frankfurt service with the same application behavior and copy these environment variables into it:

- `DATABASE_URL` for the new Frankfurt Postgres
- `AUTO_CLOSE_TOKEN`
- `TZ`
- `PYTHON_VERSION`
- `FLASK_ENV`
- `APP_TIMEZONE`

If the deployment uses any other Render secret or env var, copy it as well. The rule is simple: the new service should have the same runtime configuration as the current one, except for the new Frankfurt database URL.

## Recommended Migration Flow

1. Create a new PostgreSQL database in `frankfurt` with the same major version as production.
1. Create a new web service in `frankfurt` from the same Git repository.
1. Keep the old Oregon service and database running.
1. Generate a logical backup from the Oregon database.
1. Restore that backup into the Frankfurt database.
1. Point the new Frankfurt web service at the Frankfurt database.
1. Validate login, time entry creation, history views, exports, and the auto-close flow.
1. Switch traffic to the Frankfurt web service.
1. Leave the Oregon stack available for rollback until the new stack is stable.

## Backup And Restore

Use a machine that has PostgreSQL client tools installed.

### Backup

Prefer a custom-format dump:

```bash
pg_dump --format=custom --no-owner --no-privileges --file timetracker_oregon.dump "$SOURCE_DATABASE_URL"
```

If you need a plain SQL backup instead:

```bash
pg_dump --no-owner --no-privileges --file timetracker_oregon.sql "$SOURCE_DATABASE_URL"
```

### Restore

For a custom-format dump:

```bash
pg_restore --no-owner --no-privileges --clean --if-exists --dbname "$TARGET_DATABASE_URL" timetracker_oregon.dump
```

For a plain SQL dump:

```bash
psql "$TARGET_DATABASE_URL" -f timetracker_oregon.sql
```

### Validation After Restore

Run a few checks before cutover:

- row counts for the main tables
- a few known users
- recent time records
- admin views
- export functionality
- the scheduled auto-close endpoint

## Environment Checklist

Set these on the new Frankfurt web service:

- `DATABASE_URL`
- `AUTO_CLOSE_TOKEN`
- `TZ`
- `PYTHON_VERSION`
- `FLASK_ENV`
- `APP_TIMEZONE`

If the new Render service uses a temporary or internal database URL during restore, make sure the final value is the Frankfurt Postgres URL before going live.

## Cutover

1. Put the application into a short maintenance window if needed.
1. Confirm the Frankfurt database is fully restored.
1. Confirm the Frankfurt web service starts cleanly.
1. Confirm the app is using the Frankfurt `DATABASE_URL`.
1. Move users to the Frankfurt web URL or update the Render service mapping.
1. Watch logs for authentication failures, DB connection errors, and scheduler errors.

## Rollback

If the Frankfurt stack fails before the switch:

- keep the Oregon service live
- fix the Frankfurt service
- rerun restore if needed

If the Frankfurt stack fails after the switch:

- repoint the service back to the Oregon `DATABASE_URL`
- send traffic back to the Oregon web service
- keep the Frankfurt resources intact until you understand the failure

Only delete the Oregon resources after the Frankfurt stack has been stable for long enough that rollback is no longer needed.

## Verification Checklist

- application loads
- user login works
- clock-in and clock-out work
- history pages load
- exports work
- admin pages load
- scheduled auto-close still works
- logs do not show the old Oregon database URL

## Notes

- If you use a Render Blueprint, set the region explicitly to `frankfurt` for each service.
- If any code path still contains a hardcoded database URL, remove it before the cutover.
- If the production service uses separate cron behavior, verify the cron job is also recreated in `frankfurt`.
