# Upgrade instructions — public v3.2 to complete v3.4 QA source

Public repository: https://github.com/6s8chbpmtk-cell/Dnd-engine

This archive contains **the entire source tree**. You do not need the older
separate v3.3 archive. Retain the original `fixtures/qa_3round_gm.json` and
original `QA_V4_MULTIROUND.md` unmodified. Do NOT upload local connector
exports, personal character information, or Airtable tokens.

## New files to add (upload one at a time)

1. `recovery_bootstrap.py`
2. `connector_readonly.py`
3. `write_guard.py`
4. `test_recovery_bootstrap.py`
5. `test_connector_readonly.py`
6. `test_connector_cli.py`
7. `test_write_guard.py`
8. `scripts/verify_connector_export.py` (upload to `/scripts/`)
9. `RECOVERY_PLAYBOOK.md`
10. `TURN_WRITE_GATE.md`
11. `BLACKGLASS_REGRESSION.md`
12. `UPGRADE_FROM_V3_2.md` (optional in GitHub)

## Existing files to replace with v3.4 copies

1. `history_verifier.py` — safe version-zero active recovery and actor checks
2. `pyproject.toml` — reports v3.4.0 and all runtime modules
3. `OPERATOR_PLAYBOOK.md`
4. `QA_EVIDENCE.md`
5. `README.md`
6. `RELEASE_NOTES.md`
7. `.gitignore` — private export patterns (can edit directly in GitHub)
8. `SHA256SUMS.txt` — **replace last** after all source files

All other v3.2 files (including the GitHub Actions YAML) are unchanged.
Because GitHub executes tests on every commit, add implementation modules
**before** the new tests to avoid transient import errors. Do not overwrite
an older file by deleting it first; upload same name/path in GitHub or edit
that file in place, preserving commit history.

## Final acceptance gates

1. All Python 3.11, 3.12, 3.13 GitHub Actions matrix jobs pass after full upload.
2. Re-check SHA256SUMS.txt against all packaged source files.
3. Re-run the isolated Blackglass-style six-operation regression with six
   separately verified structured events.
4. Do not use v3.4 code as a security boundary until ChatGPT direct Airtable
   write privileges are restricted and a single trusted writer service exists.
5. Do not mark the old Blackglass version-3 ledger as fully replayable or
   silently migrate it. Preserve its original records for investigation.
