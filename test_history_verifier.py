"""Recovery regression suite: synthetic adapter, no network writes."""
import copy
from dataclasses import replace
import unittest
from engine_core import roll, d20_result, critical_damage_expression
from airtable_persistence import PersistenceError, encode_checkpoint
from history_verifier import verify_full_history
from test_airtable_persistence import FakePort, pilot, initial, RID, ERID, CID


def completed_turns():
    p=FakePort(); genesis=encode_checkpoint(initial())
    pilot(p).commit(event_key=CID+':e1',expected_version=0,
                    operation='end_turn',params={'actor_id':'p1'},player_intent='QA: end turn')
    pilot(p).commit(event_key=CID+':e2',expected_version=1,
                    operation='end_turn',params={'actor_id':'b1'})
    return p,genesis


def history(p,genesis):
    return verify_full_history(p,pilot(p).anchor,genesis)


class HistoryRecoveryTests(unittest.TestCase):
    def test_exact_two_turn_replay(self):
        p,g=completed_turns()
        b,report=history(p,g)
        self.assertEqual((report.checked_events,report.state_version,report.checked_rolls),(2,2,0))
        self.assertEqual(b.round_no,2)
        self.assertEqual(b.active.actor_id,'p1')

    def test_empty_initial_checkpoint_recovery(self):
        p=FakePort();b,report=history(p,encode_checkpoint(initial()))
        self.assertEqual((b.version,report.checked_events),(0,0))

    def test_missing_event_detected_even_if_snapshot_latest(self):
        p,g=completed_turns();del p.store['Event Ledger']['event-1']
        with self.assertRaises(PersistenceError):history(p,g)

    def test_pending_event_blocks_recovery(self):
        p,g=completed_turns();p.store['Event Ledger']['event-2']['fields']['Commit Status']='Proposed'
        with self.assertRaises(PersistenceError):history(p,g)

    def test_duplicate_event_blocks_recovery(self):
        p,g=completed_turns();p.store['Event Ledger']['duplicate']=copy.deepcopy(p.store['Event Ledger']['event-1'])
        p.store['Event Ledger']['duplicate']['id']='duplicate'
        with self.assertRaises(PersistenceError):history(p,g)

    def test_corrupt_event_metadata_blocks_recovery(self):
        p,g=completed_turns();p.store['Event Ledger']['event-2']['fields']['Mutation Fingerprint']='incorrect'
        with self.assertRaises(PersistenceError):history(p,g)

    def test_campaign_version_drift_blocks_recovery(self):
        p,g=completed_turns();p.store['Campaigns'][RID]['fields']['State Version']=3
        with self.assertRaises(PersistenceError):history(p,g)

    def test_orphan_dice_audit_blocks_recovery(self):
        p,g=completed_turns();p.store['Dice Audit v2']['extra']={'id':'extra','fields':{'Campaign ID':CID,'Roll ID':'orphan'}}
        with self.assertRaises(PersistenceError):history(p,g)

    def test_genesis_wrong_edition_blocks_recovery(self):
        p,g=completed_turns();g=g.replace('2014','2024')
        with self.assertRaises(PersistenceError):history(p,g)

    def test_attack_dice_replayed_and_linked(self):
        p=FakePort();genesis_b=initial()
        genesis_b=replace(genesis_b,combatants=(replace(genesis_b.combatants[0],x=1),genesis_b.combatants[1]))
        genesis=encode_checkpoint(genesis_b)
        p.store['Encounters'][ERID]['fields']['Combatant State Snapshot']=genesis
        atk=roll('d20+5')
        details=d20_result(atk,12,'attack','2014')
        damage=roll(critical_damage_expression('1d8+3') if details['critical_hit'] else '1d8+3') if details['success'] else None
        pilot(p).commit(event_key=CID+':hit',expected_version=0,operation='attack',params={
            'actor_id':'p1','target_id':'b1','attack':atk,'damage':damage,
            'visibility_source':'QA known unobstructed','reaction_window':'confirmed'},player_intent='QA strike')
        b,r=history(p,genesis)
        self.assertEqual((r.checked_events,r.checked_rolls),(1,2 if damage else 1))
        self.assertEqual(b.version,1)
        row=next(iter(p.store['Dice Audit v2'].values()))
        row['fields']['Recorded Total']+=1
        with self.assertRaises(PersistenceError):history(p,genesis)

    def test_wrong_checkpoint_hash_detected(self):
        p,g=completed_turns()
        f=p.store['Encounters'][ERID]['fields']
        f['Combatant State Snapshot']=f['Combatant State Snapshot'].replace('heroes','raiders')
        with self.assertRaises(PersistenceError):history(p,g)

if __name__=='__main__':unittest.main()
