"""Synthetic fuzz regressions. Fixed seeds and injected dice never become live rolls."""
import json
import random
from dataclasses import asdict, replace
import unittest
from engine_core import roll, d20_result, critical_damage_expression
from combat_runtime import apply_verified, replay, battle_from_dict, event_from_dict
from test_airtable_persistence import initial


class RandomizedEncounterRegression(unittest.TestCase):
    def test_one_hundred_multiround_battles_and_exact_replays(self):
        totals={'complete':0,'attacks':0,'misses':0,'critical_hits':0,'rounds':0}
        for seed in range(100):
            rng=random.Random(980020+seed)
            original=initial()
            original=replace(original, combatants=(original.combatants[0],replace(original.combatants[1],x=1)))
            current=original
            ledger=()
            for idx in range(50):
                if current.status=='complete':break
                actor=current.active
                enemy=next(a for a in current.combatants if a.team!=actor.team)
                if not actor.action:
                    operation='end_turn';params={'actor_id':actor.actor_id}
                else:
                    roll_at=roll(f'd20{actor.attack_bonus:+d}',die=lambda _:rng.randint(1,20))
                    outcome=d20_result(roll_at,enemy.ac,'attack',current.edition)
                    if outcome['success']:
                        expression=(critical_damage_expression(actor.damage_expression)
                                    if outcome['critical_hit'] else actor.damage_expression)
                        roll_dm=roll(expression,die=lambda faces:rng.randint(1,faces))
                    else:
                        roll_dm=None
                        totals['misses']+=1
                    totals['attacks']+=1
                    totals['critical_hits']+=int(outcome['critical_hit'])
                    operation='attack'
                    params={'actor_id':actor.actor_id,'target_id':enemy.actor_id,
                            'attack':roll_at,'damage':roll_dm,
                            'visibility_source':'QA unobstructed known map',
                            'reaction_window':'confirmed'}
                current,ledger,_=apply_verified(current,ledger,event_key=f'qa:{seed}:{idx}',
                             expected_version=current.version,operation=operation,params=params)
                current.validate()
                self.assertEqual(current.version,len(ledger))
                for a in current.combatants:
                    self.assertGreaterEqual(a.hp,0)
                    self.assertGreaterEqual(a.temp_hp,0)
                if current.status=='complete':break
            self.assertLess(idx,49,'Encounter unexpectedly nonterminating')
            self.assertEqual(replay(original,ledger).fingerprint(),current.fingerprint())
            encoded=json.dumps({'start':original.to_dict(),'events':[asdict(x) for x in ledger]})
            restored=json.loads(encoded)
            state2=replay(battle_from_dict(restored['start']),tuple(event_from_dict(e) for e in restored['events']))
            self.assertEqual(state2.fingerprint(),current.fingerprint())
            totals['complete']+=int(current.status=='complete')
            totals['rounds']+=current.round_no
        self.assertEqual(totals['complete'],100)
        self.assertGreater(totals['attacks'],200)
        self.assertGreater(totals['misses'],0)
        self.assertGreater(totals['critical_hits'],0)

if __name__=='__main__':unittest.main()
