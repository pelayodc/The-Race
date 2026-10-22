# SoloQ Challenge

## Offline Simulation

Run `python3 scripts/simulate_challenge.py` from the repository root. Open
`simulation-report/index.html` to inspect the final ranking, checks and counted
match histories. `simulation-report/report.json` contains the full simulated
edition, audit trail and task scenarios for deeper inspection.

The simulation uses the production competition rules with three fictional players
and an accelerated clock: registration, both weeks, 17 games per player per week
(only the first 15 count), duplicate delivery, consecutive attacks, FIFO tasks,
all eight challenge types, failed/missing evidence, moderation and reconciliation.
It never imports storage or API clients, changes real data, or sends Discord
messages. It requires only Python, without tokens or PostgreSQL.

Use `--seed 7` for a different set of results or `--output /tmp/challenge-demo`
to choose the report directory. Failed checks return a nonzero exit status.
GitHub Actions also runs the simulation and publishes its report as the
`soloq-challenge-simulation` artifact. This validates competition rules, not live
Discord permissions, channel creation, database connectivity or Riot responses;
those still need integration tests and a manual test on a separate Discord server.

## Opening an Edition

Run `/soloq_challenge` in the server. A member with Manage Server can open a new edition when no edition is active. PostgreSQL is required. The bot needs Manage Channels, View Channel, Send Messages, Embed Links and Read Message History.

An edition has 72 hours of registration followed by two consecutive seven-day competition weeks, measured from its actual opening time. No Monday alignment is applied. Participants must register again for every edition. Previous editions remain stored; starting a new one does not carry players, points or shells forward.

The bot creates a SoloQ Challenge category containing `sq-registration` and an administrator-only `sq-admin`. After registration it creates `sq-ranking`, `sq-shells` and `sq-history`, then deletes only that edition's registration channel. Channel and message IDs persist across restarts. The private channel grants access to roles with Manage Server or Administrator; menu actions also check the invoking member's current permission.

## Registration and Counting

Select the account platform and enter a Riot ID plus a comma-separated list of champions owned and available for ranked on that account. Account names and champion names are resolved to canonical identifiers. The account may be primary, secondary, or separate from the normal leaderboard.

An account already linked to the same Discord user is approved automatically. Other accounts need administrator approval during registration; the public match API does not prove account ownership or expose a complete owned-champion inventory. Participants attest that their declared champion list is accurate. Accounts and champion pools become fixed when registration closes. One PUUID can only belong to one participant in an edition.

Only the first 15 valid queue-420 games **started** in each competition week count. Unused slots do not roll over. Games started before a boundary and finished afterwards belong to their start week. Remakes shorter than five minutes with an early-surrender flag are excluded. Normal losses, surrender games, and games with failed challenges still consume slots.

The collector paginates the full edition match window, processes oldest first and records processed match IDs. A failed older-match request stops that player's scan rather than allowing newer games to fill the quota. Extra games are remembered but receive no points, shell rewards, challenge completions or public history entries.

At the end, scoring enters reconciliation for at least 24 hours. It remains pending if any approved participant has not been successfully scanned past the closing time or has a disputed/failed shell awaiting moderation. Final standings therefore do not silently omit games after an API outage or ignore outstanding disputes. The collector retries every two minutes and shares Riot backoff; its own per-cycle request budget is 35% of `REQUESTS`. It prioritizes participants with the oldest successful scan.

## Points

| Condition | Default points |
| --- | ---: |
| Victory | +10 |
| Defeat | 0 |
| Kills greater than deaths | +1 |
| Kills plus assists less than deaths | -1 |
| At least one quadra or penta | +1 total |
| MVP The Race | +1 |

Equal KD/KDA is neutral. A negative KD alone is not penalized. The existing The Race match MVP calculation is used across all participants; this is not an OP.GG or DPM rating. A game can award at most 13 default points. Standings use challenge points, not LP or an estimate of MMR. Equal-point ordering by Discord ID is display order, not a sporting tie-breaker.

Administrators can configure the six point values, inventory cap, failure penalty and enabled challenge IDs during registration. Settings lock at competition start. The weekly limit remains 15.

## Earning and Using Blue Shells

A counted game awards **at most one** shell if it meets any of:

- Five consecutive counted victories.
- A quadrakill or pentakill.
- At least 20 kills.
- Zero deaths.
- At least 30 assists.

All qualifying reasons are saved in the match record. The visible win/loss streak continues across weeks. The separate five-win reward counter resets every five victories or on defeat; receiving a shell for another reason does not reset it. With the default cap of three, rewards earned with full inventory are lost, including the reward at the fifth win.

From the private Blue Shell menu, select another approved participant and explicitly launch a shell. There is no waiting period between received attacks and no reverse mechanic. Targets whose current weekly quota is full cannot receive attacks. Shells received before a match starts apply in arrival order, one per counted game. A shell arriving during a game cannot affect that already-started game.

Spin roulette to reveal and fix the assignment. Reopening the menu never rerolls it. Playing the next counted game without revealing a received shell sends it to moderation; it does not bypass the shell. A failed or disputed oldest challenge blocks later challenges until resolved. Other counted games still earn their normal points and record the blocker.

The pending, completed and everyone views paginate shell records. Standings show available shells, unrevealed shells, pending challenges, signed win/loss streak and weekly/total games. History contains counted games only, with point breakdowns, reward reasons and challenge state. Moderation updates are reflected when the history message is next refreshed.

## Challenge Validation

| Challenge ID | Rule and evidence |
| --- | --- |
| `new_champion` | A declared champion not previously used in counted games of this edition; eligible choices freeze at assignment. |
| `random_role` | One assigned role, compared with Riot's inferred `teamPosition`. Mismatch or uncertainty goes to review rather than automatic punishment. |
| `no_boots` | No purchase of any item tagged Boots in Data Dragon for that match patch. Selling or undoing a purchase does not erase it. |
| `random_champion` | One random champion from the participant's declared pool. |
| `no_starter` | No purchase, at any time, of a configured starter item. Default IDs: 1054, 1055, 1056, 1082, 3865, 1101, 1102, 1103. Component items outside this explicit list remain permitted. |
| `sender_main` | Sender's most-played champion in counted games of this edition, frozen at assignment; ties use champion name. Excluded from the draw if the sender has no games or the target has not declared that champion. |
| `no_flash` | Neither summoner spell is Flash. |
| `no_last_three` | A declared champion outside the participant's three most recent counted matches at assignment. |

Impossible pool-based choices are excluded from the draw. Bans, autofill, incomplete timelines, unknown patch catalogs and other disputed conditions require moderator judgment. Riot APIs cannot prove intended lane selection or account ownership. Automatically awarded boots are not purchases; their presence alone does not fail a no-purchase rule.

Restrictions must be agreed by participants and reviewed against Riot's current developer and game-integrity policies before enabling the event. No challenge changes the objective of winning the match, and the bot does not validate or encourage intentional feeding.

## Moderation

In the administrator tab, select an unverified registration to approve it. Select a participant to use `remove` or `points:+N` / `points:-N` with a reason. Removing during registration deletes the enrollment; after the start it excludes the participant from collection and standings while retaining evidence. Individual manual adjustments are limited to 100 points per action and audited.

Select a shell and provide one of these resolutions with a reason:

- `completed`: accept fulfillment; remove any automatic failure penalty on that match.
- `waived`: excuse the challenge; remove any automatic failure penalty.
- `assigned`: retry an already assigned challenge on a future counted match, retaining the same task.

Default automatic failure penalty is zero. Administrators resolve ambiguous evidence explicitly instead of the bot guessing. Purchase IDs, spells, inferred role and timeline/catalog availability are retained as evidence for assignments checked against a match. A participant who skips roulette can be excused or manually sanctioned; retry requires an existing assignment.

The bot records other registered participants on the same team as an indication only. **This is not proof of DuoQ**, and never creates an automatic penalty. Administrators must obtain independent evidence before applying a points adjustment.

## Persistence and Recovery

The `solo_challenges` PostgreSQL table stores one locked document per Discord guild, including all editions, account PUUIDs, settings, match summaries, shells, moderation audit and Discord delivery IDs. Changes use row-level transactions and cannot be erased by the legacy leaderboard's whole-state saves. JSON backups include `soloChallenges`; empty-database legacy import can restore that group.

The two-minute background task is registered as `soloQChallenge`. Failures are logged without stopping future cycles. Discord message delivery is retried; history messages include a stable user/match marker to reconcile sends interrupted before their IDs were saved. Keep backups before database recovery or manual edits.
