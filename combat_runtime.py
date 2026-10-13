"""Conservative 5e tactical combat state machine; explicit gaps fail closed.

No network operations or player-choice delegation. Input rolls come from
engine_core.roll; deterministic injection is allowed ONLY by offline tests.
The ledger is for local validation; Airtable acknowledgement is separately
reconciled and must never be inferred from this module alone.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace, is_dataclass
from hashlib import sha256
from typing import Any
import json

from engine_core import RulesError, Roll, validate_roll, d20_result, critical_damage_expression


def _canonical(item: Any) -> Any:
    if is_dataclass(item):
        return _canonical(asdict(item))
    if isinstance(item, dict):
        return {k:_canonical(v) for k,v in item.items()}
    if isinstance(item, (list,tuple)):
        return [_canonical(v) for v in item]
    return item


def _hash(item: Any) -> str:
    return sha256(json.dumps(_canonical(item), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Fighter:
    actor_id: str
    team: str
    controller: str  # player | gm ; NPCs act on their own GM turn
    hp: int
    max_hp: int
    temp_hp: int
    ac: int
    attack_bonus: int
    damage_expression: str
    initiative: int
    con_save_bonus: int
    speed_ft: int
    x: int
    y: int
    reach_ft: int = 5
    attack_range_ft: int = 5
    action: bool = True
    bonus: bool = True
    reaction: bool = True
    move_left: int = 30
    stable: bool = False
    dead: bool = False
    death_successes: int = 0
    death_failures: int = 0
    concentration: str = ''
    concentration_save_pending: int = 0
    disengaged: bool = False

    def validate(self):
        if not self.actor_id or self.controller not in ('player','gm') or not self.team:
            raise RulesError('Actor identity/controller invalid')
        nums=(self.hp,self.max_hp,self.temp_hp,self.ac,self.speed_ft,self.reach_ft,self.attack_range_ft,self.move_left)
        if any(type(x) is not int for x in nums):
            raise RulesError('Combat stats must be integers')
        if any(type(x) is not int for x in (self.initiative,self.attack_bonus,self.con_save_bonus,self.x,self.y,
                                             self.death_successes,self.death_failures,self.concentration_save_pending)):
            raise RulesError('Combatant bonus, coordinate or save counter not an integer')
        if any(type(x) is not bool for x in (self.action,self.bonus,self.reaction,self.stable,self.dead,self.disengaged)):
            raise RulesError('Combatant action/condition flags must be boolean')
        if not isinstance(self.damage_expression,str) or not self.damage_expression.strip():
            raise RulesError('Missing combatant damage expression')
        if not isinstance(self.concentration,str):
            raise RulesError('Concentration must be a spell identifier string')
        if self.max_hp < 1 or not 0 <= self.hp <= self.max_hp or self.temp_hp < 0 or self.ac < 1:
            raise RulesError('Invalid actor HP/AC')
        if self.speed_ft < 0 or self.move_left < 0 or self.move_left > self.speed_ft:
            raise RulesError('Invalid movement budget')
        if self.reach_ft < 5 or self.attack_range_ft < self.reach_ft:
            raise RulesError('Invalid reach/range')
        if not 0 <= self.death_successes <= 2 or not 0 <= self.death_failures <= 2:
            raise RulesError('Invalid death-save counters')
        if self.concentration_save_pending < 0 or (self.hp == 0 and self.concentration):
            raise RulesError('Invalid concentration state')
        if self.dead and self.hp != 0:
            raise RulesError('Dead actor cannot have HP')
        if self.stable and (self.hp != 0 or self.dead):
            raise RulesError('Stable actor state inconsistent')
        return True


@dataclass(frozen=True)
class Battle:
    encounter_id: str
    edition: str
    round_no: int
    active_index: int
    version: int
    combatants: tuple[Fighter, ...]  # preverified initiative + tie-breaking order
    traversable: tuple[tuple[int,int], ...]
    blocked: tuple[tuple[int,int], ...] = ()
    difficult: tuple[tuple[int,int], ...] = ()
    status: str = 'active'

    @property
    def active(self) -> Fighter:
        return self.combatants[self.active_index]

    def validate(self):
        if self.edition not in ('2014','2024') or not self.encounter_id or not self.combatants:
            raise RulesError('Missing edition/encounter/combatants')
        if any(type(n) is not int for n in (self.version,self.round_no,self.active_index)):
            raise RulesError('Battle version, round and active index must be integers')
        if self.version < 0 or self.round_no < 1 or not 0 <= self.active_index < len(self.combatants):
            raise RulesError('Invalid battle version/turn')
        if self.status not in ('active','complete'):
            raise RulesError('Unknown battle status')
        ids=[p.actor_id for p in self.combatants]
        if len(set(ids)) != len(ids):
            raise RulesError('Duplicate combatant IDs')
        for collection in (self.traversable,self.blocked,self.difficult):
            if any(not isinstance(c,(tuple,list)) or len(c)!=2 or any(type(n) is not int for n in c) for c in collection):
                raise RulesError('Map tile coordinates must be integer x/y pairs')
        if len(set(tuple(c) for c in self.traversable)) != len(self.traversable) or not self.traversable:
            raise RulesError('Map requires unique explicit traversable tiles')
        if not set(map(tuple,self.blocked)).issubset(set(map(tuple,self.traversable))) or not set(map(tuple,self.difficult)).issubset(set(map(tuple,self.traversable))):
            raise RulesError('Terrain outside verified map')
        positions=[]
        for p in self.combatants:
            p.validate()
            if (p.x,p.y) not in self.traversable or (p.x,p.y) in self.blocked:
                raise RulesError('Actor positioned off-map or in obstacle')
            positions.append((p.x,p.y))
        if len(set(positions)) != len(positions):
            raise RulesError('Overlapping combatant squares unsupported')
        if self.status == 'active' and (self.active.hp == 0 or self.active.dead):
            raise RulesError('Active actor cannot be unconscious/dead')
        return True

    def to_dict(self):
        return asdict(self)

    def fingerprint(self):
        return _hash(self.to_dict())


def cell_distance_ft(a: Fighter, b: Fighter) -> int:
    """5ft square grid, diagonals count one square (not optional 5/10 variant)."""
    return 5*max(abs(a.x-b.x),abs(a.y-b.y))


def _replace_actor(b: Battle, actor: Fighter) -> Battle:
    actors=tuple(actor if x.actor_id==actor.actor_id else x for x in b.combatants)
    return replace(b, combatants=actors, version=b.version+1)


def _get_actor(b: Battle, actor_id: str) -> Fighter:
    for x in b.combatants:
        if x.actor_id == actor_id:
            return x
    raise RulesError('Unknown actor')


def _active_actor(b: Battle, actor_id: str) -> Fighter:
    if b.status != 'active' or b.active.actor_id != actor_id:
        raise RulesError('Not this combatant\'s turn')
    return b.active


def _move(b: Battle, *, actor_id: str, path: list[list[int]], threat_checked: bool=False) -> Battle:
    p=_active_actor(b,actor_id)
    if any(q.concentration_save_pending for q in b.combatants):
        raise RulesError('Resolve pending concentration save before moving')
    if threat_checked:
        raise RulesError('Unverified opportunity-attack bypass prohibited; resolve reactions through a separately audited event')
    if not path:
        raise RulesError('Explicit nonempty path required')
    previous=(p.x,p.y)
    cost=0
    occupied={(q.x,q.y) for q in b.combatants if q.actor_id!=actor_id and q.hp>0}
    threats=[q for q in b.combatants if q.actor_id!=actor_id and q.hp>0 and q.team!=p.team]
    for dest in path:
        if not (isinstance(dest,list) and len(dest)==2 and all(type(c) is int for c in dest)):
            raise RulesError('Invalid path coordinate')
        dest=tuple(dest)
        if dest not in b.traversable or dest in b.blocked or dest in occupied:
            raise RulesError('Movement obstructed or map position unknown')
        dx,dy=abs(dest[0]-previous[0]),abs(dest[1]-previous[1])
        if max(dx,dy)!=1:
            raise RulesError('Path requires adjacent tiles; no teleporting')
        # No diagonal corner clipping between two solid barriers.
        if dx and dy and ( (dest[0],previous[1]) in b.blocked and (previous[0],dest[1]) in b.blocked):
            raise RulesError('Diagonal cannot clip through blocked corner')
        if threat_checked:
            raise RulesError('Boolean opportunity-attack bypass is prohibited; resolve reactions explicitly or Disengage')
        if not p.disengaged:
            for q in threats:
                start=5*max(abs(previous[0]-q.x),abs(previous[1]-q.y))
                end=5*max(abs(dest[0]-q.x),abs(dest[1]-q.y))
                if start <= q.reach_ft and end > q.reach_ft and q.reaction:
                    raise RulesError('Potential opportunity attack: resolve reaction before movement')
        cost+=10 if dest in b.difficult else 5
        previous=dest
    if cost>p.move_left:
        raise RulesError('Insufficient movement')
    return _replace_actor(b,replace(p,x=previous[0],y=previous[1],move_left=p.move_left-cost))


def _spend(b: Battle, *,actor_id:str, resource:str, label:str='') -> Battle:
    p=_active_actor(b,actor_id)
    if any(q.concentration_save_pending for q in b.combatants):
        raise RulesError('Resolve pending concentration save before spending actions')
    fields={'action':'action','bonus':'bonus','reaction':'reaction'}
    if resource not in fields or not label or not label.strip():
        raise RulesError('Unknown spend or unspecified intent')
    if not getattr(p,resource):
        raise RulesError('Action economy exceeded')
    # Unimplemented mechanically significant actions must not be silently treated as resolved.
    unresolved={'dodge','dash','help','ready','hide','grapple','shove',
                'cast a spell','use an object','search','influence'}
    if label.strip().lower() in unresolved:
        raise RulesError('Declared action has unsupported consequences; use verified dedicated mechanic')
    # Pure bookkeeping only; non-attack consequences are NOT adjudicated here.
    return _replace_actor(b,replace(p,**{resource:False}))


def _disengage(b:Battle, *,actor_id:str) -> Battle:
    """Spend the Action to Disengage. No extra action or unverified features."""
    p=_active_actor(b,actor_id)
    if any(q.concentration_save_pending for q in b.combatants):
        raise RulesError('Resolve concentration before Disengage')
    if not p.action:raise RulesError('No action available to Disengage')
    return _replace_actor(b,replace(p,action=False,disengaged=True))


def _as_roll(data:Roll|dict|None) -> Roll|None:
    if data is None or isinstance(data,Roll):
        return data
    if isinstance(data,dict):
        d=dict(data)
        for field in ("all_faces", "kept_faces", "discarded_faces"):
            d[field]=tuple(d[field])
        return Roll(**d)
    raise RulesError("Invalid dice audit payload")


def _attack(b:Battle, *,actor_id:str,target_id:str,attack:Roll|dict,damage:Roll|dict|None,
            visibility_source:str='',cover_bonus:int=0,cover_source:str='',
            attack_style:str='melee',reaction_window:str='',advantage_source:str='') -> Battle:
    a=_active_actor(b,actor_id)
    attack=_as_roll(attack)
    damage=_as_roll(damage)
    t=_get_actor(b,target_id)
    if not a.action:
        raise RulesError('Attack action already spent')
    if a.hp==0 or t.hp==0 or t.dead:
        raise RulesError('Unsupported attack on unconscious/dead target; adjudicate separately')
    if any(c.concentration_save_pending for c in b.combatants):
        raise RulesError('Pending concentration save must resolve before another attack')
    if not visibility_source or reaction_window!='confirmed':
        raise RulesError('Unverified visibility or unresolved reaction/Shield window')
    if cover_bonus not in (0,2,5) or (cover_bonus!=0 and not cover_source):
        raise RulesError('Cover must be expressly adjudicated')
    if attack_style not in ('melee','ranged'):
        raise RulesError('Attack style must be explicit')
    distance=cell_distance_ft(a,t)
    if attack_style=='melee' and distance>a.reach_ft:
        raise RulesError('Melee reach exceeded')
    if attack_style=='ranged' and distance>a.attack_range_ft:
        raise RulesError('Ranged reach exceeded or long-range rules unsupported')
    validate_roll(attack)
    if attack.mode != 'normal' and not advantage_source.strip():
        raise RulesError('Advantage/disadvantage requires verified cause')
    if attack.count!=1 or attack.sides!=20 or attack.modifier!=a.attack_bonus:
        raise RulesError('Attack roll modifier/shape mismatch with sheet')
    if attack_style=='ranged' and any(q.team!=a.team and q.hp>0 and cell_distance_ft(a,q)<=5 for q in b.combatants if q.actor_id!=a.actor_id):
        if attack.mode!='disadvantage':
            raise RulesError('Ranged attack adjacent to hostile creature requires disadvantage unless feature verified')
    result=d20_result(attack,t.ac+cover_bonus,'attack',b.edition)
    if not result['success'] and damage is not None:
        raise RulesError('Miss must not resolve a damage roll')
    if result['success']:
        if damage is None:
            raise RulesError('Hit requires independently executed damage dice')
        validate_roll(damage)
        expected=critical_damage_expression(a.damage_expression) if result['critical_hit'] else a.damage_expression
        if damage.expression!=expected.replace(' ','').lower() or damage.mode!='normal':
            raise RulesError('Damage expression/critical dice do not match stat block')
        points=max(0,damage.total)
        blocked=min(t.temp_hp,points)
        hp_loss=points-blocked
        nhp=max(0,t.hp-hp_loss)
        pending=0
        if t.concentration and points>0 and nhp>0:
            pending=points # total damage, including damage absorbed by temp HP
        new_t=replace(t,hp=nhp,temp_hp=t.temp_hp-blocked,
                      concentration='' if nhp==0 else t.concentration,
                      concentration_save_pending=pending)
        actors=tuple(replace(a,action=False) if x.actor_id==a.actor_id else new_t if x.actor_id==t.actor_id else x for x in b.combatants)
        return replace(b,combatants=actors,version=b.version+1)
    return _replace_actor(b,replace(a,action=False))


def _end_turn(b:Battle, *,actor_id:str) -> Battle:
    _active_actor(b,actor_id)
    if any(c.concentration_save_pending for c in b.combatants):
        raise RulesError('Pending concentration resolution')
    remaining={x.team for x in b.combatants if x.hp>0 and not x.dead}
    if len(remaining)<2:
        return replace(b,status='complete',version=b.version+1)
    idx=b.active_index
    round_no=b.round_no
    for _ in range(len(b.combatants)):
        idx=(idx+1)%len(b.combatants)
        if idx==0:
            round_no+=1
        nxt=b.combatants[idx]
        if nxt.hp>0 and not nxt.dead:
            refreshed=replace(nxt,action=True,bonus=True,reaction=True,
                              move_left=nxt.speed_ft,disengaged=False)
            others=tuple(refreshed if c.actor_id==nxt.actor_id else c for c in b.combatants)
            return replace(b,combatants=others,active_index=idx,round_no=round_no,version=b.version+1)
    raise RulesError('Cannot advance combat: no capable combatants')


def _concentration(b: Battle, *, actor_id:str, save:Roll|dict) -> Battle:
    from engine_core import concentration_dc
    target=_get_actor(b,actor_id)
    if target.concentration_save_pending<=0 or not target.concentration:
        raise RulesError('No pending concentration saving throw')
    save=_as_roll(save)
    validate_roll(save)
    if save.count!=1 or save.sides!=20 or save.modifier!=target.con_save_bonus:
        raise RulesError('Concentration saving throw modifier mismatch')
    dc=concentration_dc(target.concentration_save_pending,b.edition)
    success=d20_result(save,dc,'saving_throw',b.edition)['success']
    next_target=replace(target,concentration_save_pending=0,
                        concentration=target.concentration if success else '')
    return _replace_actor(b,next_target)


def transition(b:Battle, operation:str, params:dict) -> Battle:
    b.validate()
    if operation=='concentration':
        out=_concentration(b,**params)
    elif operation=='move':
        out=_move(b,**params)
    elif operation=='attack':
        out=_attack(b,**params)
    elif operation=='spend':
        out=_spend(b,**params)
    elif operation=='disengage':
        out=_disengage(b,**params)
    elif operation=='end_turn':
        out=_end_turn(b,**params)
    else:
        raise RulesError('Unsupported operation; no fictional success')
    out.validate()
    return out


@dataclass(frozen=True)
class VerifiedEvent:
    event_key:str
    expected_version:int
    operation:str
    params:dict
    prior_hash:str
    after_hash:str
    committed_version:int
    event_hash:str

    def to_dict(self):
        return asdict(self)


def apply_verified(b:Battle, prior:tuple[VerifiedEvent,...], *,event_key:str, expected_version:int,
                   operation:str, params:dict) -> tuple[Battle, tuple[VerifiedEvent,...], str]:
    """Pure local event/replay guard, not an Airtable atomic commit."""
    if not event_key:
        raise RulesError('Idempotency key is mandatory')
    if any(e.event_key==event_key for e in prior):
        item=next(e for e in prior if e.event_key==event_key)
        if item.operation!=operation or item.params!=_canonical(params) or item.expected_version!=expected_version:
            raise RulesError('Idempotency key collision or divergent retry')
        return b,prior,'REPLAY_NO_WRITE'
    if expected_version!=b.version:
        raise RulesError('Version conflict; stop and reload Airtable')
    params=_canonical(params)
    after=transition(b,operation,params)
    data={"event_key":event_key,"expected_version":expected_version,"operation":operation,
          "params":params,"prior_hash":b.fingerprint(),"after_hash":after.fingerprint(),
          "committed_version":after.version}
    e=VerifiedEvent(**data,event_hash=_hash(data))
    return after,prior+(e,), 'PROPOSED_LOCAL_ONLY'


def replay(initial:Battle, events:tuple[VerifiedEvent,...]) -> Battle:
    """Independently replay ledger; any divergence stops recovery."""
    state=initial
    seen=set()
    for e in events:
        payload=e.to_dict()
        actual=payload.pop('event_hash')
        if actual!=_hash(payload) or e.event_key in seen:
            raise RulesError('Event tampered or duplicate key')
        if state.version!=e.expected_version or state.fingerprint()!=e.prior_hash:
            raise RulesError('Ledger gap or wrong checkpoint')
        state=transition(state,e.operation,e.params)
        if state.version!=e.committed_version or state.fingerprint()!=e.after_hash:
            raise RulesError('Post-state diverges from event')
        seen.add(e.event_key)
    return state


def battle_from_dict(data:dict) -> Battle:
    """Parse JSON checkpoint without using chat memory as the authority."""
    p=dict(data)
    p['combatants']=tuple(Fighter(**x) for x in p['combatants'])
    for key in ('traversable','blocked','difficult'):
        p[key]=tuple(tuple(v) for v in p.get(key,()))
    b=Battle(**p)
    b.validate()
    return b


def event_from_dict(data:dict) -> VerifiedEvent:
    """Load a persisted event without treating absent audit values as defaults."""
    event=VerifiedEvent(**data)
    p=event.to_dict()
    digest=p.pop('event_hash')
    if digest!=_hash(p):
        raise RulesError('Event integrity digest mismatch')
    return event
