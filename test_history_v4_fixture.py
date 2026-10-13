"""Reusable three-round GM-only synthetic trace with real source-attributed dice.

Reads a safe, static QA fixture. FakePort supplies a mocked Airtable transport.
No network, no real player campaign, and no new dice generated while replaying.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import copy, json, unittest
from engine_core import Roll
from history_verifier import verify_full_history
from airtable_persistence import BattleAnchor, PersistenceError, roll_to_audit_fields
from test_airtable_persistence import FakePort, RID, ERID

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'qa_3round_gm.json'


def make_persisted_fixture():
    plan=json.loads(FIXTURE.read_text())
    port=FakePort()
    cid=plan['campaign_id']
    port.store['Campaigns'][RID]['fields'].update({
        'Campaign ID':cid,
        'Rules Profile':'D&D 5e 2014 / SRD 5.1',
        'Game Mode':'D&D 5e',
        'State Version':len(plan['steps']),
    })
    final=json.loads(plan['final_checkpoint'])['battle']
    port.store['Encounters'][ERID]['fields'].update({
        'Campaign ID':cid,'Encounter ID':plan['encounter_id'],
        'Status':'Active','Round Number':final['round_no'],
        'Active Combatant ID':final['combatants'][final['active_index']]['actor_id'],
        'Combatant State Snapshot':plan['final_checkpoint'],
        'Current State Version':len(plan['steps']),
    })
    for step in plan['steps']:
        eventid=f'qa-event-{step["index"]:02d}'
        port.store['Event Ledger'][eventid]={'id':eventid,'fields':{
            'Campaign ID':cid,
            'Idempotency Key':step['event_key'],
            'Commit Status':'Verified',
            'State Changes Before → After':json.dumps(step['event'],sort_keys=True),
            'Mutation Fingerprint':step['event']['event_hash'],
            'State Version Before':step['before_version'],
            'State Version After':step['after_version'],
        }}
        for entry in step['rolls']:
            rdata=entry['roll']
            roll=Roll(**{**rdata,**{n:tuple(rdata[n]) for n in ('all_faces','kept_faces','discarded_faces')}})
            fields=roll_to_audit_fields(roll,campaign_id=cid,event_key=step['event_key'],ledger_record_id=eventid,target=entry['target'])
            fields['Status']='Reconciled with Event'
            auditid=f'qa-audit-{len(port.store["Dice Audit v2"])+1:02d}'
            port.store['Dice Audit v2'][auditid]={'id':auditid,'fields':fields}
    anchor=BattleAnchor(RID,ERID,cid,'2014')
    return port,anchor,plan


class V4AirtableFixtureTests(unittest.TestCase):
    def test_all_twelve_events_and_ten_dice_recover(self):
        port,anchor,plan=make_persisted_fixture()
        state,report=verify_full_history(port,anchor,plan['initial_checkpoint'])
        self.assertEqual((report.checked_events,report.checked_rolls,report.state_version),(12,10,12))
        self.assertEqual((state.round_no,state.status),(4,'active'))
        self.assertEqual(state.fingerprint(),plan['final_fingerprint'])
        self.assertEqual([(a.actor_id,a.hp) for a in state.combatants],
                         [('qa:raider',27),('qa:guardian',39)])

    def test_missing_middle_event_rejected(self):
        port,anchor,plan=make_persisted_fixture()
        del port.store['Event Ledger']['qa-event-07']
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_pending_acknowledgment_rejected(self):
        port,anchor,plan=make_persisted_fixture()
        port.store['Event Ledger']['qa-event-11']['fields']['Commit Status']='Proposed'
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_mislinked_roll_rejected(self):
        port,anchor,plan=make_persisted_fixture()
        port.store['Dice Audit v2']['qa-audit-01']['fields']['Source Event Ledger Link']=['another-event']
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_tampered_roll_rejected(self):
        port,anchor,plan=make_persisted_fixture()
        port.store['Dice Audit v2']['qa-audit-01']['fields']['Raw Face Results']='[1]'
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_timestamp_normalization_permitted(self):
        port,anchor,plan=make_persisted_fixture()
        f=port.store['Dice Audit v2']['qa-audit-01']['fields']
        # Airtable normalizes timestamps to millisecond UTC, losing microseconds.
        parsed=datetime.fromisoformat(f['Roll Timestamp'])
        f['Roll Timestamp']=parsed.isoformat(timespec='milliseconds').replace('+00:00','Z')
        verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_audit_orphan_detected(self):
        port,anchor,plan=make_persisted_fixture()
        fields=copy.deepcopy(port.store['Dice Audit v2']['qa-audit-01']['fields'])
        fields['Roll ID']='extra orphan'
        port.store['Dice Audit v2']['orphan']={'id':'orphan','fields':fields}
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

    def test_wrong_final_hp_even_with_edited_current_hash_rejected(self):
        port,anchor,plan=make_persisted_fixture()
        blob=json.loads(port.store['Encounters'][ERID]['fields']['Combatant State Snapshot'])
        blob['battle']['combatants'][0]['hp']-=1
        port.store['Encounters'][ERID]['fields']['Combatant State Snapshot']=json.dumps(blob)
        with self.assertRaises(PersistenceError):verify_full_history(port,anchor,plan['initial_checkpoint'])

if __name__=='__main__':unittest.main()
