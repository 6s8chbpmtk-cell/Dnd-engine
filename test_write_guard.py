"""v3.4 fail-closed gate regressions. FakePort ONLY; no live campaign writes."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
import unittest

from airtable_persistence import (
    PersistenceError, WriteUncertain, actor_projection, encode_checkpoint,
    BattleAnchor,
)
from test_airtable_persistence import FakePort, initial, CID, RID, ERID
from engine_core import roll, d20_result, critical_damage_expression
import test_recovery_bootstrap
from write_guard import VerifiedTurnGateway


class WriteGateTests(unittest.TestCase):
    @staticmethod
    def multiround():
        port,anchor,_=test_recovery_bootstrap.ColdLoadTests().fixture()
        return port,VerifiedTurnGateway(port,anchor),anchor

    @staticmethod
    def coldstart():
        port=FakePort()
        port.store['Encounters'][ERID]['fields']['Status']='Active'
        port.store['Encounters'][ERID]['fields']['Active Combatant ID']='p1'
        port.store['Session Logs']={
            'genesis-0':{'id':'genesis-0','fields':{
                'Session Log ID':CID+':session01:genesis',
                'Campaign ID':CID,'State Version':0,
                'Verification Status':'Verified','Log Type':'Checkpoint',
                'State Reconciliation Notes':encode_checkpoint(initial()),
                'Recap / What Happened':'Player arrived at bridge.'
            }}
        }
        state=initial()
        for actor in state.combatants:
            fields=actor_projection(state,actor.actor_id)
            fields.update({'Campaign ID':CID,'Encounter ID':state.encounter_id,
                           'Combatant Integrity':'VALID',
                           'Encounter Record Link':[ERID]})
            port.store['Combatant Turns v2']['row-'+actor.actor_id]={
                'id':'row-'+actor.actor_id,'fields':fields}
        return port,VerifiedTurnGateway(port,BattleAnchor(RID,ERID,CID,'2014'))

    def test_multiround_valid_history_preflight_is_read_only(self):
        port,gate,_=self.multiround()
        state=gate.preflight()
        self.assertEqual((state.version,state.checked_events,state.checked_rolls),(12,12,10))
        self.assertEqual(port.write_count,0)

    def test_new_coldstart_session_scoped_genesis_supported(self):
        port,gate=self.coldstart()
        state=gate.preflight()
        self.assertEqual((state.version,state.checked_events,state.round_no),(0,0,1))
        self.assertEqual(state.genesis_record_id,'genesis-0')
        self.assertEqual(port.write_count,0)

    def test_guarded_commit_produces_one_replayable_event(self):
        port,gate=self.coldstart()
        result=gate.commit(event_key=CID+':move-1',expected_version=0,operation='move',
            params={'actor_id':'p1','path':[[0,1],[1,1]]},
            player_intent='Move to (1,1)')
        self.assertEqual(result.result,'VERIFIED')
        state=gate.preflight()
        self.assertEqual((state.version,state.checked_events,state.checked_rolls),(1,1,0))
        self.assertEqual(port.store['Event Ledger']['event-1']['fields']['Commit Status'],'Verified')

    def test_six_transitions_produce_six_replayable_events(self):
        """A move, strike, end, Disengage, retreat, end turn is SIX events."""
        port,gate=self.coldstart()
        b=initial()
        foe=replace(b.combatants[1],hp=40,max_hp=40)
        b=replace(b,combatants=(b.combatants[0],foe))
        port.store['Encounters'][ERID]['fields']['Combatant State Snapshot']=encode_checkpoint(b)
        port.store['Session Logs']['genesis-0']['fields']['State Reconciliation Notes']=encode_checkpoint(b)
        for actor in b.combatants:
            row=port.store['Combatant Turns v2']['row-'+actor.actor_id]['fields']
            row.update(actor_projection(b,actor.actor_id))
        # Single real OS-backed attack roll; do not select/retry a convenient face.
        attack=roll('d20+5')
        resolved=d20_result(attack,12,'attack','2014')
        damage=(roll(critical_damage_expression('1d8+3')
                     if resolved['critical_hit'] else '1d8+3')
                if resolved['success'] else None)
        operations=[
           ('move', {'actor_id':'p1','path':[[1,0]]}, 'I move adjacent'),
           ('attack', {'actor_id':'p1','target_id':'b1','attack':attack,'damage':damage,
                       'visibility_source':'QA unobstructed','reaction_window':'confirmed'},
                       'I strike with my longsword'),
           ('end_turn',{'actor_id':'p1'},'I end my turn'),
           ('disengage',{'actor_id':'b1'},''),
           ('move',{'actor_id':'b1','path':[[3,0]]},''),
           ('end_turn',{'actor_id':'b1'},''),
        ]
        for version,(op,params,intent) in enumerate(operations):
            result=gate.commit(event_key=CID+':complete-turn:'+str(version),
                               expected_version=version,operation=op,
                               params=params,player_intent=intent)
            self.assertEqual(result.version,version+1)
        report=gate.preflight()
        self.assertEqual((report.version,report.checked_events,report.round_no),(6,6,2))
        self.assertEqual(report.checked_rolls,2 if resolved['success'] else 1)
        self.assertEqual(report.active_actor,'p1')
        self.assertEqual(len(port.store['Event Ledger']),6)

    def test_compound_prose_ledger_rejected_even_with_matching_checkpoint(self):
        port,gate,_=self.multiround()
        # This is how the Blackglass chat saved its "verified" v1 event:
        # human-readable state delta with no operation/params/before/after hashes.
        first=next(iter(port.store['Event Ledger'].values()))
        first['fields']['State Changes Before → After']=(
            'Rook moved 20 feet and struck Garrick, taking him from 11 to 4 HP')
        before=port.write_count
        with self.assertRaises(PersistenceError) as exc:
            gate.commit(event_key=gate.anchor.campaign_id+':new',expected_version=12,
                        operation='end_turn',params={'actor_id':'qa:raider'})
        self.assertIn('Malformed historical event',str(exc.exception))
        self.assertEqual(port.write_count,before)

    def test_json_event_missing_structured_operation_rejected(self):
        port,gate,_=self.multiround()
        first=next(iter(port.store['Event Ledger'].values()))
        first['fields']['State Changes Before → After']='{"operation":"move and attack","description":"combined"}'
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_missing_event_with_advanced_checkpoint_rejected(self):
        port,gate,_=self.multiround()
        port.store['Event Ledger'].pop(next(iter(port.store['Event Ledger'])))
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_duplicate_event_rejected(self):
        port,gate,_=self.multiround()
        first=next(iter(port.store['Event Ledger'].values()))
        copy=deepcopy(first);copy['id']='copy-event'
        port.store['Event Ledger']['copy-event']=copy
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_pending_event_rejected(self):
        port,gate,_=self.multiround()
        first=next(iter(port.store['Event Ledger'].values()))
        first['fields']['Commit Status']='Proposed'
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_orphaned_dice_audit_rejected(self):
        port,gate,_=self.multiround()
        port.store['Dice Audit v2']['orphan']={'id':'orphan','fields':{
            'Campaign ID':gate.anchor.campaign_id,'Roll ID':'orphan'}}
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_stale_expected_version_rejected_without_writes(self):
        port,gate,_=self.multiround()
        with self.assertRaises(PersistenceError):gate.commit(
            event_key=gate.anchor.campaign_id+':old',expected_version=10,
            operation='end_turn',params={'actor_id':'qa:raider'})
        self.assertEqual(port.write_count,0)

    def test_missing_genesis_rejected_without_writes(self):
        port,gate=self.coldstart()
        port.store['Session Logs'].clear()
        with self.assertRaises(PersistenceError):gate.preflight()
        self.assertEqual(port.write_count,0)

    def test_genesis_must_be_verified(self):
        port,gate=self.coldstart()
        port.store['Session Logs']['genesis-0']['fields']['Verification Status']='Draft'
        with self.assertRaises(PersistenceError):gate.preflight()

    def test_genesis_mismatching_serialized_fields_rejected(self):
        port,gate=self.coldstart()
        port.store['Session Logs']['genesis-0']['fields']['Recap / What Happened']='{"schema":"fake"}'
        with self.assertRaises(PersistenceError):gate.preflight()

    def test_second_write_requires_fresh_full_replay(self):
        port,gate=self.coldstart()
        gate.commit(event_key=CID+':end1',expected_version=0,operation='end_turn',
                    params={'actor_id':'p1'},player_intent='End turn')
        self.assertEqual(gate.preflight().checked_events,1)
        # A direct connector mutation inserts a bad event at the next version.
        port.store['Event Ledger']['legacy']={'id':'legacy','fields':{
            'Campaign ID':CID,'Idempotency Key':CID+':legacy',
            'Commit Status':'Verified','State Version Before':1,'State Version After':2,
            'State Changes Before → After':'A bandit moved and ended its turn.'}}
        count=port.write_count
        with self.assertRaises(PersistenceError):gate.commit(
            event_key=CID+':end2',expected_version=1,
            operation='end_turn',params={'actor_id':'b1'})
        self.assertEqual(port.write_count,count)

    def test_postwrite_validation_failure_is_uncertain_not_automatic_retry(self):
        port,gate=self.coldstart()
        old=port.update
        def altered_update(table,record_id,fields):
            out=old(table,record_id,fields)
            if table=='Event Ledger' and fields.get('Commit Status')=='Verified':
                port.store['Combatant Turns v2']['row-p1']['fields']['Current HP']=100
            return out
        port.update=altered_update
        with self.assertRaises(WriteUncertain):gate.commit(
            event_key=CID+':end1',expected_version=0,operation='end_turn',
            params={'actor_id':'p1'},player_intent='End turn')
        self.assertEqual(len(port.store['Event Ledger']),1)
        self.assertGreater(port.write_count,0)

    def test_user_agency_gate_blocks_implicit_player_action(self):
        port,gate=self.coldstart()
        with self.assertRaises(PersistenceError):gate.commit(
            event_key=CID+':without-intent',expected_version=0,
            operation='end_turn',params={'actor_id':'p1'})
        self.assertEqual(port.write_count,0)


if __name__=='__main__':unittest.main()
