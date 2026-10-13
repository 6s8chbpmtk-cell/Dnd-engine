"""Mocked Airtable transport test: no live campaign writes, no credentials."""
from __future__ import annotations

from dataclasses import replace
import copy
import unittest

from engine_core import RulesError, roll
from combat_runtime import Fighter, Battle
from airtable_persistence import (AirtableBattlePilot, BattleAnchor, CommitResult,
    PersistenceError, WriteUncertain, encode_checkpoint, decode_checkpoint,
    _require_live_rolls, roll_to_audit_fields, actor_projection)


CID='qa:combat:pilot'
EID='qa-fight'
RID='campaign-record'
ERID='encounter-record'


def initial():
    player=Fighter('p1','heroes','player',12,12,0,16,5,'1d8+3',18,2,30,0,0)
    bandit=Fighter('b1','foes','gm',11,11,0,12,3,'1d6+1',14,1,30,2,0)
    b=Battle(EID,'2014',1,0,0,(player,bandit),tuple((x,y) for x in range(7) for y in range(3)))
    b.validate()
    return b


class FakePort:
    def __init__(self):
        self.store={
            'Campaigns':{RID:{'id':RID,'fields':{
                'Campaign ID':CID,'Game Mode':'D&D 5e','Rules Profile':'D&D 5e 2014 / SRD 5.1',
                'State Version':0}}},
            'Encounters':{ERID:{'id':ERID,'fields':{
                'Campaign ID':CID,'Encounter ID':EID,'Status':'Prepared',
                'Current State Version':0,'Round Number':1,
                'Combatant State Snapshot':encode_checkpoint(initial())}}},
            'Event Ledger':{},
            'Dice Audit v2':{},
            'Combatant Turns v2':{}
        }
        self.calls=[]
        self.write_count=0
        self.fail_after_write=None
        self.fail_before_write=None
        self.rejections={}
    def get(self,table,record_id):
        if (table,record_id) in self.rejections:
            raise ConnectionError('read rejected')
        return copy.deepcopy(self.store[table][record_id])
    def list(self,table):
        return copy.deepcopy(list(self.store[table].values()))
    def _fault(self,stage):
        self.write_count+=1
        if self.fail_before_write==self.write_count and stage=='before':
            raise TimeoutError('request may have reached remote')
        if self.fail_after_write==self.write_count and stage=='after':
            raise TimeoutError('acknowledgment lost after accepted write')
    def create(self,table,fields):
        self.calls.append(('create',table))
        self._fault('before')
        rid=f'event-{len(self.store[table])+1}'
        self.store[table][rid]={'id':rid,'fields':copy.deepcopy(fields)}
        self._fault_after()
        return copy.deepcopy(self.store[table][rid])
    def update(self,table,rid,fields):
        self.calls.append(('update',table))
        self._fault('before')
        self.store[table][rid]['fields'].update(copy.deepcopy(fields))
        self._fault_after()
        return copy.deepcopy(self.store[table][rid])
    def _fault_after(self):
        if self.fail_after_write==self.write_count:
            raise TimeoutError('acknowledgment lost after accepted write')


def pilot(port):
    return AirtableBattlePilot(port,BattleAnchor(RID,ERID,CID,'2014'))


def pilot_with_actor_rows(port):
    b=initial()
    for a in b.combatants:
        port.store['Combatant Turns v2']['row-'+a.actor_id]={
            'id':'row-'+a.actor_id,'fields':actor_projection(b,a.actor_id)}
    return AirtableBattlePilot(port,BattleAnchor(RID,ERID,CID,'2014'),
                              actor_rows={'p1':'row-p1','b1':'row-b1'})


class CheckpointTests(unittest.TestCase):
    def test_roundtrip(self):
        b=initial()
        self.assertEqual(decode_checkpoint(encode_checkpoint(b)).fingerprint(),b.fingerprint())
    def test_damaged_hash(self):
        encoded=encode_checkpoint(initial()).replace('heroes','villains')
        with self.assertRaises(PersistenceError): decode_checkpoint(encoded)
    def test_unknown_schema(self):
        with self.assertRaises(PersistenceError):decode_checkpoint('{}')
    def test_bad_json(self):
        with self.assertRaises(PersistenceError):decode_checkpoint('{invalid')
    def test_unclear_edition(self):
        with self.assertRaises(PersistenceError):AirtableBattlePilot(FakePort(),BattleAnchor(RID,ERID,CID,'2000'))


class ReadTests(unittest.TestCase):
    def test_read(self):
        self.assertEqual(pilot(FakePort()).read(),initial())
    def test_missing_checkpoint(self):
        p=FakePort();p.store['Encounters'][ERID]['fields'].pop('Combatant State Snapshot')
        with self.assertRaises(PersistenceError):pilot(p).read()
    def test_wrong_campaign(self):
        p=FakePort();p.store['Encounters'][ERID]['fields']['Campaign ID']='another'
        with self.assertRaises(PersistenceError):pilot(p).read()
    def test_wrong_edition(self):
        p=FakePort();p.store['Campaigns'][RID]['fields']['Rules Profile']='D&D 5e 2024 / SRD 5.2.1'
        with self.assertRaises(PersistenceError):pilot(p).read()
    def test_split_versions(self):
        p=FakePort();p.store['Campaigns'][RID]['fields']['State Version']=1
        with self.assertRaises(PersistenceError):pilot(p).read()
    def test_no_writes_on_bad_read(self):
        p=FakePort();p.store['Campaigns'][RID]['fields']['State Version']=20
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':m1',expected_version=0,
                                                                operation='end_turn',params={'actor_id':'p1'},player_intent='End turn')
        self.assertFalse(p.calls)


class CommitTests(unittest.TestCase):
    def test_actor_projection_roundtrip(self):
        p=FakePort();b=pilot_with_actor_rows(p)
        self.assertEqual(b.read().version,0)
        result=b.commit(event_key=CID+':projection',expected_version=0,operation='end_turn',
                        params={'actor_id':'p1'},player_intent='QA end')
        self.assertEqual(result.result,'VERIFIED')
        self.assertEqual(p.store['Combatant Turns v2']['row-p1']['fields']['State Version'],1)
        self.assertEqual(p.store['Combatant Turns v2']['row-b1']['fields']['Turn Status'],'Active')
        self.assertEqual(b.read().version,1)
    def test_actor_projection_missing_mapping_rejected(self):
        p=FakePort();b=AirtableBattlePilot(p,BattleAnchor(RID,ERID,CID,'2014'),actor_rows={'p1':'row-p1'})
        with self.assertRaises(PersistenceError):b.read()
    def test_airtable_omits_empty_text_but_projection_remains_valid(self):
        p=FakePort();b=pilot_with_actor_rows(p)
        p.store['Combatant Turns v2']['row-p1']['fields'].pop('Concentration / Conditions')
        self.assertEqual(b.read().version,0)
    def test_actor_projection_drift_rejected(self):
        p=FakePort();b=pilot_with_actor_rows(p)
        p.store['Combatant Turns v2']['row-p1']['fields']['Current HP']=100
        with self.assertRaises(PersistenceError):b.read()
    def test_actor_projection_partial_timeout_halts(self):
        p=FakePort();b=pilot_with_actor_rows(p)
        p.fail_after_write=4 # ledger, encounter, campaign, first actor
        with self.assertRaises(WriteUncertain):b.commit(event_key=CID+':actorfail',
                  expected_version=0,operation='end_turn',params={'actor_id':'p1'},player_intent='QA end')
        self.assertEqual(b.inspect(CID+':actorfail')['status'],'PARTIAL_OR_UNCERTAIN_STOP')
    def test_commit_verifies_remote_readback(self):
        p=FakePort()
        res=pilot(p).commit(event_key=CID+':end',expected_version=0,operation='end_turn',
                            params={'actor_id':'p1'},player_intent='I end my turn')
        self.assertEqual(res.result,'VERIFIED')
        self.assertEqual(res.version,1)
        self.assertEqual(p.store['Campaigns'][RID]['fields']['State Version'],1)
        self.assertEqual(p.store['Encounters'][ERID]['fields']['Current State Version'],1)
        self.assertEqual(p.store['Event Ledger']['event-1']['fields']['Commit Status'],'Verified')
        self.assertEqual(decode_checkpoint(p.store['Encounters'][ERID]['fields']['Combatant State Snapshot']).active.actor_id,'b1')
    def test_retry_different_action_same_key_is_rejected(self):
        p=FakePort();b=pilot(p)
        b.commit(event_key=CID+':same',expected_version=0,operation='end_turn',
                 params={'actor_id':'p1'},player_intent='End')
        with self.assertRaises(PersistenceError):b.commit(event_key=CID+':same',expected_version=0,
                 operation='spend',params={'actor_id':'p1','resource':'action','label':'GM-recorded empty action'},player_intent='Dodge')

    def test_retry_same_key_wrong_version_is_rejected(self):
        p=FakePort();b=pilot(p)
        b.commit(event_key=CID+':same',expected_version=0,operation='end_turn',
                 params={'actor_id':'p1'},player_intent='End')
        with self.assertRaises(PersistenceError):b.commit(event_key=CID+':same',expected_version=1,
                 operation='end_turn',params={'actor_id':'p1'},player_intent='End')

    def test_read_blocks_pending_event_even_when_versions_synced(self):
        p=FakePort()
        p.store['Event Ledger']['pending']={'id':'pending','fields':{
            'Campaign ID':CID,'Idempotency Key':CID+':pending','Commit Status':'Proposed'}}
        with self.assertRaises(PersistenceError):pilot(p).read()
        self.assertEqual(pilot(p).read(allow_pending=True).version,0)

    def test_unresolved_other_event_blocks_new_mutation(self):
        p=FakePort();p.store['Event Ledger']['pending']={'id':'pending','fields':{
            'Campaign ID':CID,'Idempotency Key':CID+':pending','Commit Status':'Proposed'}}
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':new',expected_version=0,
                 operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        self.assertEqual(p.write_count,0)

    def test_tampered_existing_event_rejected(self):
        p=FakePort();b=pilot(p)
        kw=dict(event_key=CID+':same',expected_version=0,operation='end_turn',
             params={'actor_id':'p1'},player_intent='End')
        b.commit(**kw)
        p.store['Event Ledger']['event-1']['fields']['Mutation Fingerprint']='not-original'
        with self.assertRaises(PersistenceError):b.commit(**kw)

    def test_exact_retry_only_one_write(self):
        p=FakePort();b=pilot(p)
        kw=dict(event_key=CID+':end',expected_version=0,operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        b.commit(**kw);writes=p.write_count
        second=b.commit(**kw)
        self.assertEqual(second.result,'REPLAY_NO_WRITE')
        self.assertEqual(p.write_count,writes)
    def test_player_agency_guard(self):
        p=FakePort()
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':end',expected_version=0,
                                              operation='end_turn',params={'actor_id':'p1'})
        self.assertEqual(p.write_count,0)
    def test_wrong_version_no_write(self):
        p=FakePort()
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':end',expected_version=100,
                                              operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        self.assertEqual(p.write_count,0)
    def test_bad_key_no_write(self):
        p=FakePort()
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key='not namespaced',expected_version=0,
                                              operation='end_turn',params={'actor_id':'p1'},player_intent='End')
    def test_injected_dice_rejected_for_live_commit(self):
        p=FakePort()
        injected=roll('d20+5',die=lambda _:20)
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':badroll',expected_version=0,
           operation='attack',params={'actor_id':'p1','target_id':'b1','attack':injected,'damage':None},player_intent='Strike bandit')
        self.assertFalse(p.calls)
    def test_live_source_roll_accepted_for_preflight(self):
        _require_live_rolls({'attack':roll('d20+5')})
    def test_live_dice_audit_projection(self):
        r=roll('d20+2',mode='advantage')
        f=roll_to_audit_fields(r,campaign_id=CID,event_key=CID+':attack',ledger_record_id='recQa')
        self.assertEqual((f['Roll Mode'],f['Number of Dice'],f['Source'],f['Raw Dice Sum']),
                         ('Advantage',1,'OS CSPRNG (tool executed)',r.raw_sum))
        self.assertEqual(f['Recorded Total'],r.total)
    def test_test_injected_dice_projection_rejected(self):
        with self.assertRaises(PersistenceError):roll_to_audit_fields(
          roll('d20',die=lambda _:17),campaign_id=CID,event_key=CID+':attack',ledger_record_id='recQa')
    def test_persist_attack_with_real_os_dice_and_audit(self):
        p=FakePort();b=pilot(p)
        p.store['Encounters'][ERID]['fields']['Combatant State Snapshot']=encode_checkpoint(
            replace(initial(),combatants=(replace(initial().combatants[0],x=1),initial().combatants[1])))
        atk=roll('d20+5')
        from engine_core import d20_result
        hit=d20_result(atk,12,'attack','2014')['success']
        dmg=roll('2d8+3' if d20_result(atk,12,'attack','2014')['critical_hit'] else '1d8+3') if hit else None
        outcome=b.commit(event_key=CID+':attack1',expected_version=0,operation='attack',
         params={'actor_id':'p1','target_id':'b1','attack':atk,'damage':dmg,
         'visibility_source':'QA unobstructed','reaction_window':'confirmed'},player_intent='QA sword attack')
        self.assertEqual(outcome.result,'VERIFIED')
        rows=list(p.store['Dice Audit v2'].values())
        self.assertEqual(len(rows),2 if hit else 1)
        self.assertTrue(all(row['fields']['Status']=='Reconciled with Event' for row in rows))
        self.assertEqual(pilot(p).read().version,1)
    def test_edition_mismatch_halts(self):
        p=FakePort();p.store['Campaigns'][RID]['fields']['Rules Profile']='Custom RPG'
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':m',expected_version=0,
                    operation='end_turn',params={'actor_id':'p1'},player_intent='End')
    def test_no_double_spend(self):
        p=FakePort();b=pilot(p)
        b.commit(event_key=CID+':action1',expected_version=0,operation='spend',
                 params={'actor_id':'p1','resource':'action','label':'GM-recorded empty action'},player_intent='Dodge')
        with self.assertRaises(RulesError):b.commit(event_key=CID+':action2',expected_version=1,
                       operation='spend',params={'actor_id':'p1','resource':'action','label':'GM-recorded empty action'},player_intent='Dodge again')
        self.assertEqual(len(p.store['Event Ledger']),1)
    def test_persist_roundtrip_then_resume_new_object(self):
        p=FakePort();pilot(p).commit(event_key=CID+':end1',expected_version=0,
                                     operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        second=AirtableBattlePilot(p,BattleAnchor(RID,ERID,CID,'2014'))
        self.assertEqual(second.read().active.actor_id,'b1')
        r=second.commit(event_key=CID+':end2',expected_version=1,
                        operation='end_turn',params={'actor_id':'b1'})
        self.assertEqual(r.version,2)
        self.assertEqual(second.read().active.actor_id,'p1')
    def test_unresolved_proposal_halts_same_key(self):
        p=FakePort();p.fail_after_write=1
        b=pilot(p)
        with self.assertRaises(WriteUncertain):b.commit(event_key=CID+':m1',expected_version=0,
                                    operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        p.fail_after_write=None
        self.assertEqual(b.inspect(CID+':m1')['status'],'PARTIAL_OR_UNCERTAIN_STOP')
        with self.assertRaises(PersistenceError):b.commit(event_key=CID+':m1',expected_version=0,
                                    operation='end_turn',params={'actor_id':'p1'},player_intent='End')
    def test_ambiguous_after_encounter_write(self):
        p=FakePort();p.fail_after_write=2
        with self.assertRaises(WriteUncertain):pilot(p).commit(event_key=CID+':m2',expected_version=0,
                          operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        diag=pilot(p).inspect(CID+':m2')
        self.assertEqual(diag['status'],'PARTIAL_OR_UNCERTAIN_STOP')
        self.assertEqual((diag['campaign_version'],diag['encounter_version']),(0,1))
    def test_ambiguous_after_campaign_write(self):
        p=FakePort();p.fail_after_write=3
        with self.assertRaises(WriteUncertain):pilot(p).commit(event_key=CID+':m3',expected_version=0,
                          operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        diag=pilot(p).inspect(CID+':m3')
        self.assertEqual(diag['status'],'PARTIAL_OR_UNCERTAIN_STOP')
        self.assertEqual((diag['campaign_version'],diag['encounter_version']),(1,1))
    def test_ambiguous_final_ack(self):
        p=FakePort();p.fail_after_write=4
        with self.assertRaises(WriteUncertain):pilot(p).commit(event_key=CID+':m4',expected_version=0,
                          operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        diag=pilot(p).inspect(CID+':m4')
        self.assertEqual(diag['status'],'RECORDED_VERIFY_FINGERPRINT_MANUALLY')
        # Return path must not have automatically caused another write.
        self.assertEqual(p.write_count,4)
    def test_timeout_before_first_write_is_uncertain(self):
        p=FakePort();p.fail_before_write=1
        with self.assertRaises(WriteUncertain):pilot(p).commit(event_key=CID+':m5',expected_version=0,
                          operation='end_turn',params={'actor_id':'p1'},player_intent='End')
        self.assertEqual(len(p.store['Event Ledger']),0)
    def test_duplicate_remote_keys_fail(self):
        p=FakePort()
        p.store['Event Ledger']['x']={'id':'x','fields':{'Campaign ID':CID,'Idempotency Key':CID+':dupe','Commit Status':'Verified'}}
        p.store['Event Ledger']['y']={'id':'y','fields':{'Campaign ID':CID,'Idempotency Key':CID+':dupe','Commit Status':'Verified'}}
        with self.assertRaises(PersistenceError):pilot(p).commit(event_key=CID+':dupe',expected_version=0,
                          operation='end_turn',params={'actor_id':'p1'},player_intent='End')


if __name__=='__main__':unittest.main()
