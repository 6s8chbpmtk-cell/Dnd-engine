"""Recover a v3 tactical battle ONLY from persisted Airtable records.

Pure read-only orchestration: obtain campaign-scoped genesis, verify all ledger
records and audited dice, and reconcile each independent combatant projection.
The tool controller supplies an authorized AirtablePort; no credentials here.
An empty or ambiguous query is an error, not permission to invent game state.
"""
from __future__ import annotations
from dataclasses import dataclass

from airtable_persistence import AirtablePort, BattleAnchor, PersistenceError
from combat_runtime import Battle
from history_verifier import HistoryReport, verify_full_history


@dataclass(frozen=True)
class RecoveryResult:
    battle: Battle
    history: HistoryReport
    genesis_record_id: str
    actor_row_ids: dict[str, str]


def recover_from_airtable(port: AirtablePort, anchor: BattleAnchor) -> RecoveryResult:
    """Fail closed unless a unique genesis, ledger and matching actors exist.

    Assumes a single reader has access to Campaigns, Encounters, Session Logs,
    Event Ledger, Dice Audit v2 and Combatant Turns v2. Does NOT lock out
    concurrent writers; caller must single-writer-coordinate and re-read before
    performing any subsequent write.
    """
    # Permit the canonical v3.3 identity and the original cold-start pilot's
    # session-scoped name; NEVER guess genesis from latest campaign checkpoint.
    expected_ids = {anchor.campaign_id + ':genesis',
                    anchor.campaign_id + ':session01:genesis'}
    genesis = [r for r in port.list('Session Logs')
               if r.get('fields', {}).get('Campaign ID') == anchor.campaign_id
               and r.get('fields', {}).get('State Version') == 0
               and r.get('fields', {}).get('Session Log ID') in expected_ids]
    if len(genesis) != 1:
        raise PersistenceError('Missing/duplicate explicit genesis log; recover manually')
    g = genesis[0]
    if not isinstance(g.get('id'), str) or not g.get('id'):
        raise PersistenceError('Genesis log is missing record ID')
    fields=g.get('fields', {})
    if fields.get('Verification Status') not in (None, 'Verified'):
        raise PersistenceError('Genesis session log is not Verified')
    if fields.get('Log Type') not in (None, 'Checkpoint'):
        raise PersistenceError('Genesis log has wrong type')
    raw=fields.get('State Reconciliation Notes')
    legacy=fields.get('Recap / What Happened')
    # Recap text is normally prose and MUST NOT override real serialized state.
    if not isinstance(raw,str) or not raw:
        raw=legacy
    if not isinstance(raw,str) or not raw:
        raise PersistenceError('Genesis log lacks serialized checkpoint')
    if isinstance(legacy,str) and legacy.lstrip().startswith('{') and legacy!=raw:
        raise PersistenceError('Genesis checkpoint fields disagree')

    # verify_full_history detects pending/duplicate/gapped events, tampering,
    # edition or campaign mismatch, missing/altered dice, and checkpoint drift.
    state, report = verify_full_history(port, anchor, raw)
    actor_ids = {a.actor_id for a in state.combatants}
    rows = [r for r in port.list('Combatant Turns v2')
            if r.get('fields', {}).get('Campaign ID') == anchor.campaign_id
            and r.get('fields', {}).get('Encounter ID') == state.encounter_id]
    if len(rows) != len(actor_ids):
        raise PersistenceError('Missing/extra combatant projection row; cannot start turn')
    mapping = {}
    for row in rows:
        f = row.get('fields', {})
        actor = f.get('Character or NPC ID')
        if actor not in actor_ids or actor in mapping or not row.get('id'):
            raise PersistenceError('Unknown/duplicate combatant projection')
        if f.get('Combatant Integrity') != 'VALID':
            raise PersistenceError('Invalid/missing Airtable combatant integrity formula')
        linked = f.get('Encounter Record Link', [])
        if not isinstance(linked, list) or anchor.encounter_record_id not in [
                item.get('id') if isinstance(item, dict) else item for item in linked]:
            raise PersistenceError('Combatant row not linked to the expected encounter record')
        mapping[actor] = row['id']
    # Use existing strict projection comparer from the Airtable bridge.
    from airtable_persistence import AirtableBattlePilot
    AirtableBattlePilot(port, anchor, actor_rows=mapping)._verify_actor_rows(state)
    return RecoveryResult(state, report, g['id'], mapping)
