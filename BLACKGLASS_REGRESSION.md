# Blackglass failure reproduced — synthetic QA only

The defect motivating v3.4 was a three-record ledger produced by a ChatGPT
Airtable tool workflow. The narration and latest checkpoint were plausible,
but the rows were not canonical engine events and six mechanical operations
were collapsed into three version increments. Readback consistency alone
could not prove correct historical transition application.

The new `test_write_guard.py` re-creates this defect using FakePort instead of
writing any real Airtable records. It verifies:

- a prose-only `State Changes Before → After` is rejected *before* any write;
- even JSON with a non-engine composite operation is rejected;
- missing, duplicate, Proposed or tampered ledger entries block gameplay;
- lost/extra linked dice audits block gameplay;
- legitimate 12-event history can be recovered and extended to a valid 13th;
- a six-operation tactical round trip saves exactly six replayable events;
- version-zero genesis stored in `State Reconciliation Notes` under
  `:session01:genesis` is accepted when independently Verified;
- agent-controlled player mutations without recorded intent are refused;
- stale requests are refused, and ambiguous post-write verification halts
  without a blind retry.

**The original Blackglass records remain untouched.** Its current v3 saved
checkpoint is useful as a human-readable legacy snapshot, but the campaign's
strict event chain must be designated `UNREPLAYABLE` until deliberately repaired
from independently verified original inputs or the test is reset to clean
version-zero state. This package supplies no unsafe automatic repair.
