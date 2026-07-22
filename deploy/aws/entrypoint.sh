#!/bin/sh
# FIX-25 (OPS-04 / ARCH-01) — fail-closed migrations at boot.
#
# The WEB role runs `alembic upgrade head` BEFORE serving. `set -e` makes a failed
# migration abort the container with a non-zero exit, so a staged bad migration
# can never reach prod serving traffic — the deploy smoke then fails and the
# rollback drill (deploy/aws/ROLLBACK.md) cuts back to the previous image tag.
# With `--restart always`, a *transient* DB blip just retries until it clears.
#
# The SCHEDULER role skips migrations (the web owns the schema; a single migrator
# avoids a concurrent-upgrade race). The cutover sets RUN_MIGRATIONS=0 for it.
set -e

if [ "${RUN_MIGRATIONS:-1}" != "0" ]; then
    echo "[entrypoint] alembic upgrade head…"
    alembic upgrade head
    echo "[entrypoint] schema at head."
fi

exec "$@"
