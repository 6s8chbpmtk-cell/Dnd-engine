"""Conservative Airtable persistence pilot for a verified tactical Battle.

This is an intentionally NON-ATOMIC, sequential operator bridge. Airtable does
not support compare-and-swap across records; another concurrent writer can race
between the preflight reads and writes. A failed/ambiguous write halts gameplay
until a human/operator reconciles it. Never treat a response timeout as failure.

The application must explicitly authorize the player's declared move; this
module cannot infer consent from a chat transcript. NPC actions are GM-controlled.
The Airtable Campaign and Encounter state versions must be synchronized before
starting the pilot. Do not point this at existing live campaigns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
from typing import Protocol, Any
import json
import os
import urllib.parse
import urllib.request

from combat_runtime import Battle, VerifiedEvent, apply_verified, battle_from_dict, event_from_dict
from engine_core import RulesError, Roll, SOURCE, validate_roll


class PersistenceError(RulesError):
    pass


class WriteUncertain(PersistenceError):
    """Mutation may already have reached Airtable. STOP; inspect records."""


class AirtablePort(Protocol):
    def get(self, table: str, record_id: str) -> dict: ...
    def list(self, table: str) -> list[dict]: ...
    def create(self, table: str, fields: dict) -> dict: ...
    def update(self, table: str, record_id: str, fields: dict) -> dict: ...


class AirtableRESTPort:
    """Optional local server/CLI adapter; credentials ONLY from environment.

    ChatGPT's separate Airtable connector can follow the same plan. The plain
    Python package never includes a token, and no live connection is tested here.
    """
    def __init__(self, base_id: str, token: str, timeout: float = 15):
        if not base_id.startswith('app') or not token:
            raise PersistenceError('Airtable base ID and token required')
        self.base_id=base_id
        self.token=token
        self.timeout=timeout

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('AIRTABLE_BASE_ID',''),os.environ.get('AIRTABLE_TOKEN',''))

    def _send(self, method: str, table: str, record_id: str='', data: dict|None=None, query: dict|None=None):
        endpoint=f'https://api.airtable.com/v0/{urllib.parse.quote(self.base_id)}/{urllib.parse.quote(table)}'
        if record_id:
            endpoint+='/'+urllib.parse.quote(record_id)
        if query:
            endpoint+='?'+urllib.parse.urlencode(query)
        body=json.dumps(data,allow_nan=False).encode('utf-8') if data is not None else None
        req=urllib.request.Request(endpoint, data=body,method=method,headers={
            'Authorization':'Bearer '+self.token,'Content-Type':'application/json'
        })
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            return json.loads(response.read().decode('utf-8'))

    def get(self, table: str, record_id: str) -> dict:
        return self._send('GET',table,record_id)

    def list(self, table: str) -> list[dict]:
        result=[]
        offset=None
        for _ in range(30):
            params={'pageSize':100}
            if offset:params['offset']=offset
            page=self._send('GET',table,query=params)
            result+=page['records']
            offset=page.get('offset')
            if not offset:return result
        raise PersistenceError('Pagination exceeded bound; fail closed')

    def create(self, table: str, fields: dict) -> dict:
        data=self._send('POST',table,data={'fields':fields,'typecast':False})
        if 'id' not in data:raise PersistenceError('Missing Airtable created-record ID')
        return data

    def update(self, table: str, record_id: str, fields: dict) -> dict:
        return self._send('PATCH',table,record_id,data={'fields':fields,'typecast':False})


def _digest(value: Any) -> str:
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def encode_checkpoint(battle: Battle) -> str:
    battle.validate()
    return json.dumps({'schema':'battle-v3','battle':battle.to_dict(),
                       'fingerprint':battle.fingerprint()},sort_keys=True,separators=(',',':'))


def decode_checkpoint(serialized: str) -> Battle:
    try:
        data=json.loads(serialized)
        if set(data)!= {'schema','battle','fingerprint'} or data['schema']!='battle-v3':
            raise PersistenceError('Unknown/extra checkpoint schema')
        b=battle_from_dict(data['battle'])
        if b.fingerprint()!=data['fingerprint']:
            raise PersistenceError('Checkpoint hash does not match battle state')
        return b
    except (TypeError,ValueError,KeyError,AttributeError) as exc:
        if isinstance(exc,PersistenceError):raise
        raise PersistenceError('Checkpoint malformed') from exc


def _canonical_params(params: dict) -> dict:
    def convert(v):
        if is_dataclass(v):return convert(asdict(v))
        if isinstance(v,dict):return {str(k):convert(x) for k,x in v.items()}
        if isinstance(v,(tuple,list)):return [convert(x) for x in v]
        return v
    obj=convert(params)
    # Must be valid canonical JSON; reject arbitrary Python instances.
    json.dumps(obj,sort_keys=True,allow_nan=False)
    return obj


def _stored_event(record:dict)->VerifiedEvent:
    try:
        fields=record['fields']
        ev=event_from_dict(json.loads(fields['State Changes Before → After']))
        if (fields.get('Mutation Fingerprint')!=ev.event_hash or
            fields.get('State Version Before')!=ev.expected_version or
            fields.get('State Version After')!=ev.committed_version or
            fields.get('Idempotency Key')!=ev.event_key):
            raise PersistenceError('Event ledger metadata/hash inconsistent')
        return ev
    except (KeyError,TypeError,ValueError) as exc:
        if isinstance(exc,PersistenceError):raise
        raise PersistenceError('Malformed historical event; stop replay') from exc


def _require_live_rolls(params: dict):
    """Block deliberately injected unit-test dice from persisted game outcomes.

    Digests only check internal consistency. This is not independent proof that
    a remote actor used the OS randomness source claimed in the payload.
    """
    for name in ('attack','damage','save'):
        item=params.get(name)
        if item is None:continue
        if isinstance(item,dict):
            try:
                payload={**item}
                for key in ('all_faces','kept_faces','discarded_faces'):
                    payload[key]=tuple(payload[key])
                item=Roll(**payload)
            except (TypeError,KeyError) as exc:
                raise PersistenceError('Bad roll payload') from exc
        if not isinstance(item,Roll):raise PersistenceError('Unsupported dice payload')
        validate_roll(item)
        if item.entropy_source!=SOURCE:
            raise PersistenceError('Test-injected or unrecognized die source prohibited for live commit')


def roll_to_audit_fields(roll_obj:Roll, *,campaign_id:str,event_key:str,
                         ledger_record_id:str,target:int|None=None) -> dict:
    """Project a real generated roll into the Airtable Dice Audit v2 schema.

    A digest is a consistency check, NOT proof of fairness. The actual source
    of randomness remains the tool-executed Python roll function.
    """
    validate_roll(roll_obj)
    if roll_obj.entropy_source!=SOURCE:
        raise PersistenceError('Cannot mark an injected/test roll as OS entropy')
    data={
        'Roll ID':roll_obj.roll_id,'Campaign ID':campaign_id,'Event ID / Key':event_key,
        'Dice Expression':roll_obj.expression,'Number of Dice':roll_obj.count,
        'Faces per Die':roll_obj.sides,'Raw Face Results':json.dumps(roll_obj.all_faces),
        'Modifier Total':roll_obj.modifier,'Recorded Total':roll_obj.total,
        'Source':'OS CSPRNG (tool executed)',
        'Source / Execution Evidence':roll_obj.entropy_source+'; roll-id='+roll_obj.roll_id,
        'Status':'Generated','Raw Dice Sum':roll_obj.raw_sum,
        'Roll Mode':roll_obj.mode.capitalize(),
        'Kept Face Results':json.dumps(roll_obj.kept_faces),
        'Discarded Face Results':json.dumps(roll_obj.discarded_faces),
        'Roll Timestamp':roll_obj.created_utc,
        'Integrity Digest (not proof of entropy)':roll_obj.digest,
        'Source Event Ledger Link':[ledger_record_id]
    }
    if target is not None:
        if type(target) is not int:raise PersistenceError('Invalid numeric target')
        data['Target DC or AC']=target
    return data


def actor_projection(battle:Battle, actor_id:str) -> dict:
    """Exact Combatant Turns v2 values for one active encounter participant."""
    battle.validate()
    actors={a.actor_id:a for a in battle.combatants}
    if actor_id not in actors:raise PersistenceError('Unknown actor for Airtable projection')
    a=actors[actor_id]
    status=('Completed' if battle.status=='complete' else 'Dead' if a.dead else
            'Down' if a.hp==0 else 'Active' if battle.active.actor_id==actor_id else 'Ready')
    return {
        'Combatant Key':f'{battle.encounter_id}:{actor_id}',
        'Encounter ID':battle.encounter_id,
        'Character or NPC ID':actor_id,
        'Controller':'Player' if a.controller=='player' else 'GM',
        'Initiative Result':a.initiative,
        'Round Number':battle.round_no,
        'Current HP':a.hp,'Maximum HP':a.max_hp,'Temporary HP':a.temp_hp,
        'Movement Remaining ft':a.move_left,
        'Action Available':'Yes' if a.action else 'No',
        'Bonus Action Available':'Yes' if a.bonus else 'No',
        'Reaction Available':'Yes' if a.reaction else 'No',
        'Concentration / Conditions':a.concentration,
        'Death Save Successes':a.death_successes,'Death Save Failures':a.death_failures,
        'Map X':a.x,'Map Y':a.y,
        'State Version':battle.version,'Turn Status':status
    }


@dataclass(frozen=True)
class BattleAnchor:
    campaign_record_id: str
    encounter_record_id: str
    campaign_id: str
    expected_rules: str  # locked to 2014 or 2024


@dataclass(frozen=True)
class CommitResult:
    result: str
    event_key: str
    version: int
    event_record_id: str|None = None


class AirtableBattlePilot:
    """One encounter, one sequential writer, application-level reconciliation."""
    def __init__(self, port:AirtablePort, anchor:BattleAnchor, *,
                 actor_rows:dict[str,str]|None=None):
        self.port=port
        self.anchor=anchor
        self.actor_rows=actor_rows
        if anchor.expected_rules not in ('2014','2024'):
            raise PersistenceError('Pilot requires fixed 5e edition')

    def _verify_actor_rows(self,battle:Battle) -> None:
        if self.actor_rows is None:return
        if set(self.actor_rows)!={a.actor_id for a in battle.combatants}:
            raise PersistenceError('Combatant projection mapping incomplete/extra')
        for actor_id,row_id in self.actor_rows.items():
            observed=self.port.get('Combatant Turns v2',row_id).get('fields',{})
            expected=actor_projection(battle,actor_id)
            if any(observed.get(key,'' if value=='' else None)!=value for key,value in expected.items()):
                raise PersistenceError(f'Combatant projection diverged for {actor_id}')

    def _ledger_events(self,event_key:str) -> list[dict]:
        return [r for r in self.port.list('Event Ledger')
                if r.get('fields',{}).get('Idempotency Key')==event_key and
                r.get('fields',{}).get('Campaign ID')==self.anchor.campaign_id]

    def read(self, *, allow_pending:bool=False) -> Battle:
        if not allow_pending:
            unresolved=[r for r in self.port.list('Event Ledger')
                        if r.get('fields',{}).get('Campaign ID')==self.anchor.campaign_id
                        and r.get('fields',{}).get('Commit Status') in
                           ('Proposed','Conflict - Needs Reconciliation')]
            if unresolved:
                raise PersistenceError('Unresolved ledger event: recovery required before resuming')
        c=self.port.get('Campaigns',self.anchor.campaign_record_id).get('fields',{})
        e=self.port.get('Encounters',self.anchor.encounter_record_id).get('fields',{})
        if c.get('Campaign ID')!=self.anchor.campaign_id or e.get('Campaign ID')!=self.anchor.campaign_id:
            raise PersistenceError('Cross-campaign record link; refuse to proceed')
        if c.get('Game Mode')!='D&D 5e':
            raise PersistenceError('Campaign is not configured for D&D 5e')
        profile={'2014':'D&D 5e 2014 / SRD 5.1','2024':'D&D 5e 2024 / SRD 5.2.1'}[self.anchor.expected_rules]
        if c.get('Rules Profile')!=profile:
            raise PersistenceError('Rules profile mismatch')
        state=decode_checkpoint(e.get('Combatant State Snapshot',''))
        if state.encounter_id!=e.get('Encounter ID') or state.edition!=self.anchor.expected_rules:
            raise PersistenceError('Encounter identity/edition mismatch')
        if e.get('Current State Version')!=state.version or c.get('State Version')!=state.version:
            raise PersistenceError('Campaign/encounter versions disagree; reconcile, do not guess')
        if e.get('Status') not in ('Active','Prepared','Paused'):
            raise PersistenceError('Encounter status not suitable for pilot')
        self._verify_actor_rows(state)
        return state

    def commit(self,*,event_key:str,expected_version:int,operation:str,params:dict,
               player_intent:str='') -> CommitResult:
        """Stages Proposed ledger -> encounter snapshot -> campaign version -> Verified.

        Any exception after the first write is ambiguous, including a response
        timeout. The operator must run inspect() and reconcile; never retry as
        though it were definitely rolled back.
        """
        if not event_key or not event_key.startswith(self.anchor.campaign_id+':'):
            raise PersistenceError('Namespaced event key required')
        if not isinstance(params,dict):raise PersistenceError('Operation params must be a dict')
        records=[r for r in self.port.list('Event Ledger')
                 if r.get('fields',{}).get('Campaign ID')==self.anchor.campaign_id]
        existing=[r for r in records if r.get('fields',{}).get('Idempotency Key')==event_key]
        if len(existing)>1:
            raise PersistenceError('Duplicate idempotency keys: halt')
        if existing:
            r=existing[0]
            status=r.get('fields',{}).get('Commit Status')
            if status!='Verified':
                raise PersistenceError('Pending/conflicted key: reconcile, not retry')
            ev=_stored_event(r)
            if (ev.operation!=operation or
                ev.expected_version!=expected_version or
                ev.params!=_canonical_params(params)):
                raise PersistenceError('Same key used for a different action; halt')
            state=self.read()
            if (state.version!=ev.committed_version or
                state.fingerprint()!=ev.after_hash):
                raise PersistenceError('Ledger version/hash does not match state')
            return CommitResult('REPLAY_NO_WRITE',event_key,state.version,r.get('id'))
        unresolved=[r for r in records if r.get('fields',{}).get('Commit Status') in
                    ('Proposed','Conflict - Needs Reconciliation')]
        if unresolved:
            raise PersistenceError('Other unresolved events in campaign: halt and reconcile')
        state=self.read()
        if state.version!=expected_version:
            raise PersistenceError('Version conflict; reload state')
        if state.status!='active':
            raise PersistenceError('Cannot mutate complete battle')
        if operation not in ('concentration','move','attack','spend','disengage','end_turn'):
            raise PersistenceError('Operation unsupported')
        if operation != 'concentration':
            actor=state.active
        else:
            actor=next((c for c in state.combatants if c.actor_id==params.get('actor_id')),None)
            if actor is None:raise PersistenceError('Unknown concentration actor')
        if actor.controller=='player' and not player_intent.strip():
            raise PersistenceError('Player action requires explicit declared player intent')
        _require_live_rolls(params)
        # The event may embed sensitive actor details in the private Airtable
        # record; this package never sends such payloads to public GitHub.
        advanced,evs,_=apply_verified(state,(),event_key=event_key,
                    expected_version=expected_version,operation=operation,params=params)
        event=evs[0]
        if len(encode_checkpoint(advanced))>80000:
            raise PersistenceError('Encounter snapshot too large for safe Airtable field')
        entry={
            'Event Name':f'Engine pilot: {operation}',
            'Event ID':event_key,
            'Campaign ID':self.anchor.campaign_id,
            'Idempotency Key':event_key,
            'Mutation Fingerprint':event.event_hash,
            'Event Type':'Action Resolution',
            'Intent / Action':player_intent if actor.controller=='player' else f'GM NPC operation: {operation}',
            'State Changes Before → After':json.dumps(event.to_dict(),sort_keys=True),
            'State Version Before':state.version,
            'State Version After':advanced.version,
            'Commit Status':'Proposed'
        }
        began=False
        eid=None
        try:
            began=True  # even a failed network request may have reached Airtable
            record=self.port.create('Event Ledger',entry)
            eid=record['id']
            audit_expectations=[]
            for k in ('attack','damage','save'):
                raw=params.get(k)
                if raw is None:continue
                parsed=raw
                if isinstance(parsed,dict):
                    parsed=Roll(**{**parsed,**{f:tuple(parsed[f]) for f in
                                  ('all_faces','kept_faces','discarded_faces')}})
                projected=roll_to_audit_fields(parsed,campaign_id=self.anchor.campaign_id,
                                                event_key=event_key,ledger_record_id=eid)
                rr=self.port.create('Dice Audit v2',projected)
                audit_expectations.append((rr['id'],projected))
            self.port.update('Encounters',self.anchor.encounter_record_id,{
                'Combatant State Snapshot':encode_checkpoint(advanced),
                'Current State Version':advanced.version,
                'Round Number':advanced.round_no,
                'Active Combatant ID':advanced.active.actor_id if advanced.status=='active' else '',
                'Status':'Resolved' if advanced.status=='complete' else 'Active'
            })
            self.port.update('Campaigns',self.anchor.campaign_record_id,{
                'State Version':advanced.version
            })
            if self.actor_rows is not None:
                for actor in advanced.combatants:
                    self.port.update('Combatant Turns v2',self.actor_rows[actor.actor_id],
                                     actor_projection(advanced,actor.actor_id))
            # Read-back must establish exactly one event and matching state.
            after=self.read(allow_pending=True) if advanced.status=='active' else self._read_completed()
            observed=self._ledger_events(event_key)
            if after.fingerprint()!=advanced.fingerprint() or len(observed)!=1 or observed[0]['id']!=eid:
                raise PersistenceError('Post-write verification failed')
            for roll_record_id,projected in audit_expectations:
                fetched=self.port.get('Dice Audit v2',roll_record_id).get('fields',{})
                if any(fetched.get(k)!=v for k,v in projected.items()):
                    raise PersistenceError('Dice audit read-back mismatch')
                self.port.update('Dice Audit v2',roll_record_id,{'Status':'Reconciled with Event'})
            self.port.update('Event Ledger',eid,{'Commit Status':'Verified'})
            # The final status-update acknowledgment itself may be lost.
            final=self._ledger_events(event_key)
            if len(final)!=1 or final[0]['id']!=eid or final[0].get('fields',{}).get('Commit Status')!='Verified':
                raise PersistenceError('Final event status read-back failed')
            return CommitResult('VERIFIED',event_key,advanced.version,eid)
        except Exception as exc:
            if began:
                raise WriteUncertain(f'Write or acknowledgement uncertain for key={event_key}; STOP and inspect records') from exc
            raise

    def _read_completed(self)->Battle:
        # read() deliberately disallows resolved encounters for future moves;
        # final snapshot must still be independently verified.
        c=self.port.get('Campaigns',self.anchor.campaign_record_id)['fields']
        e=self.port.get('Encounters',self.anchor.encounter_record_id)['fields']
        b=decode_checkpoint(e.get('Combatant State Snapshot',''))
        if (c.get('Campaign ID')!=self.anchor.campaign_id or e.get('Campaign ID')!=self.anchor.campaign_id or
            c.get('Rules Profile')!={'2014':'D&D 5e 2014 / SRD 5.1','2024':'D&D 5e 2024 / SRD 5.2.1'}[self.anchor.expected_rules] or
            b.edition!=self.anchor.expected_rules or b.encounter_id!=e.get('Encounter ID')):
            raise PersistenceError('Final encounter campaign/edition identity inconsistent')
        if b.status!='complete' or e.get('Status')!='Resolved' or b.version!=c.get('State Version') or b.version!=e.get('Current State Version'):
            raise PersistenceError('Completion read-back inconsistent')
        self._verify_actor_rows(b)
        return b

    def inspect(self,event_key:str)->dict:
        """Read-only operator diagnosis following an ambiguous write."""
        try:
            campaign=self.port.get('Campaigns',self.anchor.campaign_record_id).get('fields',{})
            encounter=self.port.get('Encounters',self.anchor.encounter_record_id).get('fields',{})
            events=self._ledger_events(event_key)
        except Exception as exc:
            return {'status':'UNAVAILABLE_STOP','detail':type(exc).__name__}
        event_status=[r.get('fields',{}).get('Commit Status') for r in events]
        try:
            b=decode_checkpoint(encounter.get('Combatant State Snapshot',''))
            fingerprint=b.fingerprint()
            v=b.version
        except Exception:
            fingerprint=None
            v=None
        cv=campaign.get('State Version')
        ev=encounter.get('Current State Version')
        if len(events)>1:
            status='DUPLICATE_EVENTS_STOP'
        elif not events and cv==ev==v:
            status='NO_EVENT_CHECK_EXPECTED_CHECKPOINT_MANUALLY'
        elif len(events)==1 and event_status[0] in ('Verified','Committed') and cv==ev==v==events[0]['fields'].get('State Version After'):
            status='RECORDED_VERIFY_FINGERPRINT_MANUALLY'
        else:
            status='PARTIAL_OR_UNCERTAIN_STOP'
        return {'status':status,'campaign_version':cv,'encounter_version':ev,
                'snapshot_version':v,'snapshot_fingerprint':fingerprint,
                'event_count':len(events),'ledger_statuses':event_status}
