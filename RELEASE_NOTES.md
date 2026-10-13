# v3.2 QA — October 13, 2026

**Status: QA staging. Not released to GitHub yet.**

## Verified changes

- Adds tactical `Fighter`, `Battle` and pure verified transition/replay engine.
- Adds separate `initiative.py`, including explicit tie-resolution requirements.
- Adds Airtable persistence adapter with synthetic fault/timeout and acknowledgement-loss tests, dice audit projection, optional per-combatant row reconciliation, and conservative readback requirements.
- Adds read-only checkpoint/replay CLI and 100 randomized synthetic multi-round battle replays and targeted security/invariant tests.
- Tightens dice expression metadata, die-shape validation, justified advantage requirement, map coordinate typing, unsupported mechanical action refusal, and blocks the unverified opportunity-attack boolean bypass.
- Latest local test result: **186/186 passing** under available local Python. Separate prior GitHub core v2 workflow: **39/39 passing**; v3.2 code is not on GitHub yet.
- Real QA Airtable pilot first verified three sequential state events, including actual OS-backed melee attack and linked dice audit. Separately created two per-combatant QA records that exactly reconcile with the checkpoint. An overlapping candidate action was refused on stale preflight; no live game campaign state was edited.

## Remaining verification gates

1. Manually upload/review v3.2 files to the public GitHub repository (the current ChatGPT GitHub integration rejects repository writes with 403). Verify all GitHub Actions Python-matrix jobs.
2. The 12-event/10-dice three-round GM-only Airtable persistence test passed. A separate controlled test of concentration, true encounter victory/defeat completion and actual reaction resolution is still required.
3. Perform a genuinely separate-chat cold start and verify exact recovery without relying on chat memory.
4. Implement and verify opportunity reaction, Dodge, Extra Attack, spells, 0 HP/death saves and more 5e rules modules.
5. Introduce an external atomic commit coordinator for concurrent session writers; no claim of race safety until then.
6. Confirm Airtable automation alerts through explicit review; all four currently remain undeployed.

## Security

No real campaign secrets or Airtable credentials are included in this public-ready package. SHA-256 state/dice digests detect accidental modifications, but **are not signatures nor cryptographic proof of OS randomness**. Publish only source and synthetic tests, never player records.

## v3.2 addendum

- A full three-round GM-only Airtable QA sequence was persisted through 12 Verified events and 10 reconciled dice rolls; a fresh remote connector read matched all event hashes, versions and roll evidence. Details in `QA_V4_MULTIROUND.md`.
- Persisted an initial v0 QA genesis checkpoint in Session Logs; new `history_verifier.py` independently replays full historical events from that origin, comparing the replay to the latest state and all dice audits.
- Added `scripts/verify_airtable_history.py` read-only REST verifier, eight exact-scenario replay tests and additional omission/tamper/continuity tests. Total: 186 local tests passing.
- Same-chat cold data reload proved; genuinely separate conversation cold-start remains to be tested.
