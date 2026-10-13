"""Read-only full-history verification against Airtable REST.

This script makes GET requests only. Supply a PAT through environment variable
AIRTABLE_TOKEN and base through AIRTABLE_BASE_ID. Never commit credentials.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from airtable_persistence import AirtableRESTPort, BattleAnchor, PersistenceError
from history_verifier import verify_full_history


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='READ ONLY: replay Airtable ledger from genesis')
    parser.add_argument('--campaign-record',required=True)
    parser.add_argument('--encounter-record',required=True)
    parser.add_argument('--campaign-id',required=True)
    parser.add_argument('--edition',required=True,choices=('2014','2024'))
    parser.add_argument('--genesis-file',required=True,type=Path,
                        help='Local UTF-8 text containing the original battle-v3 checkpoint')
    args=parser.parse_args(argv)
    try:
        initial=args.genesis_file.read_text(encoding='utf-8')
        port=AirtableRESTPort.from_env()
        anchor=BattleAnchor(args.campaign_record,args.encounter_record,
                            args.campaign_id,args.edition)
        battle,report=verify_full_history(port,anchor,initial)
    except (OSError,PersistenceError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'HALT_UNVERIFIED','error':str(exc)}),file=sys.stderr)
        return 2
    print(json.dumps({'status':'VERIFIED_READ_ONLY','report':asdict(report),
                      'battle':battle.to_dict()},sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
