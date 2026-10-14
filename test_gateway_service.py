"""All gateway API tests use FakePort only. Never write actual Airtable state."""
from __future__ import annotations
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from airtable_persistence import actor_projection, encode_checkpoint, BattleAnchor
from gateway_service import GatewayAPI
import test_write_guard
from write_guard import VerifiedTurnGateway
from engine_core import SOURCE

CID = 'pilot-test'
KEY = 'A_qa_test_key_long_enough_but_not_an_actual_token_123456789'


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        self.port, self.guard = test_write_guard.WriteGateTests.coldstart()
        # FakePort uses synthetic IDs; the HTTP API correctly requires Airtable rec IDs.
        old = self.guard.anchor
        campaign_rec, encounter_rec = "recQACampaign0001", "recQAEncounter0001"
        self.port.store["Campaigns"][campaign_rec] = self.port.store["Campaigns"].pop(old.campaign_record_id)
        self.port.store["Encounters"][encounter_rec] = self.port.store["Encounters"].pop(old.encounter_record_id)
        for row in self.port.store["Combatant Turns v2"].values():
            row["fields"]["Encounter Record Link"] = [encounter_rec]
        self.guard = VerifiedTurnGateway(self.port, BattleAnchor(campaign_rec, encounter_rec,
                                                                 old.campaign_id, old.expected_rules))
        self.api = GatewayAPI(api_key=KEY, port_factory=lambda: self.port,
                              public_url='https://sample.example')
        self.base = {'campaign_id': self.guard.anchor.campaign_id,
                     'campaign_record_id': self.guard.anchor.campaign_record_id,
                     'encounter_record_id': self.guard.anchor.encounter_record_id,
                     'edition': '2014'}

    def call(self, endpoint: str, request: dict, key: str = KEY):
        return self.api.dispatch('POST', endpoint, 'Bearer '+key,
                                 json.dumps(request).encode())

    def turn(self, operation='move', **override):
        req = {**self.base,
               'operation': operation, 'actor_id': 'p1',
               'expected_version': 0, 'event_key': self.base['campaign_id']+':test-1',
               'player_intent': 'I take this action'}
        if operation == 'move':
            req['path'] = [[1,0]]
        if operation == 'melee_attack':
            req.update(target_id='b1', reaction_window_confirmed=True)
        req.update(override)
        return req

    def test_health_no_network_or_game_info(self):
        status,data=self.api.dispatch('GET','/health','')
        self.assertEqual(status,200)
        self.assertEqual(data['status'],'running')
        self.assertNotIn('campaign_id',data)

    def test_openapi_has_two_restricted_operations(self):
        status,data=self.api.dispatch('GET','/openapi.json','')
        self.assertEqual(status,200)
        self.assertEqual(data['servers'][0]['url'],'https://sample.example')
        self.assertEqual(set(data['paths']),{'/v1/state','/v1/turn'})
        self.assertFalse(any('dice' in k for k in data['paths']['/v1/turn']['post']['requestBody']['content']['application/json']['schema']['properties']))

    def test_refuses_bad_auth_before_touching_port(self):
        status,body=self.call('/v1/turn',self.turn(),key='not-the-key')
        self.assertEqual(status,401)
        self.assertEqual(self.port.write_count,0)

    def test_initial_recovery_and_no_writes(self):
        status,r=self.call('/v1/state',self.base)
        self.assertEqual(status,200)
        self.assertEqual((r['version'],r['checked_events'],r['active_actor']),(0,0,'p1'))
        self.assertEqual(self.port.write_count,0)

    def test_single_move_one_structured_event(self):
        status,r=self.call('/v1/turn',self.turn())
        self.assertEqual(status,200)
        self.assertEqual(r['state']['checked_events'],1)
        self.assertEqual(r['state']['version'],1)
        self.assertEqual(len(self.port.store['Event Ledger']),1)
        ev=next(iter(self.port.store['Event Ledger'].values()))['fields']
        self.assertEqual(json.loads(ev['State Changes Before → After'])['operation'],'move')

    def test_unknown_input_rejects_fake_dice(self):
        status,r=self.call('/v1/turn',self.turn(attack={'all_faces':[20]}))
        self.assertEqual(status,422)
        self.assertEqual(self.port.write_count,0)

    def test_compound_move_and_attack_cannot_enter_one_request(self):
        status,r=self.call('/v1/turn',self.turn(target_id='b1'))
        self.assertEqual(status,422)
        self.assertEqual(self.port.write_count,0)

    def test_expected_version_drift_rejected_without_write(self):
        status,r=self.call('/v1/turn',self.turn(expected_version=5))
        self.assertEqual(status,422)
        self.assertEqual(self.port.write_count,0)

    def test_player_intent_required(self):
        status,r=self.call('/v1/turn',self.turn(player_intent=''))
        self.assertEqual(status,422)
        self.assertEqual(self.port.write_count,0)

    def test_out_of_reach_attack_does_not_roll(self):
        with patch('gateway_service.roll',side_effect=AssertionError('No dice allowed')):
            status,r=self.call('/v1/turn',self.turn('melee_attack'))
        self.assertEqual(status,422)
        self.assertEqual(self.port.write_count,0)

    def test_valid_melee_attack_generates_audited_os_rolls(self):
        initial=self.port.store['Encounters'][self.guard.anchor.encounter_record_id]
        # Set combatants adjacent, retain unique version-0 genesis and projections.
        from test_airtable_persistence import initial as initial_battle
        state=initial_battle()
        b1=replace(state.combatants[1],x=1,y=0,hp=60,max_hp=60)
        state=replace(state,combatants=(state.combatants[0],b1))
        initial['fields']['Combatant State Snapshot']=encode_checkpoint(state)
        self.port.store['Session Logs']['genesis-0']['fields']['State Reconciliation Notes']=encode_checkpoint(state)
        for a in state.combatants:
            row=self.port.store['Combatant Turns v2']['row-'+a.actor_id]['fields']
            row.update(actor_projection(state,a.actor_id))
        status,r=self.call('/v1/turn',self.turn('melee_attack'))
        self.assertEqual(status,200,str(r))
        self.assertEqual(r['state']['checked_events'],1)
        self.assertEqual(r['version'],1)
        self.assertEqual(r['dice']['attack']['entropy_source'],SOURCE)
        self.assertTrue(1 <= r['dice']['attack_resolution']['natural'] <= 20)
        self.assertEqual(r['state']['checked_rolls'],2 if r['dice']['damage'] else 1)

    def test_unreplayable_legacy_history_blocks_every_write(self):
        status,_=self.call('/v1/turn',self.turn())
        self.assertEqual(status,200)
        ledger=next(iter(self.port.store['Event Ledger'].values()))
        ledger['fields']['State Changes Before → After']='Rook moved and attacked'
        before=self.port.write_count
        status,out=self.call('/v1/turn',self.turn(operation='end_turn',expected_version=1,
                                                    event_key=self.base['campaign_id']+':bad2'))
        self.assertIn(status,(409,422))
        self.assertEqual(self.port.write_count,before)
        status,out=self.call('/v1/state',self.base)
        self.assertEqual(status,409)
        self.assertIn('HALT',out['status'])

    def test_missing_required_auth_and_bad_json(self):
        status,r=self.api.dispatch('POST','/v1/state','Bearer '+KEY,b'not json')
        self.assertEqual(status,422)


if __name__=='__main__': unittest.main()
