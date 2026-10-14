# Narrative RPG Engine — tactical core v3.5 QA

This repository is a **strict, conservative Python rules helper** for a ChatGPT-run, Airtable-backed narrative RPG campaign. It is **not** a complete D&D 5e implementation, autonomous GM, VTT, or always-on server. Game state is persisted in Airtable, not in model text or in this repository. No personal campaign notes, API credentials, or real character inventories belong in this public codebase.


## v3.5 QA — action-restricted gateway prototype

`gateway_service.py` offers an authenticated, narrow HTTP interface to the
v3.4 replay-and-commit gateway. GPT Actions may read verified tactical state
or submit **one** supported operation (move, Disengage, end turn, limited
adjacent melee). Fake/client-supplied rolls, arbitrary Airtable writes and
compound operations are rejected. Melee attack dice are generated
server-side with Python OS entropy and committed with the event.

This service is **not deployed by this repository**, so publishing the source
**does not enforce** writing restrictions. Real enforcement also requires
a dedicated Airtable writer credential, authenticated hosted service,
Action-only custom GPT, and restricted competing write access. The first
prototype is **QA only**, without atomic multi-table transactions or complete
5e legality. See `DEPLOY_GATEWAY.md` before configuring a host.

## v3.4 strict turn-write gateway

The new `write_guard.VerifiedTurnGateway` adds a **full genesis-to-current
history replay before each supported mutation and again after it**. One
mechanical operation must produce one `VerifiedEvent` with canonical JSON
and a unique idempotency key. Composite narration may require multiple
successive events, never a collapsed "move+attack+end turn" record. Any
unreplayable legacy ledger halts gameplay with **no write attempts**.

`recovery_bootstrap.py` now accepts the actual cold-start test's uniquely
Verified `:session01:genesis` Session Logs record and the canonical `:genesis`
name, including a real `battle-v3` stored in `State Reconciliation Notes`.
The initial prepared or active v0 status is verified without silently
skipping the actor check when active.

**Not a secure write boundary:** a chat that retains direct Airtable write
permissions can still bypass the Python gateway. Production safety requires
a sole authorized coordinator with restricted write access. See
`TURN_WRITE_GATE.md` and `BLACKGLASS_REGRESSION.md`. Existing test-campaign
records were not modified by this release.

## Supported mechanics (narrow, tested subset)

- OS-backed random dice with raw/kept/discarded faces, modifiers, roll IDs, UTC timestamps, and digest consistency checks. Test-injected dice are explicitly marked and refused on live persistence paths.
- D&D 5e 2014/2024 tagged single-d20 comparisons and natural-1/20 attack treatment; concentration DC and death-saving-throw helpers (not a complete death-saving state machine in live combat).
- Initiative bootstrap from independently verified d20 results with explicit adjudication of ties.
- A narrow battle state machine: explicit square-grid paths, movement budgets, obstacles, difficult terrain, initiative turn order, melee/ranged single weapon attacks, HP and temporary HP, a deliberately limited concentration response, and action/reaction bookkeeping.
- Event-key/version/hash-checked local ledger and exact deterministic replay of synthetic battles.
- Strict read-only `recovery_bootstrap.py` loads a canonical genesis Session Log and validates the complete historical ledger, dice audits, and independent per-combatant Airtable projections before restoring turn state.
- A read-only ChatGPT Airtable connector bridge (`connector_readonly.py`) that translates complete field-ID keyed tool responses into the strict history recovery engine. It aborts on incomplete pagination, stale actions, missing actors or invalid linked records; CLI: `scripts/verify_connector_export.py`. This is not a background sync.
- A non-atomic Airtable adapter: proposed event → optional dice audits → encounter checkpoint → campaign version → optional per-combatant projections → read-back → verified event. On any uncertain write, STOP and reconcile instead of assuming a rollback.
- Unit/integration/synthetic stress tests using no live campaign writes.

## Run the tests

```bash
python -m unittest discover -s . -p 'test_*.py' -v
```

The QA release was tested locally with **255 passing unittest cases**, including **100 synthetic multi-round encounters** with independent exact replay. GitHub Actions runs Python 3.11, 3.12 and 3.13 on every push. The v3.5 package is locally tested; publishing these changes will trigger the remote matrix.

## Verify a complete ChatGPT Airtable connector export (read-only)

```bash
python scripts/verify_connector_export.py /local/private/connector_export.json \
  --campaign-record recCAMPAIGNID --encounter-record recENCOUNTERID \
  --campaign-id qa:example --edition 2014
```

The JSON file must have `schemas` and `pages` mappings for all six required tables. Use the schema response from `list_tables_for_base` and **every page** of `list_records_for_table`. Only the operator should have this local file: it may contain private game details. Never commit it to GitHub. This tool performs no Airtable writes. It verifies tactical state only, not spell/scene/character-sheet completeness.

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

See `OPERATOR_PLAYBOOK.md`, `RECOVERY_PLAYBOOK.md`, `QA_EVIDENCE.md` and `RELEASE_NOTES.md` before attempting a campaign.

## Known non-negotiable limits

No atomic compare-and-swap across Airtable tables, so concurrent writers are not safe. Use one writer per campaign; the mock adapter does **not** prove the REST gateway can bypass Airtable restrictions. After playing, the main Character/NPC sheets and inventory still require reconciliation with the tactical snapshot. One writer per campaign is required and operational coordination is still essential. Opportunity attacks cannot be bypassed with a boolean claim; an actual reaction resolution path remains unimplemented. Extra Attack, most spells/features, full cover/conditions, reactions such as Shield, death/injury edge cases, and long-rest/XP/resource completeness remain outside the currently proven tactical subset. Character sheets and rules-source verification remain necessary before admitting a real character to play.

**Passing tests ≠ a fully playable engine.** This release is a QA staging foundation, not a declaration of D&D 5e completeness.
