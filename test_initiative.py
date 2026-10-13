"""Tie tests use injected dice only in QA, never accepted as live production."""
from dataclasses import replace
import unittest
from engine_core import roll, RulesError
from initiative import make_battle
from test_airtable_persistence import initial


def die(n):return lambda _:n

def fixture(*,rolls=(14,7),bonuses=(2,1),tie_order=None,allow_test_rolls=True):
    b=initial()
    r={'p1':roll(f'd20{bonuses[0]:+d}',die=die(rolls[0])),
       'b1':roll(f'd20{bonuses[1]:+d}',die=die(rolls[1]))}
    mods={'p1':bonuses[0],'b1':bonuses[1]}
    return make_battle(encounter_id='qa-fight',edition='2014',actors=b.combatants,
                       initiative_rolls=r,initiative_modifiers=mods,
                       traversable=b.traversable,tie_order=tie_order,allow_test_rolls=allow_test_rolls)


class InitiativeTests(unittest.TestCase):
    def test_order_and_total(self):
        b=fixture()
        self.assertEqual((b.active.actor_id,b.active.initiative,b.combatants[1].initiative),('p1',16,8))
    def test_automatic_npc_first(self):
        b=fixture(rolls=(1,17))
        self.assertEqual(b.active.actor_id,'b1')
    def test_tie_requires_explicit_decision(self):
        with self.assertRaises(RulesError):fixture(rolls=(10,11))
    def test_tie_manual_order(self):
        b=fixture(rolls=(10,11),tie_order=('b1','p1'))
        self.assertEqual([x.actor_id for x in b.combatants],['b1','p1'])
    def test_tie_manual_reverse_order(self):
        b=fixture(rolls=(10,11),tie_order=('p1','b1'))
        self.assertEqual(b.active.actor_id,'p1')
    def test_manual_order_cannot_override_roll(self):
        with self.assertRaises(RulesError):fixture(tie_order=('b1','p1'))
    def test_missing_modifier(self):
        b=initial();r={'p1':roll('d20+2',die=die(5)),'b1':roll('d20+1',die=die(6))}
        with self.assertRaises(RulesError):make_battle(encounter_id='qa',edition='2014',actors=b.combatants,
                     initiative_rolls=r,initiative_modifiers={'p1':2},traversable=b.traversable,allow_test_rolls=True)
    def test_wrong_modifier(self):
        b=initial();r={'p1':roll('d20+2',die=die(5)),'b1':roll('d20+1',die=die(6))}
        with self.assertRaises(RulesError):make_battle(encounter_id='qa',edition='2014',actors=b.combatants,
                     initiative_rolls=r,initiative_modifiers={'p1':1,'b1':1},traversable=b.traversable,allow_test_rolls=True)
    def test_test_rng_rejected_for_live_battle(self):
        with self.assertRaises(RulesError):fixture(allow_test_rolls=False)
    def test_actual_os_backed_dice(self):
        b=initial()
        rolls={'p1':roll('d20+2'),'b1':roll('d20+1')}
        totals=[r.total for r in rolls.values()]
        tie_order=('p1','b1') if totals[0]>=totals[1] else ('b1','p1')
        out=make_battle(encounter_id='qa-real-dice',edition='2014',actors=b.combatants,
                        initiative_rolls=rolls,initiative_modifiers={'p1':2,'b1':1},
                        traversable=b.traversable,tie_order=tie_order)
        self.assertEqual(out.active.initiative,max(totals))
    def test_unverified_map_rejected(self):
        b=initial();r={'p1':roll('d20+2',die=die(5)),'b1':roll('d20+1',die=die(6))}
        with self.assertRaises(RulesError):make_battle(encounter_id='qa',edition='2014',actors=b.combatants,
                     initiative_rolls=r,initiative_modifiers={'p1':2,'b1':1},traversable=(),allow_test_rolls=True)

if __name__=='__main__':unittest.main()
