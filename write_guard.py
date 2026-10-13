"""Fail-closed tactical mutation gateway, v3.4 QA.

This gate verifies all persisted tactical history from an independently saved
version-zero genesis *before* permitting any new engine transition. The legacy
AirtableBattlePilot class alone intentionally does not do a full history replay.

IMPORTANT: This is an application-layer gate. A direct Airtable connector user
with write permission can bypass Python entirely; true prevention needs a
trusted writer service and restricted Airtable write credentials. Airtable
updates are not transactional. Never automatically retry uncertain writes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

from airtable_persistence import (
    AirtablePort, AirtableBattlePilot, BattleAnchor, CommitResult,
    PersistenceError, WriteUncertain,
)
from combat_runtime import Battle
from history_verifier import HistoryReport
from recovery_bootstrap import recover_from_airtable


@dataclass(frozen=True)
class GatePreflight:
    campaign_id: str
    encounter_id: str
    version: int
    round_no: int
    active_actor: str | None
    fingerprint: str
    checked_events: int
    checked_rolls: int
    genesis_record_id: str


class VerifiedTurnGateway:
    """Safe entry point for any *supported* tactical pilot write.

    Every operation must be one engine transition ('move', 'attack', 'end_turn',
    'disengage', 'spend', 'concentration'). A mixed move-and-attack user intent
    needs *two commits with different event keys*, preserving every sub-event.
    
    Readiness checks run both before and after a mutation; post-write failure
    is ambiguous, and must halt until a person independently reconciles it.
    This is not concurrency-safe for multiple writers.
    """
    def __init__(self, port: AirtablePort, anchor: BattleAnchor):
        self.port=port
        self.anchor=anchor

    def preflight(self) -> GatePreflight:
        loaded = recover_from_airtable(self.port, self.anchor)
        state=loaded.battle
        report=loaded.history
        return GatePreflight(
            campaign_id=self.anchor.campaign_id, encounter_id=state.encounter_id,
            version=state.version, round_no=state.round_no,
            active_actor=state.active.actor_id if state.status=='active' else None,
            fingerprint=state.fingerprint(),checked_events=report.checked_events,
            checked_rolls=report.checked_rolls,genesis_record_id=loaded.genesis_record_id,
        )

    def commit(self, *, event_key: str, expected_version: int, operation: str,
               params: dict, player_intent: str='') -> CommitResult:
        # This check has NO writes. All malformed/legacy/compacted histories STOP.
        recovered=recover_from_airtable(self.port,self.anchor)
        state=recovered.battle
        if state.status!='active':
            raise PersistenceError('GATE_STOP: encounter not active')
        if expected_version!=state.version:
            raise PersistenceError('GATE_STOP: stale requested version; reload entire history')
        if not event_key.startswith(self.anchor.campaign_id+':'):
            raise PersistenceError('GATE_STOP: event key not campaign-namespaced')
        # Verify all actor rows before delegating to the only versioned writer.
        pilot=AirtableBattlePilot(self.port,self.anchor,actor_rows=recovered.actor_row_ids)
        result=pilot.commit(event_key=event_key,expected_version=expected_version,
                            operation=operation,params=params,player_intent=player_intent)
        if result.result == 'REPLAY_NO_WRITE':
            return result
        try:
            subsequent=recover_from_airtable(self.port,self.anchor)
            after=subsequent.battle
            if (after.version!=expected_version+1 or
                subsequent.history.checked_events!=recovered.history.checked_events+1 or
                after.fingerprint()==state.fingerprint()):
                raise PersistenceError('GATE_STOP: post-commit replay count/version mismatch')
        except Exception as exc:
            # The engine already attempted a write. Never retry automatically.
            raise WriteUncertain('GATE_STOP: post-write history not verified; inspect ledger, do not reroll/retry') from exc
        return result
