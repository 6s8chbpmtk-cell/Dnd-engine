"""QA and adversarial recovery tests, never live campaign writes."""
from __future__ import annotations
from copy import deepcopy
import unittest

from airtable_persistence import PersistenceError, actor_projection
from recovery_bootstrap import recover_from_airtable
from test_history_v4_fixture import make_persisted_fixture


class ColdLoadTests(unittest.TestCase):
    def fixture(self):
        port, anchor, plan = make_persisted_fixture()
        port.store['Session Logs'] = {
            'genesis-1': {'id': 'genesis-1', 'fields': {
                'Session Log ID': plan['campaign_id'] + ':genesis',
                'Campaign ID': plan['campaign_id'],
                'State Version': 0,
                'Recap / What Happened': plan['initial_checkpoint'],
            }}
        }
        port.store['Combatant Turns v2'] = {}
        state = __import__('airtable_persistence').decode_checkpoint(plan['final_checkpoint'])
        for a in state.combatants:
            fields = actor_projection(state, a.actor_id)
            fields['Campaign ID'] = anchor.campaign_id
            fields['Combatant Integrity'] = 'VALID'
            fields['Encounter Record Link'] = [anchor.encounter_record_id]
            rid = 'actor-' + a.actor_id
            port.store['Combatant Turns v2'][rid] = {'id': rid, 'fields': fields}
        return port, anchor, plan

    def test_cold_load_full_history_and_actor_rows(self):
        port, anchor, plan = self.fixture()
        loaded = recover_from_airtable(port, anchor)
        self.assertEqual((loaded.history.state_version, loaded.history.checked_rolls,
                          loaded.history.checked_events), (12, 10, 12))
        self.assertEqual((loaded.battle.active.actor_id, loaded.battle.round_no), ('qa:raider', 4))
        self.assertEqual(set(loaded.actor_row_ids), {'qa:raider','qa:guardian'})
        self.assertEqual(loaded.battle.fingerprint(), plan['final_fingerprint'])

    def test_missing_genesis_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Session Logs'].clear()
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_duplicate_genesis_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Session Logs']['genesis-2'] = deepcopy(port.store['Session Logs']['genesis-1'])
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_malformed_genesis_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Session Logs']['genesis-1']['fields']['Recap / What Happened'] = 'nope'
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_stale_actor_row_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['actor-qa:guardian']['fields']['State Version'] = 11
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_divergent_actor_hp_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['actor-qa:guardian']['fields']['Current HP'] = 40
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_missing_actor_blocks_start(self):
        port, anchor, _ = self.fixture()
        del port.store['Combatant Turns v2']['actor-qa:guardian']
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_duplicate_actor_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['copy'] = deepcopy(port.store['Combatant Turns v2']['actor-qa:guardian'])
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_pending_event_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Event Ledger']['qa-event-04']['fields']['Commit Status']='Proposed'
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_foreign_campaign_actor_rows_ignored(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['foreign'] = {'id':'foreign', 'fields': {
            'Campaign ID':'another-game', 'Encounter ID':'qa-gm-battle',
            'Character or NPC ID':'hack','State Version':12}}
        self.assertEqual(recover_from_airtable(port,anchor).history.state_version,12)

    def test_invalid_combatant_formula_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['actor-qa:guardian']['fields']['Combatant Integrity']='INVALID'
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_crosslinked_actor_record_blocks_start(self):
        port, anchor, _ = self.fixture()
        port.store['Combatant Turns v2']['actor-qa:raider']['fields']['Encounter Record Link']=['other-encounter']
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

    def test_missing_actor_action_flag_blocks_start(self):
        port, anchor, _ = self.fixture()
        del port.store['Combatant Turns v2']['actor-qa:guardian']['fields']['Action Available']
        with self.assertRaises(PersistenceError):recover_from_airtable(port, anchor)

if __name__ == '__main__':unittest.main()
