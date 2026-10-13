#!/usr/bin/env python3
"""Verify a ChatGPT Airtable connector snapshot; never write to Airtable.

Input file is a LOCAL data snapshot containing potentially sensitive GM-only
information. Never commit such JSON to a public source repository. The output
contains only the tactical recovery checkpoint, not GM-only scene notes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airtable_persistence import BattleAnchor, PersistenceError
from connector_readonly import recover_from_connector_export


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='Read-only audited Airtable recovery from a connector JSON export')
    parser.add_argument('snapshot',type=Path,help='local field-ID-keyed export JSON; never commit to a public repository')
    parser.add_argument('--campaign-record',required=True,help='Airtable rec... Campaign record ID')
    parser.add_argument('--encounter-record',required=True,help='Airtable rec... Encounter record ID')
    parser.add_argument('--campaign-id',required=True,help='namespaced campaign ID')
    parser.add_argument('--edition',required=True,choices=('2014','2024'))
    args=parser.parse_args(argv)
    if not args.campaign_record.startswith('rec') or not args.encounter_record.startswith('rec'):
        parser.error('Airtable Campaign/Encounter record IDs must begin with rec')
    try:
        export=json.loads(args.snapshot.read_text(encoding='utf-8'))
        anchor=BattleAnchor(args.campaign_record,args.encounter_record,args.campaign_id,args.edition)
        state=recover_from_connector_export(export,anchor)
    except (OSError,json.JSONDecodeError,PersistenceError,KeyError,TypeError,ValueError) as exc:
        error=str(exc)
        reason=('UNREPLAYABLE_EVENT_PAYLOAD' if 'Malformed historical event' in error
                else 'CHECKPOINT_OR_HISTORY_INVALID')
        print(json.dumps({'ready':False,'status':'HALT_UNVERIFIED',
                          'reason_code':reason,'error':error},sort_keys=True),file=sys.stderr)
        return 2
    r=state.history
    print(json.dumps({
        'ready':True,'status':'TACTICAL_HISTORY_VERIFIED','campaign_id':r.campaign_id,'encounter_id':r.encounter_id,
        'edition':r.edition,'version':r.state_version,'round':r.round_no,
        'active_actor':state.battle.active.actor_id if state.battle.status=='active' else None,
        'verified_events':r.checked_events,'audited_rolls':r.checked_rolls,
        'fingerprint':r.checkpoint_fingerprint,'combatant_rows':len(state.actor_row_ids),
        'scope':'TACTICAL ONLY; no complete character/spell/scene preflight verified'
    },sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
