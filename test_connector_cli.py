"""Read-only command-line connector verification smoke tests."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import test_connector_readonly

SCRIPT=Path(__file__).parent/'scripts'/'verify_connector_export.py'

class ConnectorCliTests(unittest.TestCase):
    def test_good_export(self):
        payload,anchor,_=test_connector_readonly.ConnectorReadbackTests().fixture()
        with tempfile.TemporaryDirectory() as dir_name:
            file=Path(dir_name)/'qa_export.json'
            file.write_text(json.dumps(payload),encoding='utf-8')
            args=[sys.executable,str(SCRIPT),str(file),'--campaign-record',anchor.campaign_record_id,
                  '--encounter-record',anchor.encounter_record_id,'--campaign-id',anchor.campaign_id,
                  '--edition','2014']
            cmd=subprocess.run(args,capture_output=True,text=True,check=True)
            result=json.loads(cmd.stdout)
            self.assertEqual((result['ready'],result['version'],result['verified_events'],result['audited_rolls']),
                             (True,12,12,10))
            self.assertEqual(result['combatant_rows'],2)

    def test_missing_actor_row_fails(self):
        payload,anchor,_=test_connector_readonly.ConnectorReadbackTests().fixture()
        payload['pages']['Combatant Turns v2'][0]['records'].pop()
        with tempfile.TemporaryDirectory() as dir_name:
            file=Path(dir_name)/'qa_export.json';file.write_text(json.dumps(payload),encoding='utf-8')
            cmd=subprocess.run([sys.executable,str(SCRIPT),str(file),
                '--campaign-record',anchor.campaign_record_id,'--encounter-record',anchor.encounter_record_id,
                '--campaign-id',anchor.campaign_id,'--edition','2014'],capture_output=True,text=True)
            self.assertEqual(cmd.returncode,2)
            self.assertFalse(json.loads(cmd.stderr)['ready'])
            self.assertEqual(json.loads(cmd.stderr)['status'],'HALT_UNVERIFIED')

    def test_prose_only_legacy_event_identified_by_reason_code(self):
        payload,anchor,_=test_connector_readonly.ConnectorReadbackTests().fixture()
        fid=next(f['id'] for f in payload['schemas']['Event Ledger']
                 if f['name']=='State Changes Before → After')
        payload['pages']['Event Ledger'][0]['records'][0]['cellValuesByFieldId'][fid]=(
            'Player moved and attacked, changing the HP and round')
        with tempfile.TemporaryDirectory() as dir_name:
            file=Path(dir_name)/'broken_legacy_ledger.json'
            file.write_text(json.dumps(payload),encoding='utf-8')
            cmd=subprocess.run([sys.executable,str(SCRIPT),str(file),
                '--campaign-record',anchor.campaign_record_id,
                '--encounter-record',anchor.encounter_record_id,
                '--campaign-id',anchor.campaign_id,
                '--edition','2014'],capture_output=True,text=True)
            self.assertEqual(cmd.returncode,2)
            error=json.loads(cmd.stderr)
            self.assertEqual(error['status'],'HALT_UNVERIFIED')
            self.assertEqual(error['reason_code'],'UNREPLAYABLE_EVENT_PAYLOAD')

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as dir_name:
            file=Path(dir_name)/'broken.json';file.write_text('{broken',encoding='utf-8')
            cmd=subprocess.run([sys.executable,str(SCRIPT),str(file),
                '--campaign-record','rec12345678901234','--encounter-record','rec12345678901235',
                '--campaign-id','qa:test','--edition','2014'],capture_output=True,text=True)
            self.assertEqual(cmd.returncode,2)

if __name__=='__main__':unittest.main()
