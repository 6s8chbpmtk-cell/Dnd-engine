# ChatGPT + Airtable D&D Engine — QA Operator Playbook

## Source of truth

- Airtable Campaigns: the campaign identity, edition lock, canonical state version and recovery summary.
- Airtable Encounters: checkpointed `battle-v3` state, round, active combatant, matching version.
- Airtable Event Ledger: intent, state delta, event hash, idempotency key, before/after versions, proposed/verified status.
- Airtable Dice Audit v2: each actual OS-backed roll, raw/kept dice, source, digest, audited result, linked event.
- GitHub: versioned engine source and tests, **never the current campaign state**.
- ChatGPT narration is not authoritative without a verified Airtable event for mechanical outcomes.

## Mandatory preflight for every gameplay turn

1. Resolve the exact Campaign and Encounter by their Airtable IDs. Confirm their Campaign IDs agree and status is correct. Read last verified edition and **all unresolved events** for that campaign.
2. Parse the Encounter checkpoint. Recompute its state fingerprint; compare the Campaign and Encounter version fields to checkpoint.version. If any mismatch, STOP.
3. Inspect player control: never select/spend a player action without explicit intent. NPC autonomy applies only to GM-controlled actors.
4. Verify stat block completeness and geometry. Check range, cover, obstacles, movement budget, spell/action requirements and reaction windows. Decline unsupported features instead of inventing resolution.
5. Roll with real OS-backed `engine_core.roll` exactly when requested by the action and rule. Do not reroll to get a convenient outcome. `TEST INJECTION` dice are never authorized for live combat.
6. Propose a single versioned event with a campaign-namespaced unique key. Run local `apply_verified()` from the exact current checkpoint. Validate every relevant die and resulting battle state.

## Non-atomic Airtable write protocol (one writer only)

1. Create Event Ledger record with `Commit Status=Proposed`, precise idempotency key, `Mutation Fingerprint`, event JSON in `State Changes Before → After`, and expected before/after versions.
2. For any dice, create Dice Audit v2 records linked to the proposed event, including raw/kept/discarded faces, total, source and fingerprint.
3. Update Encounter state snapshot, round, active combatant, and version.
4. Update Campaign state version. If per-combatant records are configured, update every associated Combatant Turns v2 row to the exact same state version. Independently read Campaign, Encounter and those rows back.
5. Verify exact event uniqueness, checkpoint fingerprint and version, dice arithmetic and audit relationships. Mark Dice Audit records `Reconciled with Event`.
6. Mark Event Ledger `Verified`, then read it back. Only now may ChatGPT narrate a committed mechanical outcome.
7. Any failure/timeout at any stage is **WRITE UNCERTAIN**. Stop, inspect source records and reconcile the single intended event before any further move. Do not create a second event or reroll.

## Cold-start recovery

1. Read the active Campaign and the latest verified Encounter; never trust chat-only state.
2. Check unresolved event proposals and inconsistent campaign/encounter/combatant-row versions. Stop if either exists.
3. Validate checkpoint digest and its exact profile/version. Independently check the latest event after_hash equals checkpoint fingerprint and the before/after version relationship; if necessary, replay full event chain from known initialization checkpoint.
4. Load player and NPC sheets, initiative, spell slots and other resources **from Airtable**. A tactical checkpoint alone does not prove those are complete.
5. Show a short canonical status summary (location, turn/round, HP, conditions, action economy, slots/resources, XP and coin when tracked); request action only after recovery is clean.

## The October 13 QA pilot

Isolated QA campaign `qa:combat:pilot` (not an actual user-play campaign) successfully committed synthetic end-turn, move and OS-backed melee attack as versioned events 1 through 3. The bandit's real roll was d20=18 +3 vs AC16, then d6=4 +1 damage. Two source-linked Dice Audit records were reconciled, the final independent combat fingerprint matched, and campaign/encounter versions both became 3. During testing a second candidate event saw a version mismatch and STOPPED before writing: this is a useful concurrent-writer safety regression, **not evidence Airtable supports atomicity**.

## Explicit missing components

- No trusted always-on runner/CAS transaction service. Current tool connections are not a continuous gameplay server.
- No complete D&D 5e class, spell, condition, terrain and reactions implementation.
- No reliable procedural legal spell selection or inventory/XP ledger synchronization with tactical transition yet.
- Cold-start restart must be validated in a wholly separate ChatGPT conversation before production use.
- Draft Airtable alerts are intentionally disabled pending review to avoid unexpected usage and notifications.


## v3.4 MANDATORY: fail-closed history before *every* write

Never commit a turn directly by editing Airtable fields from the ChatGPT
connector. `AirtableBattlePilot` by itself validates a local write but does not
replay all historical events before every write. Authorized automated writers
MUST call `VerifiedTurnGateway.commit()` from `write_guard.py`, which reloads
unique verified genesis, all events, dice audits and projections, then replays
from genesis before and after committing exactly one operation. Reject a
prose-only ledger, even when `Commit Status=Verified` and the latest checkpoint
fingerprint matches. No speculative rerolls or auto-repair.

**A chat with unrestricted direct Airtable writes can bypass this guard.**
This is an operator contract for ChatGPT until a dedicated service enforces
write permissions. Full deployment needs a sole token holder and serialized
mutation endpoint. See `TURN_WRITE_GATE.md`.
