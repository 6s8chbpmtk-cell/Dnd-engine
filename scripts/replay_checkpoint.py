"""Inspect a verified v3 snapshot, optionally replay saved ledger events.

Read-only. This script never connects to or mutates Airtable.
Run: python scripts/replay_checkpoint.py checkpoint.json [--events events.json]
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from airtable_persistence import decode_checkpoint
from combat_runtime import replay, event_from_dict
from engine_core import RulesError


def main(argv=None):
    parser=argparse.ArgumentParser(description='Validate a battle-v3 checkpoint and print GM state')
    parser.add_argument('checkpoint',help='JSON with battle, fingerprint, schema=battle-v3')
    parser.add_argument('--events',help='JSON list of events applied after the checkpoint')
    args=parser.parse_args(argv)
    try:
        checkpoint=json.loads(Path(args.checkpoint).read_text(encoding='utf-8'))
        state=decode_checkpoint(json.dumps(checkpoint))
        event_count=0
        if args.events:
            events=json.loads(Path(args.events).read_text(encoding='utf-8'))
            if not isinstance(events,list):raise RulesError('Ledger must be a JSON list')
            event_count=len(events)
            state=replay(state,tuple(event_from_dict(e) for e in events))
        report={
            'encounter_id':state.encounter_id,'edition':state.edition,
            'status':state.status,'round':state.round_no,'version':state.version,
            'active_actor':state.active.actor_id if state.status=='active' else None,
            'fingerprint':state.fingerprint(),'replayed_events':event_count,
            'combatants':[{ 'actor_id':a.actor_id,'controller':a.controller,
                 'team':a.team,'hp':a.hp,'max_hp':a.max_hp,'temp_hp':a.temp_hp,
                 'ac':a.ac,'position':[a.x,a.y],'movement_remaining':a.move_left,
                 'action':a.action,'bonus_action':a.bonus,'reaction':a.reaction,
                 'concentration':a.concentration,'dead':a.dead,'stable':a.stable}
                for a in state.combatants]
        }
        print(json.dumps(report,sort_keys=True))
        return 0
    except (RulesError,OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'error':str(exc)}),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
