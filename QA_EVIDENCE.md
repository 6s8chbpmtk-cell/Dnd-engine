# QA evidence — tactical engine v3.2

## Automated tests

- Python 3.13.5 in the local tool runtime, October 13, 2026.
- 186 / 186 `unittest` tests passed. Includes 100 randomized synthetic multi-round battles with precise event-ledger replay and checkpoint restoration.
- Fake Airtable transport tests simulate lost acknowledgments, interrupted writes, stale versions, duplicate event keys, altered digests, inconsistent actor projections and injection of test dice.
- `scripts/replay_checkpoint.py` validates and summarizes a checkpoint offline, optionally replaying a serialized ordered event chain.

## Real Airtable connector integration (isolated QA only)

An isolated QA campaign (not a live game) had three consecutively verified state changes from version 0 to 3: synthetic player end-turn, autonomous bandit move, and an actual OS-backed melee attack. The third action used the first generated d20 face 18 + 3 = 21 against AC 16, and d6 face 4 + 1 = 5 damage, changing the synthetic fighter's HP 12 to 7. Both dice audits independently returned MATCH and VALID RANGE, with the engine event hash, checkpoint and campaign/encounter versions agreeing. Two Combatant Turns v2 QA records matched the checkpoint in all core tracked fields.

Two actual Airtable formulas had zero-value defects: State Delta Check erroneously flagged version 0 as unversioned; Resource Quantity Guard treated quantity 0 with blank charges as UNTRACKED. Both were corrected and independently tested against valid and invalid controls.

The original QA initiative values were fixed synthetic order (not source-certified dice); the new `initiative.py` builder enforces genuine d20 sources for future play.

## Unproven/blocked gates

- GitHub currently hosts core v2 with a successful 39-test workflow, not this newer source. Upload and GitHub CI validation are still pending.
- No Airtable multi-row atomicity or cross-writer race safety.
- No independent cryptographic proof of random source, only OS-backed execution claims and digest consistency.
- No genuinely fresh-chat cold restart verification.
- No full SRD 5e spell/class/condition/reaction completeness.
- Four Airtable integrity automations remain intentionally disabled.
- The Airtable REST adapter uses injected environment credentials only; the in-chat Airtable plugin path is separately orchestrated, not an always-on server.


## v3.2 live Airtable QA extension

- In a *separate GM-only test campaign*, persisted 12 Verified Ledger events over 3 completed rounds; final tactical version 12, round 4, one fighter 39/45 HP and other 27/42 HP, still Active.
- Linked 10 OS-backed CSPRNG rolls reconciled to those events; each passed Airtable MATCH/VALID RANGE formula checks.
- Verified two interrupted staged writes were recovered without rolling again or duplicating ledger events.
- Read the remote Airtable data in a fresh independent connector invocation: 12 events plus 10 audited rolls exactly matched the same initial/final checkpoints and locally replayed chain. Source genesis v0 persisted in a QA Session Log.
- Added `history_verifier.py` and 21 new recovery/CLI/fixture tests. Current local total: 186/186 passing. Real external separate-conversation cold start remains an unpassed gate.
- See `QA_V4_MULTIROUND.md` for exact scope and explicit unsupported full 5e mechanics.
