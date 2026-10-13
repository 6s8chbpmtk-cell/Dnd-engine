"""Deterministic QA fixtures; outcomes are NOT genuine dice samples."""
import json
import unittest
from dataclasses import asdict,replace
from engine_core import RulesError, roll
from combat_runtime import (Fighter,Battle,apply_verified,replay,battle_from_dict,
                            event_from_dict,transition,cell_distance_ft)


def faces(*values):
    values=iter(values)
    return lambda _:next(values)


def dice(expression, *results, mode='normal'):
    return roll(expression,mode=mode,die=faces(*results))


def arena(*,fighter_x=0,bandit_x=2,concentration='',fighter_hp=12,bandit_hp=11):
    a=Fighter('fighter','heroes','player',fighter_hp,12,0,16,5,'1d8+3',17,2,30,fighter_x,0,
              attack_range_ft=5,move_left=30,concentration=concentration)
    b=Fighter('bandit','foes','gm',bandit_hp,11,0,12,3,'1d6+1',13,1,30,bandit_x,0)
    tiles=tuple((x,y) for x in range(8) for y in range(4))
    return Battle('qa-arena','2014',1,0,0,(a,b),tiles)


def attack_params(actor,target,at,dm=None,**kw):
    result=dict(actor_id=actor,target_id=target,attack=at,damage=dm,
                visibility_source='QA inspected unobstructed corridor',
                reaction_window='confirmed')
    result.update(kw)
    return result


class MapAndTurnTests(unittest.TestCase):
    def test_sane_fixture(self): self.assertTrue(arena().validate())
    def test_reject_bad_hp(self):
        b=arena(); bad=replace(b.combatants[0],hp=22)
        with self.assertRaises(RulesError):replace(b,combatants=(bad,b.combatants[1])).validate()
    def test_duplicate_actor_ids(self):
        b=arena()
        with self.assertRaises(RulesError):replace(b,combatants=(b.combatants[0],replace(b.combatants[1],actor_id='fighter'))).validate()
    def test_occupancy_violation(self):
        b=arena()
        with self.assertRaises(RulesError):replace(b,combatants=(b.combatants[0],replace(b.combatants[1],x=0))).validate()
    def test_unverified_map(self):
        with self.assertRaises(RulesError):replace(arena(),traversable=()).validate()
    def test_no_diagonal_clip_two_walls(self):
        b=replace(arena(bandit_x=5),blocked=((1,0),(0,1)))
        with self.assertRaises(RulesError):transition(b,'move',dict(actor_id='fighter',path=[[1,1]]))
    def test_diagonal_legal_open_cells(self):
        b=arena(bandit_x=5)
        a=transition(b,'move',dict(actor_id='fighter',path=[[1,1],[2,2]]))
        self.assertEqual((a.active.x,a.active.y,a.active.move_left,a.version),(2,2,20,1))
    def test_path_rejects_teleport(self):
        with self.assertRaises(RulesError):transition(arena(), 'move',dict(actor_id='fighter',path=[[4,0]]))
    def test_path_rejects_occupied(self):
        b=arena(bandit_x=1)
        with self.assertRaises(RulesError):transition(b,'move',dict(actor_id='fighter',path=[[1,0]]))
    def test_path_blocked(self):
        b=replace(arena(bandit_x=5),blocked=((1,0),))
        with self.assertRaises(RulesError):transition(b,'move',dict(actor_id='fighter',path=[[1,0]]))
    def test_path_outside_map(self):
        with self.assertRaises(RulesError):transition(arena(), 'move',dict(actor_id='fighter',path=[[-1,0]]))
    def test_difficult_terrain_double_cost(self):
        b=replace(arena(bandit_x=5),difficult=((1,0),))
        q=transition(b,'move',dict(actor_id='fighter',path=[[1,0]]))
        self.assertEqual(q.active.move_left,20)
    def test_move_budget_exceeded(self):
        b=replace(arena(bandit_x=7),combatants=(replace(arena(bandit_x=7).combatants[0],move_left=5),arena(bandit_x=7).combatants[1]))
        with self.assertRaises(RulesError):transition(b,'move',dict(actor_id='fighter',path=[[1,0],[2,0]]))
    def test_potential_opportunity_attack_fail_closed(self):
        b=arena(fighter_x=0,bandit_x=1)
        with self.assertRaises(RulesError):transition(b,'move',dict(actor_id='fighter',path=[[0,1],[0,2]]))
    def test_boolean_opportunity_bypass_rejected(self):
        b=arena(fighter_x=0,bandit_x=1)
        with self.assertRaises(RulesError):
            transition(b,'move',dict(actor_id='fighter',path=[[0,1],[0,2]],threat_checked=True))
    def test_disengage_then_leave_reach(self):
        b=arena(fighter_x=0,bandit_x=1)
        b=transition(b,'disengage',dict(actor_id='fighter'))
        self.assertFalse(b.active.action)
        q=transition(b,'move',dict(actor_id='fighter',path=[[0,1],[0,2]]))
        self.assertEqual((q.active.x,q.active.y),(0,2))
    def test_disengage_requires_action(self):
        b=transition(arena(),'spend',dict(actor_id='fighter',resource='action',label='QA bookkeeping'))
        with self.assertRaises(RulesError):transition(b,'disengage',dict(actor_id='fighter'))
    def test_not_another_actors_turn(self):
        with self.assertRaises(RulesError):transition(arena(),'spend',dict(actor_id='bandit',resource='action',label='GM-recorded empty action'))
    def test_action_spend_only_once(self):
        b=transition(arena(),'spend',dict(actor_id='fighter',resource='action',label='GM-recorded empty action'))
        with self.assertRaises(RulesError):transition(b,'spend',dict(actor_id='fighter',resource='action',label='GM-recorded empty action'))
    def test_spend_requires_intent(self):
        with self.assertRaises(RulesError):transition(arena(),'spend',dict(actor_id='fighter',resource='bonus'))
    def test_end_turn_advances_then_rounds(self):
        b=transition(arena(),'end_turn',dict(actor_id='fighter'))
        self.assertEqual((b.active.actor_id,b.round_no),('bandit',1))
        b=transition(b,'end_turn',dict(actor_id='bandit'))
        self.assertEqual((b.active.actor_id,b.round_no),('fighter',2))
    def test_end_turn_refreshes_action(self):
        b=transition(arena(),'spend',dict(actor_id='fighter',resource='action',label='GM-recorded empty action'))
        b=transition(b,'end_turn',dict(actor_id='fighter'))
        b=transition(b,'end_turn',dict(actor_id='bandit'))
        self.assertTrue(b.active.action)
    def test_status_after_victory(self):
        b=arena(bandit_hp=0)
        # inert 0HP NPC allowed, but active player can finish encounter
        q=transition(b,'end_turn',dict(actor_id='fighter'))
        self.assertEqual(q.status,'complete')
    def test_serialize_roundtrip(self):
        b=arena(); restored=battle_from_dict(json.loads(json.dumps(b.to_dict())))
        self.assertEqual(restored,b)
    def test_distance_diagonal_5ft(self):
        b=arena(bandit_x=1)
        self.assertEqual(cell_distance_ft(b.combatants[0],b.combatants[1]),5)


class AttackTests(unittest.TestCase):
    def adjacent(self, **kwargs):return arena(bandit_x=1,**kwargs)
    def test_attack_hit_applies_dice(self):
        b=self.adjacent()
        q=transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',12),dice('1d8+3',4)))
        self.assertEqual(q.combatants[1].hp,4)
        self.assertFalse(q.active.action)
    def test_miss_spends_action_but_no_hp_loss(self):
        q=transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',2)))
        self.assertEqual(q.combatants[1].hp,11)
        self.assertFalse(q.active.action)
    def test_miss_cannot_roll_damage(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',2),dice('1d8+3',2)))
    def test_hit_requires_damage_roll(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',15)))
    def test_crit_doubles_damage_dice(self):
        q=transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',20),dice('2d8+3',4,2)))
        self.assertEqual(q.combatants[1].hp,2)
    def test_crit_reject_normal_dice(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',20),dice('1d8+3',4)))
    def test_natural_one_auto_misses_even_big_modifier(self):
        b=self.adjacent()
        a=replace(b.combatants[0],attack_bonus=99)
        q=transition(replace(b,combatants=(a,b.combatants[1])),'attack',attack_params('fighter','bandit',dice('d20+99',1)))
        self.assertEqual(q.combatants[1].hp,11)
    def test_wrong_attack_bonus(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+3',17)))
    def test_melee_out_of_reach(self):
        with self.assertRaises(RulesError):transition(arena(),'attack',attack_params('fighter','bandit',dice('d20+5',17),dice('1d8+3',4)))
    def test_visibility_requires_grounding(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',17),dice('1d8+3',4),visibility_source=''))
    def test_reaction_window_not_implicit(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',dict(actor_id='fighter',target_id='bandit',attack=dice('d20+5',17),damage=dice('1d8+3',4),visibility_source='QA'))
    def test_cover_explicit_bonus(self):
        b=self.adjacent()
        q=transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',8),cover_bonus=2,cover_source='GM verified half cover'))
        self.assertEqual(q.combatants[1].hp,11)
    def test_cover_requires_source(self):
        with self.assertRaises(RulesError):transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',8),cover_bonus=2))
    def test_range_explicit_limit(self):
        b=arena(); a=replace(b.combatants[0],attack_range_ft=15)
        q=transition(replace(b,combatants=(a,b.combatants[1])),'attack',attack_params('fighter','bandit',dice('d20+5',16),dice('1d8+3',4),attack_style='ranged'))
        self.assertEqual(q.combatants[1].hp,4)
    def test_ranged_close_requires_disadvantage(self):
        b=self.adjacent();a=replace(b.combatants[0],attack_range_ft=30)
        b=replace(b,combatants=(a,b.combatants[1]))
        with self.assertRaises(RulesError):transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',15),dice('1d8+3',4),attack_style='ranged'))
    def test_ranged_close_with_disadvantage(self):
        b=self.adjacent();a=replace(b.combatants[0],attack_range_ft=30)
        b=replace(b,combatants=(a,b.combatants[1]))
        q=transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',15,6,mode='disadvantage'),attack_style='ranged',advantage_source='QA adjacent enemy verified'))
        self.assertEqual(q.combatants[1].hp,11)
    def test_attack_cannot_hit_ko_without_new_rule(self):
        b=arena(bandit_x=1,bandit_hp=0)
        with self.assertRaises(RulesError):transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',15),dice('1d8+3',4)))
    def test_double_attack_prevented(self):
        q=transition(self.adjacent(),'attack',attack_params('fighter','bandit',dice('d20+5',5)))
        with self.assertRaises(RulesError):transition(q,'attack',attack_params('fighter','bandit',dice('d20+5',5)))
    def test_concentration_check_required_after_hit(self):
        b=self.adjacent(concentration='Bless')
        b=transition(b,'end_turn',dict(actor_id='fighter'))
        q=transition(b,'attack',attack_params('bandit','fighter',dice('d20+3',19),dice('1d6+1',4)))
        self.assertEqual(q.combatants[0].concentration_save_pending,5)
        with self.assertRaises(RulesError):transition(q,'end_turn',dict(actor_id='bandit'))
    def test_concentration_success_keeps_spell(self):
        b=self.adjacent(concentration='Bless'); b=transition(b,'end_turn',dict(actor_id='fighter'))
        b=transition(b,'attack',attack_params('bandit','fighter',dice('d20+3',19),dice('1d6+1',4)))
        q=transition(b,'concentration',dict(actor_id='fighter',save=dice('d20+2',14)))
        self.assertEqual(q.combatants[0].concentration,'Bless')
    def test_concentration_failure_drops_spell(self):
        b=self.adjacent(concentration='Bless'); b=transition(b,'end_turn',dict(actor_id='fighter'))
        b=transition(b,'attack',attack_params('bandit','fighter',dice('d20+3',19),dice('1d6+1',4)))
        q=transition(b,'concentration',dict(actor_id='fighter',save=dice('d20+2',2)))
        self.assertEqual(q.combatants[0].concentration,'')
    def test_concentration_wrong_save_bonus(self):
        b=self.adjacent(concentration='Bless'); b=transition(b,'end_turn',dict(actor_id='fighter'))
        b=transition(b,'attack',attack_params('bandit','fighter',dice('d20+3',19),dice('1d6+1',4)))
        with self.assertRaises(RulesError):transition(b,'concentration',dict(actor_id='fighter',save=dice('d20+9',11)))


class LedgerTests(unittest.TestCase):
    def test_event_exact_replay_no_write(self):
        b=arena()
        kw=dict(event_key='qa:one',expected_version=0,operation='move',params=dict(actor_id='fighter',path=[[1,0]]))
        b,events,status=apply_verified(b,(),**kw)
        self.assertEqual(status,'PROPOSED_LOCAL_ONLY')
        second,events2,status=apply_verified(b,events,**kw)
        self.assertEqual((status,second,events2),('REPLAY_NO_WRITE',b,events))
    def test_event_collision_fail(self):
        b=arena()
        new,ev,_=apply_verified(b,(),event_key='id-1',expected_version=0,operation='move',params=dict(actor_id='fighter',path=[[1,0]]))
        with self.assertRaises(RulesError):apply_verified(new,ev,event_key='id-1',expected_version=0,operation='move',params=dict(actor_id='fighter',path=[[1,1]]))
    def test_version_conflict_fail(self):
        with self.assertRaises(RulesError):apply_verified(arena(),(),event_key='id-1',expected_version=9,operation='end_turn',params={'actor_id':'fighter'})
    def test_event_strict_checkpoint_replay(self):
        b=arena(); original=b
        for i,operation,params in [(1,'move',dict(actor_id='fighter',path=[[1,0]])),(2,'attack',attack_params('fighter','bandit',dice('d20+5',12),dice('1d8+3',4)))]:
            b,ev,status=apply_verified(b,() if i==1 else ev,event_key=f'id-{i}',expected_version=b.version,operation=operation,params=params)
        self.assertEqual(replay(original,ev),b)
    def test_event_serialization_roundtrip(self):
        b=arena();prev=b
        b,ev,_=apply_verified(b,(),event_key='attack-1',expected_version=0,operation='end_turn',params={'actor_id':'fighter'})
        encoded=json.dumps([asdict(e) for e in ev]);decoded=tuple(event_from_dict(q) for q in json.loads(encoded))
        self.assertEqual(replay(prev,decoded),b)
    def test_attack_event_json_roundtrip(self):
        b=arena(bandit_x=1)
        b2,ev,_=apply_verified(b,(),event_key='attack-1',expected_version=0,operation='attack',params=attack_params('fighter','bandit',dice('d20+5',12),dice('1d8+3',2)))
        encoded=json.dumps([asdict(e) for e in ev])
        self.assertEqual(replay(b,tuple(event_from_dict(d) for d in json.loads(encoded))),b2)
    def test_replay_reject_wrong_checkpoint(self):
        b=arena();_,ev,_=apply_verified(b,(),event_key='turn',expected_version=0,operation='end_turn',params={'actor_id':'fighter'})
        with self.assertRaises(RulesError):replay(replace(b,version=1),ev)
    def test_replay_reject_tamper(self):
        b=arena();_,ev,_=apply_verified(b,(),event_key='turn',expected_version=0,operation='end_turn',params={'actor_id':'fighter'})
        with self.assertRaises(RulesError):replay(b,(replace(ev[0],operation='move'),))
    def test_replay_reject_duplicate_events(self):
        b=arena();_,ev,_=apply_verified(b,(),event_key='turn',expected_version=0,operation='end_turn',params={'actor_id':'fighter'})
        with self.assertRaises(RulesError):replay(b,ev+ev)


class TwoRoundQATest(unittest.TestCase):
    def test_three_round_qa_fixture(self):
        # Completely synthetic fighter vs bandit, no live PC or campaign changes.
        start=arena(bandit_x=1)
        state,events=start,()
        actions=[
            ('attack',attack_params('fighter','bandit',dice('d20+5',4))),
            ('end_turn',dict(actor_id='fighter')),
            ('attack',attack_params('bandit','fighter',dice('d20+3',19),dice('1d6+1',4))),
            ('end_turn',dict(actor_id='bandit')),
            ('attack',attack_params('fighter','bandit',dice('d20+5',16),dice('1d8+3',5))),
            ('end_turn',dict(actor_id='fighter')),
            ('attack',attack_params('bandit','fighter',dice('d20+3',5))),
            ('end_turn',dict(actor_id='bandit')),
            ('attack',attack_params('fighter','bandit',dice('d20+5',11),dice('1d8+3',3))),
            ('end_turn',dict(actor_id='fighter')),
        ]
        for i,(op,params) in enumerate(actions):
            state,events,_=apply_verified(state,events,event_key=f'qa:encounter:r{i}',
                                          expected_version=state.version,operation=op,params=params)
        self.assertEqual(state.status,'complete')
        self.assertEqual((state.combatants[0].hp,state.combatants[1].hp),(7,0))
        self.assertEqual(state.version,10)
        self.assertEqual(replay(start,events),state)
        serial=json.dumps(dict(initial=start.to_dict(),events=[asdict(x) for x in events],final=state.to_dict()))
        sample=json.loads(serial)
        replayed=replay(battle_from_dict(sample['initial']),tuple(event_from_dict(e) for e in sample['events']))
        self.assertEqual(replayed.fingerprint(),battle_from_dict(sample['final']).fingerprint())


if __name__=='__main__':unittest.main()


class SafetyRegressionTests(unittest.TestCase):
    def test_reject_float_map_coordinates(self):
        with self.assertRaises(RulesError): replace(arena(),traversable=((0.5,0),)).validate()

    def test_reject_boolean_active_index(self):
        with self.assertRaises(RulesError): replace(arena(),active_index=True).validate()

    def test_reject_boolean_state_version(self):
        with self.assertRaises(RulesError): replace(arena(),version=False).validate()

    def test_reject_unimplemented_dodge(self):
        with self.assertRaises(RulesError): transition(arena(),'spend',{'actor_id':'fighter','resource':'action','label':'Dodge'})

    def test_reject_unimplemented_spell(self):
        with self.assertRaises(RulesError): transition(arena(),'spend',{'actor_id':'fighter','resource':'action','label':'Cast a spell'})

    def test_reject_unjustified_advantage(self):
        b=arena(bandit_x=1)
        with self.assertRaises(RulesError):transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',16,8,mode='advantage'),dice('1d8+3',4)))

    def test_accept_justified_advantage(self):
        b=arena(bandit_x=1)
        q=transition(b,'attack',attack_params('fighter','bandit',dice('d20+5',16,8,mode='advantage'),dice('1d8+3',4),advantage_source='QA spell condition confirmed'))
        self.assertEqual(q.combatants[1].hp,4)
