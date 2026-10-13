"""Initiative bootstrap with complete d20 audit and explicit tie adjudication.

Player-versus-NPC tie ordering is never silently assigned; caller supplies an
ordered list of actor IDs and the GM must establish the source for modifiers.
2014/2024 5e initiative is a Dexterity check; class feature exceptions require
an independently verified modifier before creating the combat snapshot.
"""
from __future__ import annotations
from dataclasses import replace
from engine_core import Roll, RulesError, SOURCE, validate_roll
from combat_runtime import Fighter, Battle


def make_battle(*, encounter_id:str, edition:str, actors:tuple[Fighter,...],
                initiative_rolls:dict[str,Roll], initiative_modifiers:dict[str,int],
                traversable:tuple[tuple[int,int],...],blocked:tuple[tuple[int,int],...]=(),
                difficult:tuple[tuple[int,int],...]=(), tie_order:tuple[str,...]|None=None,
                allow_test_rolls:bool=False) -> Battle:
    ids=[a.actor_id for a in actors]
    if not ids or len(set(ids))!=len(ids):raise RulesError('Missing/duplicate combatants')
    if set(initiative_rolls)!=set(ids) or set(initiative_modifiers)!=set(ids):
        raise RulesError('Each actor requires one verified initiative roll and modifier')
    results={}
    for a in actors:
        bonus=initiative_modifiers[a.actor_id]
        r=initiative_rolls[a.actor_id]
        if type(bonus) is not int or not isinstance(r,Roll):
            raise RulesError('Unverified initiative modifier/roll')
        validate_roll(r)
        if r.mode!='normal' or r.count!=1 or r.sides!=20 or r.modifier!=bonus:
            raise RulesError('Initiative roll must be 1d20 + source-verified modifier')
        if not allow_test_rolls and r.entropy_source!=SOURCE:
            raise RulesError('Test dice may not initialize a live combat')
        results[a.actor_id]=r.total
    unique_totals=set(results.values())
    if len(unique_totals)!=len(ids) and tie_order is None:
        raise RulesError('Initiative tie requires explicit DM/player tie order')
    if tie_order is not None:
        if len(tie_order)!=len(ids) or set(tie_order)!=set(ids):
            raise RulesError('Explicit initiative order must contain each actor once')
        if any(results[tie_order[i]]<results[tie_order[i+1]] for i in range(len(ids)-1)):
            raise RulesError('Tie-break order cannot override higher initiative rolls')
        ordered_ids=tie_order
    else:
        ordered_ids=tuple(sorted(ids,key=lambda x:results[x],reverse=True))
    source={a.actor_id:a for a in actors}
    ordered=tuple(replace(source[k],initiative=results[k],action=True,bonus=True,
                          reaction=True,move_left=source[k].speed_ft) for k in ordered_ids)
    battle=Battle(encounter_id,edition,1,0,0,ordered,traversable,blocked,difficult)
    battle.validate()
    return battle
