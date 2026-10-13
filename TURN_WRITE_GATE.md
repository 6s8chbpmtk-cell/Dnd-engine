# Mandatory turn-write protocol — v3.4 QA

**Do not treat a chat's claim that an Airtable event is `Verified` as proof.**
The field can be written manually. A mechanically verified event must independently
replay under the released Python engine from an authenticated, separately saved genesis.

## What was broken

An isolated cold-start exercise recorded 3 Airtable event rows for 6 mechanical
transitions: player movement, player attack, player end turn, NPC Disengage,
NPC movement, NPC end turn. Each ledger row contained readable prose in
`State Changes Before → After` instead of the canonical full event object:

```json
{
  "event_key": "<campaign-namespace>:<unique-event-id>",
  "expected_version": 0,
  "operation": "move",
  "params": {"actor_id": "<exact-actor-id>", "path": [[2, 2]]},
  "prior_hash": "<64-hex SHA256>",
  "after_hash": "<64-hex SHA256>",
  "committed_version": 1,
  "event_hash": "<64-hex SHA256>"
}
```

**The JSON above is a schematic, not a complete sample to paste or an
instruction to invent hashes.** The engine MUST produce `VerifiedEvent.to_dict()`
by applying the actual rule transition. Attack events carry full validated
dice payloads; linked Dice Audit v2 rows must match them byte-for-byte after
normalization. Writing a plausible summary or a hash of a summary is insufficient.

## Operator sequence for every new mechanical action

1. Identify the exact campaign and encounter records by Airtable ID; select
   exactly one edition and check that the caller authorized player intent.
2. Read all Session Logs, Event Ledger, Dice Audit v2, Campaign, Encounter and
   Combatant Turns v2 rows needed for the targeted encounter. Collect **every**
   connector pagination page and all required fields, never truncate a ledger.
3. Run `VerifiedTurnGateway.preflight()` against a full live AirtablePort.
   It must replay from the unique verified version-zero genesis and check the
   current checkpoint + actor projections. **Any failure means STOP.**
4. Determine the next **single** supported operation. An instruction like
   "move around rubble, then attack" means **at least two separate** engine
   `commit()` calls with distinct campaign-namespaced event keys and separate
   consecutive expected versions. `disengage`, `move` and `end_turn` are likewise
   separate. A single turn is not a single event.
5. Call `VerifiedTurnGateway.commit()` with the exact operation, parameters,
   explicit player intent for player-controlled characters, and real OS-backed
   dice payloads only when a roll is required. The gateway stages one
   structured Proposed event, rolls/audits, snapshot and version updates,
   player/NPC tactical projections, and final readback.
6. The gateway **replays the entire resulting history** and verifies the new
   state before returning success. Only then describe the action as saved.
7. On any uncertain write or failed post-check, do not re-roll, retry, or
   blindly resubmit. Inspect actual ledger, version and dice to reconcile.

For separate ChatGPT sessions using the Airtable MCP connector **without a
trusted Python writer**, use `scripts/verify_connector_export.py` on a complete
read-only field-ID export. It rejects malformed histories and returns
`HALT_UNVERIFIED`. This verifies state but **does not implement a connector
write gateway**. ChatGPT may narrate non-mechanical possibilities but MUST NOT
advance the battle until the condition is resolved or a new isolated campaign
is intentionally started. Do not mark damaged ledger entries "Verified" to
silence the alarm.

## Security boundary and remaining engineering work

This package is an application-layer guard, **not a permission enforcement
system**. With broad direct edit privileges, ChatGPT (or any Airtable user) can
still bypass Python and change Campaign, Encounter or Ledger records manually.
True enforcement requires:

- restrict game-state table writes to a dedicated write-capable service token;
- expose a narrow mutation endpoint that runs the gateway before/after writes;
- give chat sessions read access and allow writes only through that endpoint;
- coordinate/serialize concurrent requests (Airtable has no multi-record
  transaction or compare-and-swap); audit a separate append-only record where
  appropriate.

Until then, full D&D play, provenance of remote entropy, concurrency guarantees,
and unreplayable legacy checkpoints are **not certified**. v3.4 does not convert
prose-only ledger records into legitimate historical proofs.
