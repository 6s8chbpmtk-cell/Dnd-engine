import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HERE=Path(__file__).resolve().parent
SCRIPT=HERE/'scripts'/'verify_airtable_history.py'

class ReadOnlyHistoryCLITests(unittest.TestCase):
    def test_missing_required_arguments_fails_closed(self):
        result=subprocess.run([sys.executable,str(SCRIPT)],capture_output=True,text=True,timeout=6)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('required',result.stderr.lower())

    def test_no_token_fails_without_network(self):
        env={k:v for k,v in os.environ.items() if k not in ('AIRTABLE_TOKEN','AIRTABLE_BASE_ID')}
        result=subprocess.run([sys.executable,str(SCRIPT),'--campaign-record','rec123',
                    '--encounter-record','rec456','--campaign-id','qa',
                    '--edition','2014','--genesis-file',str(HERE/'fixtures'/'qa_3round_gm.json')],
                    capture_output=True,text=True,timeout=6,env=env)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stderr)['status'],'HALT_UNVERIFIED')

if __name__=='__main__':unittest.main()
