"""Read-only operator interface tests; synthetic fixtures."""
import json
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from combat_runtime import apply_verified
from airtable_persistence import encode_checkpoint
from test_airtable_persistence import initial

SCRIPT=Path(__file__).resolve().parent/'scripts/replay_checkpoint.py'


class ReplayCliTests(unittest.TestCase):
    def invoke(self,checkpoint,events=None):
        with tempfile.TemporaryDirectory() as folder:
            cp=Path(folder)/'snapshot.json'
            cp.write_text(json.dumps(checkpoint),encoding='utf-8')
            args=[sys.executable,str(SCRIPT),str(cp)]
            if events is not None:
                ev=Path(folder)/'events.json';ev.write_text(json.dumps(events),encoding='utf-8')
                args+=['--events',str(ev)]
            return subprocess.run(args,capture_output=True,text=True)
    def test_read_only_checkpoint(self):
        c=json.loads(encode_checkpoint(initial()))
        out=self.invoke(c)
        self.assertEqual(out.returncode,0,out.stderr)
        data=json.loads(out.stdout)
        self.assertEqual((data['version'],data['active_actor'],data['combatants'][0]['hp']),(0,'p1',12))
    def test_replay_checkpoint_from_ledger(self):
        b=initial();n,ev,_=apply_verified(b,(),event_key='qa:1',expected_version=0,
                   operation='end_turn',params={'actor_id':'p1'})
        out=self.invoke(json.loads(encode_checkpoint(b)),[asdict(x) for x in ev])
        self.assertEqual(out.returncode,0,out.stderr)
        data=json.loads(out.stdout)
        self.assertEqual((data['version'],data['active_actor'],data['replayed_events']),(1,'b1',1))
    def test_reject_modified_snapshot(self):
        c=json.loads(encode_checkpoint(initial()));c['battle']['combatants'][0]['hp']=999
        out=self.invoke(c);self.assertEqual(out.returncode,2)
        self.assertIn('error',json.loads(out.stderr))
    def test_reject_invalid_ledger(self):
        out=self.invoke(json.loads(encode_checkpoint(initial())),{'not':'a list'})
        self.assertEqual(out.returncode,2)
    def test_reject_event_forgery(self):
        b=initial();n,ev,_=apply_verified(b,(),event_key='qa:1',expected_version=0,
                   operation='end_turn',params={'actor_id':'p1'})
        rows=[asdict(x) for x in ev];rows[0]['operation']='move'
        out=self.invoke(json.loads(encode_checkpoint(b)),rows)
        self.assertEqual(out.returncode,2)
    def test_reject_unknown_checkpoint_schema(self):
        c=json.loads(encode_checkpoint(initial()));c['schema']='anything'
        out=self.invoke(c)
        self.assertEqual(out.returncode,2)

if __name__=='__main__':unittest.main()
