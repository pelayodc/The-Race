# Security and Performance Review

Code review of Discord entry points, private sessions, account linking,
subscriptions, challenge collection/rules, matchmaking, persistence, external
requests, Docker build inputs and GitHub workflows. Findings below distinguish
implemented corrections from follow-up work. This is not a penetration test or
a dependency vulnerability certification.

## Corrected

- **High: missing command authorization.** `/add`, `/remove` and
  `/admin_matchmaking` now require administrator authorization independently of
  feature toggles. Forced `/patch` checks are also restricted and rate-limited.
- **High: shared state accessible from another server.** Slash commands and
  administrator permission checks now restrict access to `DISCORD_GUILD_ID`, or
  the guild of the configured cached `DISCORD_CHANNEL`. Unknown guilds fail closed.
- **High: unverified account reservations.** A verified account owner can replace
  pending registrations claiming that PUUID; an approved registration cannot be
  overwritten. Replacement is audited.
- **High: stale subscription snapshots.** Confirming a DM now updates only the
  subscription value transactionally in PostgreSQL. The legacy JSON path reloads
  state after the DM before saving. This fixes the reproduced loss of an unrelated
  account added during DM delivery, but not all legacy snapshot races below.
- **High: destructive integration test configuration.** The PostgreSQL fixture
  rejects database names other than `test_*` or `*_test` before initializing or
  dropping tables. Operators must still supply a disposable database and user.
- **Medium: duplicate matchmaking starts.** An in-process lock rejects concurrent
  team/channel creation. Join/leave operations stop during that transition. This
  is not a distributed lock or a guarantee across multiple bot instances.
- **Medium: voice channel permission exposure.** Generated team channels preserve
  source/category overwrites instead of explicitly granting everyone access.
- **Medium: excessive CI credentials.** Tests use read-only repository permissions
  and checkout without persisted credentials. Badge publication runs in a separate
  narrowly scoped write-enabled job after successful tests.
- **Availability hardening.** Legacy HTTP requests now have bounded timeouts;
  Riot transport failures return the existing failure tuples and record sanitized
  errors. PostgreSQL connections, pool waits, statements and row locks have bounded
  waits. Champion icon downloads use HTTPS. New summoner lookups URL-encode Riot
  IDs and pass the token in a header instead of the URL.
- **Docker build hygiene.** Environment variants, local credential/tool folders,
  logs and simulation output are excluded from the build context.

## Optimizations Implemented

- Challenge history recovery scans the channel once per publication batch rather
  than once per missing message ID, preserving crash-recovery markers.
- Challenge collection rotates the first guild each tick to prevent a fixed guild
  from consuming the shared budget every cycle. Budget cleanup runs in `finally`,
  and a failing guild no longer prevents the next guild from being processed.
- Channel IDs are persisted only when they change, avoiding recurring whole-document
  challenge writes for unchanged channels.
- Subscription writes target a single runtime value and use a PostgreSQL row lock
  rather than rewriting summoners, matches, timelines and unrelated settings.

## Follow-Up Work

1. **High: legacy snapshot writes remain unsafe with overlapping writers.**
   `storage.save_state` replaces four tables from a caller's complete dictionary.
   Other workflows still hold snapshots across awaits, so a later save can erase
   newer updates, including a targeted subscription update. Replace snapshots with
   targeted transactions or optimistic revisions. Until then, run one bot instance;
   that reduces but does not eliminate in-process races.
2. **High availability risk: synchronous work remains on the event loop.**
   Legacy Riot collection, image generation and SQLAlchemy operations can still
   stall Discord handling, despite bounded waits. Introduce an async repository/
   HTTP layer or worker ownership for complete transactions. Offloading arbitrary
   snapshot writers independently would increase the race risk above.
3. **Medium: unbounded cache and challenge document growth.** Every legacy load
   reads cached matches/timelines, while each challenge edit rewrites all editions
   for the guild. Separate editions/records/deliveries, add retention policies,
   and update only changed records.
4. **Medium: repeated historical collection.** Each player scan still paginates
   the whole edition window. Add an incremental cursor with an overlap window and
   recovery tests before reducing the scan range. Rotation improves fairness but
   does not replace distributed rate limiting or guarantee freshness under load.
5. **Hardening: runtime and supply chain.** The Docker image still runs as root.
   Moving to a non-root user requires verifying writable asset/cache paths and
   existing mounts. Review pinned dependencies with a current vulnerability scanner,
   pin action/container digests, and configure branch protection separately.

## Verification and Limits

Automated regressions cover account-claim replacement, command authorization,
server restrictions, admin challenge entry, subscription stale-write reproduction,
safe test database selection, request timeouts, overlapping team creation,
private voice permissions, batched history recovery and scheduler fairness.
The PostgreSQL suite includes targeted-update isolation and rollback tests.

Local tests and the offline challenge simulation do not validate live Discord
permissions or Riot behavior. PostgreSQL integration tests require a dedicated
database and run in CI; they were not executed locally without that service.
No production deployment, live load test, secrets inspection or current dependency
advisory scan was performed. Workflow permissions must also be checked against
the actual repository settings after publication.
