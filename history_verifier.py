"""Strict read-only full-history recovery for Airtable battle checkpoints.

Requires a separately retained *genesis* checkpoint. The latest checkpoint is
not trusted until every committed event after genesis has been replayed, with
its dice audits matched one-for-one to the exact roll payloads. This method
never writes, never rerolls, and cannot prove Airtable atomicity or genuine
randomness independently. Operates on either RESTPort or a mock AirtablePort.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from engine_core import Roll, SOURCE, validate_roll, RulesError
from combat_runtime import Battle, VerifiedEvent, replay
from airtable_persistence import (
    AirtablePort, BattleAnchor, PersistenceError,
    decode_checkpoint, _stored_event, roll_to_audit_fields,
)

@dataclass(frozen=True)
class HistoryReport:
    campaign_id: str
    encounter_id: str
    edition: str
    checked_events: int
    checked_rolls: int
    state_version: int
    checkpoint_fingerprint: str
    round_no: int
    status: str


def verify_full_history(port: AirtablePort, anchor: BattleAnchor,
                        genesis_checkpoint: str) -> tuple[Battle, HistoryReport]:
    """Reject any missing, extra, pending, corrupted, or noncontiguous record.

    The operator MUST obtain genesis from a persisted, independently selected
    source, such as a Session Log v0 record, not invent it from current state.
    No unaudited extra campaign rolls are permitted in the restricted pilot.
    """
    genesis = decode_checkpoint(genesis_checkpoint)
    if genesis.version != 0 or not genesis.encounter_id:
        raise PersistenceError('Genesis must have version zero and an encounter ID')
    if genesis.edition != anchor.expected_rules:
        raise PersistenceError('Genesis edition differs from locked campaign edition')
    campaign = port.get('Campaigns', anchor.campaign_record_id)['fields']
    encounter = port.get('Encounters', anchor.encounter_record_id)['fields']
    if (campaign.get('Campaign ID') != anchor.campaign_id or
            encounter.get('Campaign ID') != anchor.campaign_id or
            encounter.get('Encounter ID') != genesis.encounter_id or
            campaign.get('Game Mode') != 'D&D 5e' or
            campaign.get('Rules Profile') != {
                '2014': 'D&D 5e 2014 / SRD 5.1',
                '2024': 'D&D 5e 2024 / SRD 5.2.1',
            }[anchor.expected_rules]):
        raise PersistenceError('Cross-campaign / edition / encounter mismatch')

    records = [r for r in port.list('Event Ledger')
               if r.get('fields', {}).get('Campaign ID') == anchor.campaign_id]
    seen_keys: set[str] = set()
    parsed: list[tuple[VerifiedEvent, str]] = []
    for record in records:
        f = record.get('fields', {})
        key = f.get('Idempotency Key')
        if not isinstance(key, str) or not key.startswith(anchor.campaign_id + ':'):
            raise PersistenceError('Unnamespaced or missing campaign event key')
        if key in seen_keys:
            raise PersistenceError('Duplicate ledger key')
        seen_keys.add(key)
        if f.get('Commit Status') != 'Verified':
            raise PersistenceError('Pending/conflicted/nonverified event blocks recovery')
        parsed.append((_stored_event(record), record['id']))
    parsed.sort(key=lambda t: t[0].expected_version)
    expected = genesis.version
    for event, _ in parsed:
        if event.expected_version != expected or event.committed_version != expected+1:
            raise PersistenceError('Event chain gap, fork or impossible state version')
        expected += 1

    try:
        restored = replay(genesis, tuple(event for event, _ in parsed))
    except (RulesError, KeyError, ValueError, TypeError) as exc:
        raise PersistenceError('Historical event chain failed pure replay') from exc
    checkpoint = decode_checkpoint(encounter.get('Combatant State Snapshot', ''))
    if (checkpoint.fingerprint() != restored.fingerprint() or
        restored.version != encounter.get('Current State Version') or
        restored.version != campaign.get('State Version') or
        restored.round_no != encounter.get('Round Number') or
        ((restored.version>0 or encounter.get('Status')=='Active') and
         (restored.active.actor_id if restored.status=='active' else '') !=
             (encounter.get('Active Combatant ID') or '')) or
        encounter.get('Status') not in (
            ('Resolved',) if restored.status=='complete' else
            ('Prepared','Active') if restored.version==0 else ('Active',)
        )):
        raise PersistenceError('Current campaign and encounter are not replay-equivalent')

    audits = [r for r in port.list('Dice Audit v2')
              if r.get('fields', {}).get('Campaign ID') == anchor.campaign_id]
    expected_roll_count = 0
    used_audits: set[str] = set()
    for event, event_record_id in parsed:
        for kind in ('attack','damage','save'):
            data = event.params.get(kind)
            if data is None:
                continue
            if not isinstance(data, dict):
                raise PersistenceError('Historical roll payload missing')
            try:
                roll = Roll(**{**data, **{n: tuple(data[n]) for n in
                                         ('all_faces', 'kept_faces', 'discarded_faces')}})
                validate_roll(roll)
            except (RulesError, KeyError, ValueError, TypeError) as exc:
                raise PersistenceError('Historical die payload altered') from exc
            if roll.entropy_source != SOURCE:
                raise PersistenceError('Unaudited/test-injected source is not admissible')
            matches = [r for r in audits if r.get('fields',{}).get('Roll ID') == roll.roll_id
                       and r.get('fields',{}).get('Event ID / Key') == event.event_key]
            if len(matches)!=1 or matches[0]['id'] in used_audits:
                raise PersistenceError('Missing/duplicate dice audit for event')
            row = matches[0]; used_audits.add(row['id']); f=row['fields']
            projection = roll_to_audit_fields(roll,campaign_id=anchor.campaign_id,
                                               event_key=event.event_key,
                                               ledger_record_id=event_record_id)
            for k,v in projection.items():
                if k == 'Status':continue
                if k == 'Source Event Ledger Link':
                    if f.get(k) != [event_record_id]:
                        raise PersistenceError('Dice audit points to another ledger event')
                elif k == 'Roll Timestamp':
                    # Airtable truncates input microseconds to milliseconds and
                    # normalizes +00:00 to Z; that is not dice tampering.
                    try:
                        norm=lambda x: datetime.fromisoformat(x.replace('Z','+00:00')).astimezone(timezone.utc)
                        drift=abs((norm(f.get(k))-norm(v)).total_seconds())
                        if drift >= 0.001:
                            raise PersistenceError('Airtable dice timestamp drift')
                    except (AttributeError, TypeError, ValueError) as exc:
                        raise PersistenceError('Invalid Airtable dice timestamp') from exc
                elif f.get(k) != v:
                    raise PersistenceError(f'Dice audit mismatch: {k}')
            if f.get('Status') != 'Reconciled with Event':
                raise PersistenceError('Die audit is not reconciled')
            if f.get('Arithmetic Check') not in (None, 'MATCH') or f.get('Dice Range Check') not in (None, 'VALID RANGE'):
                raise PersistenceError('Airtable dice formula validation failed')
            expected_roll_count+=1
    if len(audits)!=len(used_audits):
        raise PersistenceError('Unexpected or unlinked campaign dice audit')
    report = HistoryReport(anchor.campaign_id, genesis.encounter_id,
                           genesis.edition,len(parsed),expected_roll_count,
                           restored.version,restored.fingerprint(),
                           restored.round_no,restored.status)
    return restored,report
