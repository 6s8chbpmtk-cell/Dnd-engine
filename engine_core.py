"""Small, offline, edition-aware core for the ChatGPT + Airtable RPG engine.

This is an executable helper library, NOT a full D&D rules implementation.
Secure tool-executed rolls and basic invariant guards are explicit. Airtable
write transactions still require application-level reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Callable, Optional
import json
import re
import secrets

ENGINE_VERSION = "2.0.0-qa"
SOURCE = "Python secrets.randbelow / OS-backed CSPRNG (tool execution)"
DICE_RE = re.compile(r"^(?:(\d{1,2})?d(\d{1,4}))(?:([+-]\d{1,5}))?$", re.IGNORECASE)


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Roll:
    roll_id: str
    created_utc: str
    expression: str
    mode: str
    count: int  # kept dice in result; advantage/disadvantage roll 2d20, keep one
    sides: int
    all_faces: tuple[int, ...]
    kept_faces: tuple[int, ...]
    discarded_faces: tuple[int, ...]
    modifier: int
    raw_sum: int
    total: int
    entropy_source: str
    digest: str

    def to_dict(self):
        return asdict(self)


def _secure_die(sides: int) -> int:
    return secrets.randbelow(sides) + 1


def roll(expression: str, mode: str = "normal", *, die: Optional[Callable[[int], int]] = None) -> Roll:
    """Evaluate NdM+K. Advantage/disadvantage permitted only on one d20.

    A supplied die function is for deterministic TESTS ONLY. Production should
    omit it to use secrets.randbelow. No claiming independent proof of entropy.
    """
    compact = expression.replace(" ", "").lower()
    match = DICE_RE.fullmatch(compact)
    if not match:
        raise RulesError("Unsupported dice expression; expected NdM+K")
    count = int(match.group(1) or "1")
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)
    if count < 1 or count > 99 or sides < 2 or sides > 1000:
        raise RulesError("Dice count/faces out of bounds")
    if mode not in ("normal", "advantage", "disadvantage"):
        raise RulesError("Unknown mode")
    if mode != "normal" and (count != 1 or sides != 20):
        raise RulesError("Advantage/disadvantage applies to one d20 only")
    die_func = die if die is not None else _secure_die
    all_faces = tuple(die_func(sides) for _ in range(2 if mode != "normal" else count))
    if any(not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= sides) for v in all_faces):
        raise RulesError("Random source returned an invalid die face")
    if mode == "advantage":
        chosen = max(all_faces)
        kept, discarded = (chosen,), (min(all_faces),)
    elif mode == "disadvantage":
        chosen = min(all_faces)
        kept, discarded = (chosen,), (max(all_faces),)
    else:
        kept, discarded = all_faces, ()
    raw_sum = sum(kept)
    payload = dict(expression=compact, mode=mode, count=count, sides=sides,
                   all_faces=all_faces, kept_faces=kept, discarded_faces=discarded,
                   modifier=modifier, raw_sum=raw_sum, total=raw_sum + modifier,
                   entropy_source=SOURCE if die is None else "TEST INJECTION (not genuine random)",
                   created_utc=datetime.now(timezone.utc).isoformat(),
                   roll_id=secrets.token_hex(12))
    # Checks against accidental alteration only; a digest is NOT cryptographic
    # evidence of fair random generation or Airtable immutability.
    digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result = Roll(**payload, digest=digest)
    validate_roll(result)
    return result


def validate_roll(result: Roll) -> bool:
    """Validate count, face bounds, kept/discarded selection, arithmetic, digest."""
    if result.mode not in ("normal", "advantage", "disadvantage"):
        raise RulesError("Invalid roll mode")
    total_faces = 2 if result.mode != "normal" else result.count
    if len(result.all_faces) != total_faces or len(result.kept_faces) != (1 if result.mode != "normal" else result.count):
        raise RulesError("Wrong number of dice")
    if any(v < 1 or v > result.sides for v in result.all_faces):
        raise RulesError("Die out of range")
    if result.mode == "normal" and (result.kept_faces != result.all_faces or result.discarded_faces):
        raise RulesError("Normal mode selection mismatch")
    if result.mode == "advantage" and (result.kept_faces != (max(result.all_faces),) or result.discarded_faces != (min(result.all_faces),)):
        raise RulesError("Advantage selection mismatch")
    if result.mode == "disadvantage" and (result.kept_faces != (min(result.all_faces),) or result.discarded_faces != (max(result.all_faces),)):
        raise RulesError("Disadvantage selection mismatch")
    if result.raw_sum != sum(result.kept_faces) or result.total != result.raw_sum + result.modifier:
        raise RulesError("Roll arithmetic mismatch")
    payload = result.to_dict()
    actual_digest = payload.pop("digest")
    expected_digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if actual_digest != expected_digest:
        raise RulesError("Roll digest mismatch")
    return True


def d20_result(roll_obj: Roll, target: int, kind: str, edition: str) -> dict:
    """Core 5e d20 comparison. Specific feats/spells remain rules-layer concerns.

    Natural 20 and 1 are automatically hits/misses only on ATTACK rolls here.
    Do not confuse ability checks or saves with attacks. Critical damage must
    be separately resolved by the applicable rules.
    """
    if edition not in ("2014", "2024") or kind not in ("attack", "ability_check", "saving_throw"):
        raise RulesError("Unsupported edition or d20 test type")
    if roll_obj.sides != 20 or roll_obj.count != 1:
        raise RulesError("Expected one effective d20")
    natural = roll_obj.kept_faces[0]
    if kind == "attack" and natural == 20:
        success, critical, reason = True, True, "Natural 20 attack: automatic critical hit"
    elif kind == "attack" and natural == 1:
        success, critical, reason = False, False, "Natural 1 attack: automatic miss"
    else:
        success, critical, reason = roll_obj.total >= target, False, "Compare total against target"
    return {"edition": edition, "kind": kind, "natural": natural,
            "modifier": roll_obj.modifier, "total": roll_obj.total, "target": target,
            "success": success, "critical_hit": critical, "reason": reason,
            "roll_id": roll_obj.roll_id}


def ability_modifier(score: int) -> int:
    if not isinstance(score, int) or score < 1 or score > 30:
        raise RulesError("Ability score out of supported 1..30 range")
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    if not isinstance(level, int) or level < 1 or level > 20:
        raise RulesError("Level out of supported 1..20 range")
    return 2 + (level - 1) // 4


@dataclass(frozen=True)
class Turn:
    actor_id: str
    round_number: int
    hp: int
    max_hp: int
    temp_hp: int
    movement_remaining: int
    action_available: bool
    bonus_available: bool
    reaction_available: bool
    x: int
    y: int
    version: int

    def validate(self):
        if self.round_number < 1 or self.max_hp < 1 or not 0 <= self.hp <= self.max_hp:
            raise RulesError("Invalid HP or round")
        if self.temp_hp < 0 or self.movement_remaining < 0 or self.version < 0:
            raise RulesError("Invalid resource/turn state")
        return True

    def spend(self, resource: str) -> "Turn":
        key = {"action": "action_available", "bonus": "bonus_available", "reaction": "reaction_available"}.get(resource)
        if key is None:
            raise RulesError("Unknown action resource")
        if not getattr(self, key):
            raise RulesError("Action already spent; no duplicate spend")
        return replace(self, **{key: False, "version": self.version + 1})

    def move(self, feet: int, x: int, y: int) -> "Turn":
        if feet < 0 or feet > self.movement_remaining:
            raise RulesError("Movement budget exceeded")
        return replace(self, movement_remaining=self.movement_remaining-feet,
                       x=x, y=y, version=self.version+1)

    def damage(self, amount: int) -> "Turn":
        if amount < 0:
            raise RulesError("Negative damage; use healing instead")
        absorbed = min(self.temp_hp, amount)
        hp_loss = amount - absorbed
        return replace(self, temp_hp=self.temp_hp-absorbed,
                       hp=max(0, self.hp-hp_loss), version=self.version+1)

    def heal(self, amount: int) -> "Turn":
        if amount < 0:
            raise RulesError("Negative healing")
        return replace(self, hp=min(self.max_hp, self.hp + amount),version=self.version+1)


def preflight_mutation(*, expected_version: int, actual_version: int,
                       idempotency_key: str, committed_keys: set[str]) -> str:
    """Preflight only; NOT atomic enforcement against concurrent Airtable writers."""
    if not idempotency_key:
        raise RulesError("Missing idempotency key")
    if idempotency_key in committed_keys:
        return "REPLAY_NO_WRITE"
    if actual_version != expected_version:
        return "CONFLICT_HALT"
    return "PREPARE_PROPOSED_EVENT"


def concentration_dc(damage: int, edition: str) -> int:
    """2014 and 2024 differ at very high damage: 2024 caps DC at 30.

    Source: official D&D Beyond Basic Rules 2014 Spellcasting and
    2024 Rules Glossary > Concentration.
    """
    if damage < 0 or edition not in ("2014", "2024"):
        raise RulesError("Invalid concentration damage or edition")
    base = max(10, damage // 2)
    return min(30, base) if edition == "2024" else base


def critical_damage_expression(expression: str) -> str:
    """Double dice count, not fixed modifier, for a simple one-pool crit.

    Extra dice from class features, spell-specific effects, resistances,
    immunities and other exceptions MUST be resolved by source-specific rules.
    """
    match = DICE_RE.fullmatch(expression.replace(" ", "").lower())
    if not match:
        raise RulesError("Unsupported damage formula")
    count, sides, mod = int(match.group(1) or 1), int(match.group(2)), int(match.group(3) or 0)
    if count * 2 > 99 or sides < 2 or sides > 1000:
        raise RulesError("Critical dice out of supported bounds")
    return f"{2*count}d{sides}" + (f"{mod:+d}" if mod else "")


@dataclass(frozen=True)
class DeathSaveState:
    successes: int = 0
    failures: int = 0
    stable: bool = False
    dead: bool = False
    recovered_hp: int = 0


def apply_death_save(state: DeathSaveState, roll_obj: Roll, edition: str) -> DeathSaveState:
    """Common SRD death-save outcomes; only for a creature at 0 HP.

    A natural 1 counts as two failures; natural 20 restores one HP. Three
    successes stabilizes the creature; three failures causes death.
    Damage while at 0 HP and massive damage are separate mechanics.
    """
    if edition not in ("2014", "2024") or roll_obj.count != 1 or roll_obj.sides != 20:
        raise RulesError("Invalid death save rules profile or die")
    if state.dead or state.stable or state.recovered_hp:
        raise RulesError("Death saves only apply when dying at 0 HP")
    n = roll_obj.kept_faces[0]
    if n == 20:
        return DeathSaveState(recovered_hp=1)
    s, f = state.successes, state.failures
    if n == 1:
        f += 2
    elif roll_obj.total >= 10:
        s += 1
    else:
        f += 1
    if f >= 3:
        return DeathSaveState(successes=min(s,2), failures=3, dead=True)
    if s >= 3:
        return DeathSaveState(stable=True)
    return DeathSaveState(successes=s, failures=f)


def refreshed_turn(previous: Turn, *, speed: int, round_number: int) -> Turn:
    """Reset basic action economy at start of a new turn; validate preconditions.

    Does not apply effects that prevent actions/reactions or alter speed:
    these must be applied by the conditions/feature layer BEFORE resolution.
    """
    if round_number < previous.round_number or speed < 0:
        raise RulesError("Invalid round/speed")
    t = replace(previous, round_number=round_number, movement_remaining=speed,
                action_available=True, bonus_available=True,
                reaction_available=True, version=previous.version+1)
    t.validate()
    return t


def resource_spend(quantity: int, amount: int) -> int:
    if not isinstance(quantity, int) or not isinstance(amount,int) or quantity < 0 or amount < 1:
        raise RulesError("Invalid resource request")
    if amount > quantity:
        raise RulesError("Insufficient resources; no negative quantity")
    return quantity - amount
