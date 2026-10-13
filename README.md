# Narrative RPG Engine — tactical core v3.2 QA

This repository is a **strict, conservative Python rules helper** for a ChatGPT-run, Airtable-backed narrative RPG campaign. It is **not** a complete D&D 5e implementation, autonomous GM, VTT, or always-on server. Game state is persisted in Airtable, not in model text or in this repository. No personal campaign notes, API credentials, or real character inventories belong in this public codebase.

## Supported mechanics (narrow, tested subset)

- OS-backed random dice with raw/kept/discarded faces, modifiers, roll IDs, UTC timestamps, and digest consistency checks. Test-injected dice are explicitly marked and refused on live persistence paths.
- D&D 5e 2014/2024 tagged single-d20 comparisons and natural-1/20 attack treatment; concentration DC and death-saving-throw helpers (not a complete death-saving state machine in live combat).
- Initiative bootstrap from independently verified d20 results with explicit adjudication of ties.
- A narrow battle state machine: explicit square-grid paths, movement budgets, obstacles, difficult terrain, initiative turn order, melee/ranged single weapon attacks, HP and temporary HP, a deliberately limited concentration response, and action/reaction bookkeeping.
- Event-key/version/hash-checked local ledger and exact deterministic replay of synthetic battles.
- A non-atomic Airtable adapter: proposed event → optional dice audits → encounter checkpoint → campaign version → optional per-combatant projections → read-back → verified event. On any uncertain write, STOP and reconcile instead of assuming a rollback.
- Unit/integration/synthetic stress tests using no live campaign writes.

## Run the tests

```bash
python -m unittest discover -s . -p 'test_*.py' -v
```

The QA release was tested locally with **186 passing unittest cases**, including **100 synthetic multi-round encounters** with independent exact replay. GitHub Actions runs the same tests under Python 3.11, 3.12 and 3.13 after publication.

## Full-history recovery and current QA evidence

`history_verifier.py` replays every Airtable Event Ledger row from a persisted v0 genesis checkpoint, verifies the current campaign/encounter state, and reconciles every OS-backed dice audit. A pre-recorded **12-event/10-roll, three-round** GM-only QA fixture and its regression tests are included under `fixtures/`. See `QA_V4_MULTIROUND.md`. This verifies one small class of mechanically supported battles, not full D&D 5e gameplay.

```bash
python scripts/verify_airtable_history.py --campaign-record REC --encounter-record REC --campaign-id NAME --edition 2014 --genesis-file checkpoint.txt
```

The REST script reads Airtable using environment credentials only, performs GETs, and fails closed. Do not set a real PAT inside public source code. ChatGPT connector use requires a separate operator workflow.

## Read-only checkpoint recovery example

```bash
python scripts/replay_checkpoint.py checkpoint.json --events subsequent_events.json
```

This validates a `battle-v3` snapshot and sequential event hashes locally. If `--events` is omitted, it simply summarizes the checkpoint. It never accesses or changes Airtable.

## CLI dice example

```bash
python scripts/roll_cli.py d20+5 --kind attack --target 15 --edition 2014
```

This emits machine-readable JSON. It does **not** write to Airtable by itself.

## Operational model

1. Choose a specific campaign and its locked edition (2014 or 2024). Never infer or switch editions.
2. Load the current Airtable campaign, encounter, unresolved events, selected rules and verified scene facts.
3. Obtain the player's explicit action. The GM controls NPCs; it never invents a player's action.
4. Verify 5e preconditions, map geometry, cover/LOS, reactions and any relevant character traits before rolling.
5. Execute genuine dice with `engine_core.roll`; reject test-injected dice in live event writes.
6. Generate a proposed state transition; check version, checkpoint fingerprint and event uniqueness.
7. Commit the event and related records sequentially and read them all back before narrating a finalized outcome.
8. On a version conflict, duplicate event or uncertain network acknowledgment, halt and reconcile from Airtable. **Never blindly retry a write.**

See `OPERATOR_PLAYBOOK.md` and `RELEASE_NOTES.md` before attempting a campaign.

## Known non-negotiable limits

No atomic compare-and-swap across Airtable tables, so concurrent writers are not safe. Use one writer per campaign; the mock adapter does **not** prove the REST gateway can bypass Airtable restrictions. After playing, the main Character/NPC sheets and inventory still require reconciliation with the tactical snapshot. One writer per campaign is required and operational coordination is still essential. Opportunity attacks cannot be bypassed with a boolean claim; an actual reaction resolution path remains unimplemented. Extra Attack, most spells/features, full cover/conditions, reactions such as Shield, death/injury edge cases, and long-rest/XP/resource completeness remain outside the currently proven tactical subset. Character sheets and rules-source verification remain necessary before admitting a real character to play.

**Passing tests ≠ a fully playable engine.** This release is a QA staging foundation, not a declaration of D&D 5e completeness.
