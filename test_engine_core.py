import unittest
from dataclasses import replace
from engine_core import (roll, validate_roll, d20_result, ability_modifier, proficiency_bonus,
                         Turn, preflight_mutation, RulesError)


def die_values(*values):
    it = iter(values)
    return lambda sides: next(it)


class DiceTests(unittest.TestCase):
    def test_basic_roll(self):
        r = roll("2d6+3", die=die_values(4, 6))
        self.assertEqual((r.all_faces, r.raw_sum, r.total), ((4,6), 10, 13))
        self.assertTrue(validate_roll(r))

    def test_os_entropy_roll(self):
        r = roll("1d20+2")
        self.assertTrue(1 <= r.kept_faces[0] <= 20)
        self.assertEqual(r.total, r.kept_faces[0] + 2)
        self.assertTrue(validate_roll(r))
        self.assertTrue(r.entropy_source.startswith("Python secrets"))

    def test_advantage(self):
        r = roll("d20+5", "advantage", die=die_values(3, 17))
        self.assertEqual((r.kept_faces, r.discarded_faces, r.total), ((17,), (3,), 22))

    def test_disadvantage(self):
        r = roll("d20-1", "disadvantage", die=die_values(19, 7))
        self.assertEqual((r.kept_faces, r.discarded_faces, r.total), ((7,), (19,), 6))

    def test_invalid_advantage(self):
        with self.assertRaises(RulesError): roll("2d6", "advantage")

    def test_bad_roll_formula(self):
        with self.assertRaises(RulesError): roll("999d10000")

    def test_invalid_face_source(self):
        with self.assertRaises(RulesError): roll("1d20", die=die_values(21))

    def test_digest_tampering(self):
        r = roll("1d20+2",die=die_values(10))
        with self.assertRaises(RulesError): validate_roll(replace(r, total=100))

    def test_advantage_tampering(self):
        r = roll("d20", "advantage", die=die_values(1, 20))
        with self.assertRaises(RulesError): validate_roll(replace(r, kept_faces=(1,)))

    def test_attack_critical_both_editions(self):
        for edition in ("2014", "2024"):
            r = roll("d20-5",die=die_values(20))
            out = d20_result(r, target=30, kind="attack", edition=edition)
            self.assertTrue(out["success"])
            self.assertTrue(out["critical_hit"])

    def test_attack_natural_one_miss(self):
        r = roll("d20+100",die=die_values(1))
        self.assertFalse(d20_result(r, 5, "attack", "2014")["success"])

    def test_natural_20_ability_not_auto_success(self):
        for ed in ("2014", "2024"):
            r = roll("d20-5",die=die_values(20))
            self.assertFalse(d20_result(r, 30, "ability_check", ed)["success"])

    def test_natural_one_save_not_auto_fail(self):
        for ed in ("2014", "2024"):
            r = roll("d20+20",die=die_values(1))
            self.assertTrue(d20_result(r, 15, "saving_throw", ed)["success"])

    def test_ability_modifiers(self):
        self.assertEqual([ability_modifier(s) for s in (1,8,10,11,12,20)],[-5,-1,0,0,1,5])

    def test_proficiency(self):
        self.assertEqual([proficiency_bonus(s) for s in (1,4,5,8,9,12,13,16,17,20)],[2,2,3,3,4,4,5,5,6,6])


class TurnTests(unittest.TestCase):
    def setup_turn(self):
        return Turn("qa-fighter",1,9,12,4,30,True,True,True,0,0,0)

    def test_damage_temp_hp_first(self):
        t=self.setup_turn().damage(7)
        self.assertEqual((t.hp,t.temp_hp,t.version),(6,0,1))

    def test_damage_clamped_zero(self):
        t=self.setup_turn().damage(100)
        self.assertEqual(t.hp,0)

    def test_healing_capped(self):
        self.assertEqual(self.setup_turn().heal(100).hp,12)

    def test_spend_duplicate(self):
        t=self.setup_turn().spend("action")
        with self.assertRaises(RulesError): t.spend("action")

    def test_reaction_separate(self):
        t=self.setup_turn().spend("reaction")
        self.assertFalse(t.reaction_available)
        self.assertTrue(t.action_available)

    def test_movement_budget(self):
        t=self.setup_turn().move(20,4,0)
        self.assertEqual(t.movement_remaining,10)
        with self.assertRaises(RulesError):t.move(15,7,0)

    def test_negative_resource(self):
        with self.assertRaises(RulesError):self.setup_turn().damage(-1)
        with self.assertRaises(RulesError):self.setup_turn().move(-2,0,0)

    def test_invalid_hp_state(self):
        with self.assertRaises(RulesError): replace(self.setup_turn(),hp=14).validate()

    def test_idempotency_replay(self):
        self.assertEqual(preflight_mutation(expected_version=8, actual_version=8,
                                           idempotency_key="unique-1", committed_keys={"unique-1"}),"REPLAY_NO_WRITE")

    def test_idempotency_conflict(self):
        self.assertEqual(preflight_mutation(expected_version=8, actual_version=9,
                                           idempotency_key="unique-2",committed_keys=set()),"CONFLICT_HALT")

    def test_idempotency_propose(self):
        self.assertEqual(preflight_mutation(expected_version=8, actual_version=8,
                                           idempotency_key="unique-3",committed_keys=set()),"PREPARE_PROPOSED_EVENT")


from engine_core import (concentration_dc, critical_damage_expression, DeathSaveState,
                         apply_death_save, refreshed_turn, resource_spend)

class ExtendedRulesTests(unittest.TestCase):
    def test_concentration_2014_uncapped(self):
        self.assertEqual(concentration_dc(100,"2014"),50)

    def test_concentration_2024_cap(self):
        self.assertEqual(concentration_dc(100,"2024"),30)

    def test_concentration_round_down(self):
        self.assertEqual(concentration_dc(23,"2014"),11)
        self.assertEqual(concentration_dc(18,"2024"),10)

    def test_crit_doubles_dice_not_mod(self):
        self.assertEqual(critical_damage_expression("1d8+3"),"2d8+3")
        self.assertEqual(critical_damage_expression("2d6-1"),"4d6-1")

    def test_death_nat_one(self):
        d=apply_death_save(DeathSaveState(failures=1),roll("d20",die=die_values(1)),"2024")
        self.assertTrue(d.dead)
        self.assertEqual(d.failures,3)

    def test_death_nat_20_recover(self):
        d=apply_death_save(DeathSaveState(failures=2),roll("d20",die=die_values(20)),"2014")
        self.assertEqual(d.recovered_hp,1)
        self.assertEqual(d.failures,0)

    def test_death_three_successes(self):
        d=apply_death_save(DeathSaveState(successes=2),roll("d20",die=die_values(14)),"2014")
        self.assertTrue(d.stable)
        self.assertEqual(d.successes,0)

    def test_death_no_roll_after_stable(self):
        with self.assertRaises(RulesError):
            apply_death_save(DeathSaveState(stable=True),roll("d20",die=die_values(7)),"2014")

    def test_death_save_bonuses(self):
        d=apply_death_save(DeathSaveState(),roll("d20+4",die=die_values(7)),"2024")
        self.assertEqual(d.successes,1)

    def test_turn_refresh(self):
        prev=Turn("qa",1,10,10,0,0,False,False,False,2,2,3)
        nxt=refreshed_turn(prev,speed=30,round_number=2)
        self.assertEqual((nxt.movement_remaining,nxt.action_available,nxt.reaction_available,nxt.version),(30,True,True,4))

    def test_resource_spend(self):
        self.assertEqual(resource_spend(3,2),1)
        with self.assertRaises(RulesError): resource_spend(1,2)
        with self.assertRaises(RulesError): resource_spend(1,0)




class CliIntegrationTests(unittest.TestCase):
    def test_cli_roll_json_schema(self):
        import subprocess, json, sys
        from pathlib import Path
        script=Path(__file__).resolve().parent/'scripts'/'roll_cli.py'
        out=subprocess.run([sys.executable, str(script), 'd20+3', '--mode', 'advantage',
                            '--kind', 'attack', '--target', '14', '--edition', '2024'],
                           capture_output=True, text=True, check=True)
        data=json.loads(out.stdout)
        self.assertIn('roll', data)
        self.assertEqual(data['roll']['mode'], 'advantage')
        self.assertEqual(data['roll']['entropy_source'].split(' / ')[0], 'Python secrets.randbelow')
        self.assertIn('resolution', data)
        self.assertEqual(data['resolution']['target'], 14)

    def test_cli_invalid_expression_exit_code(self):
        import subprocess, sys
        from pathlib import Path
        script=Path(__file__).resolve().parent/'scripts'/'roll_cli.py'
        out=subprocess.run([sys.executable, str(script), '1000d99999'],
                           capture_output=True, text=True)
        self.assertEqual(out.returncode,2)
        self.assertIn('error', out.stderr)

if __name__ == '__main__':
    unittest.main(verbosity=2)


class RollShapeSecurityRegression(unittest.TestCase):
    def forge(self, roll_obj, **changes):
        from dataclasses import replace, asdict
        import hashlib,json
        item=replace(roll_obj,**changes)
        payload=asdict(item)
        payload.pop('digest')
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        return replace(item,digest=digest)

    def test_dice_count_mismatch_rejected_even_with_reforged_digest(self):
        from engine_core import validate_roll
        b=roll('1d6+2',die=lambda n:4)
        forged=self.forge(b,count=0,expression='0d6+2',all_faces=(),kept_faces=(),raw_sum=0,total=2)
        with self.assertRaises(RulesError):validate_roll(forged)

    def test_sides_mismatch_rejected_even_with_reforged_digest(self):
        from engine_core import validate_roll
        b=roll('1d6+2',die=lambda n:4)
        forged=self.forge(b,sides=8)
        with self.assertRaises(RulesError):validate_roll(forged)

    def test_non_d20_advantage_rejected_with_reforged_digest(self):
        from engine_core import validate_roll
        b=roll('1d20',mode='advantage',die=lambda n:10)
        forged=self.forge(b,sides=6,expression='d6',all_faces=(5,2),kept_faces=(5,),discarded_faces=(2,),raw_sum=5,total=5)
        with self.assertRaises(RulesError):validate_roll(forged)

    def test_boolean_die_face_rejected_even_with_reforged_digest(self):
        from engine_core import validate_roll
        b=roll('1d6',die=lambda n:1)
        forged=self.forge(b,all_faces=(True,),kept_faces=(True,))
        with self.assertRaises(RulesError):validate_roll(forged)
