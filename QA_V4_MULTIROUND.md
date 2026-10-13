# D&D engine v3.2 — real Airtable multi-round QA (2026-10-13)

## Scope

An isolated D&D 5e 2014 test campaign and encounter used **two GM-controlled synthetic fighters**. This is not a real user choice, player session, or full D&D 5e rules engine. The scenario uses a flat, unobstructed 5-foot grid, one basic weapon attack per turn, verified attack reach, and manually marked reaction windows; no opportunistic rules shortcuts.

## End-to-end results

- **12 sequential Airtable events** were created as `Proposed`, checkpointed, and independently read back, then marked `Verified`.
- **10 individual dice rolls** (attack and damage) were generated with Python `secrets.randbelow` / OS-backed CSPRNG and recorded in linked Airtable Dice Audit v2 rows; each row was checked for MATCH arithmetic and VALID RANGE and marked `Reconciled with Event`.
- Three full initiative cycles were recorded. At version 12 the next turn begins round 4. The encounter remains `Active` (both fighters alive; QA was a multi-round persistence/turn cycle test, not a victory/defeat test).
- Saved version **12**, round **4**; guardian **39/45 HP**, raider **27/42 HP**. Final fingerprint `77c4825103bddfe0ba4ae55c8e298f5e4cdfc658d52daf1669449f57ad4d4034`.
- An **independent fresh connector read** reloaded Campaign, Encounter, all 12 ledger rows and all 10 audit rows, checked every stored event hash and expected version, and matched every audit expression, total, digest, arithmetic and status against the independently locally replayed action plan: `PASS_FULL_COLD_READBACK`. This was in a new tool invocation in the same conversation—not a separate ChatGPT conversation cold start.
- The initial version-0 checkpoint was stored in an isolated Airtable Session Logs record so that a new conversation can replay without constructing a genesis state from chat memory.
- An interrupted tooling pass left event 2 staged with encounter v2 but campaign v1; another left event 11 `Proposed` at current v10. Both were recovered conservatively by inspecting exact event identities, hashes and dice; neither involved a duplicate event or reroll. These incidents demonstrate recovery behavior, **not Airtable transaction guarantees**.
- No real player campaign state was edited. Four Airtable QA alert automation drafts remain disabled.

## Offline regression and recovery proof

- `fixtures/qa_3round_gm.json`: synthetic real-entropy QA event plan, complete initial and final checksums, and all 12 events and 10 roll histories. It does not contain Airtable credentials or real player information.
- `history_verifier.py`: starting from saved genesis and the linked full ledger, rejects duplicate/missing/unverified/tampered events, mismatching current snapshots, orphan/altered/mislinked dice rows, incompatible rules and version gaps. This does not prove cryptographic authenticity of an attacker-controlled Airtable database; SHA is only consistency checking.
- `scripts/verify_airtable_history.py`: **read-only** optional CLI using `AIRTABLE_TOKEN` and `AIRTABLE_BASE_ID` from the environment (never hard-coded).
- Local result: **186/186 automated Python tests passed** under the available Python interpreter (3.13). The earlier public GitHub repository still has the v2 core with **39/39 passing** on GitHub Actions; the v3.2 package has not yet been uploaded or CI-verified there.

## Remaining blockers before broad strict D&D 5e play

1. Publish v3.2 source to GitHub and observe GitHub Actions matrix completion. GitHub connector writes are rejected with 403; upload via a write-capable route or user GUI.
2. Perform a *separate-chat* full recovery that reads the genesis Session Log and all remote events; a same-chat fresh tool invocation is not equivalent to that test.
3. Implement actual opportunity attacks and reactions (Shield), spellcasting/slots, conditions, Dodge/Ready/Extra Attack, 0 HP/death saves and more 2014/2024-specific rules.
4. Add durable reconciliation of main Character/NPC records, inventory/XP and encounter projections, not only checkpointed tactical state.
5. Provide a trusted execution service with serialized writer lock/transaction coordinator. Airtable's API cannot make the multi-table workflow atomic, and the check-then-write protocol is unsafe with concurrent writers.

**Readiness: QA Complete / Not Play-Ready**, not unrestricted RAW-complete combat.
