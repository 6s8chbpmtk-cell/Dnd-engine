# v3.4.0 QA — fail-closed turn-write gateway

**Status:** source built and unit-tested locally on October 13, 2026; pending GitHub publication and independent live-gateway integration. **Not a complete D&D 5e engine.**

## What's new

- `write_guard.py` adds `VerifiedTurnGateway` with strict, read-only history replay **before and after every supported tactical write**. If any previous event, dice audit, actor projection or version fails full verification, the gateway refuses the next write.
- Preserves **one mechanical transition per canonical `VerifiedEvent`**. Move, attack, end turn, Disengage, move, end turn are six events, not a three-record prose summary. The new six-transition test replays the exact history and verifies the active combatant and round.
- `recovery_bootstrap.py` now recognizes the actual original cold-start campaign's uniquely verified, version-zero Session Logs identity `<campaign-id>:session01:genesis`, with exact JSON saved in `State Reconciliation Notes`. The standard `<campaign-id>:genesis` format still works. A prose recap is not a valid checkpoint.
- `history_verifier.py` accepts initial version-zero active encounters and validates their active actor; their version and complete history remain mandatory.
- `scripts/verify_connector_export.py` returns machine-readable `HALT_UNVERIFIED` and `UNREPLAYABLE_EVENT_PAYLOAD` when a connector export contains malformed historical events.
- Regressions reproduce the observed Blackglass compound/prose-only event failure, incomplete/duplicate/stale ledger, orphan dice, unverified genesis, unapproved player actions, and uncertain post-write behavior.
- Includes the previous unpublished v3.3 read-only connector bridge (`connector_readonly.py`, `recovery_bootstrap.py`) and its adversarial tests, so it is a **complete upgrade from the public v3.2**.

## Evidence

- **242 / 242 Python `unittest` executions passed locally** on Python 3.13, including 100 deterministic-seed synthetic multi-round battles and an independent synthetic twelve-event/replay fixture.
- No real Airtable record was modified while developing v3.4; the original Blackglass failure remains intact as evidence.
- No live Airtable gateway write or fresh separate-chat enforcement has been tested for v3.4.
- The three-version GitHub Actions matrix is configured but its **v3.4 result is pending publication**.

## Known limitations and safety

Airtable multi-record writes are **non-atomic**, and the application-layer gateway does not enforce access permissions. A ChatGPT conversation that still has direct table-write permission can bypass it. A trusted single-writer service with exclusive Airtable write credentials is required to close that bypass. SHA digests demonstrate internal consistency, not independent proof of OS randomness. Full D&D 5e legality, spells, conditions, reactions and death rules remain incomplete. Do not auto-rewrite the original unreplayable ledger to make it appear compliant.

See `TURN_WRITE_GATE.md`, `BLACKGLASS_REGRESSION.md`, `OPERATOR_PLAYBOOK.md` and `RECOVERY_PLAYBOOK.md`.
