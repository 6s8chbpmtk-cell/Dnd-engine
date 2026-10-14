# v3.5 local QA gateway evidence — October 13, 2026

- **255/255** full unittest discoveries passed on local Python 3.13 after adding
  `gateway_service.py` and thirteen new FakePort-only gateway tests.
- New coverage: Bearer API authentication; read-only exact tactical recovery;
  canonical structured one-operation write; deterministic post-write history
  verification; server-side OS attack/damage dice; no client-provided dice;
  no compound operations; no implicit player action; stale expected version;
  out-of-reach attack refuses to roll; bad-history rejection before writes.
- Prior **v3.4 GitHub Actions** had success on Python 3.11, 3.12 and 3.13.
  v3.5 GitHub matrix is pending upload.
- **No live service deployed** and **no real Airtable test write** occurred
  during v3.5 development. Running local tests does not verify live token
  scoping, Airtable network access, Render hosting or a GPT Action.
- See `DEPLOY_GATEWAY.md` for enforced write-token separation, limitations
  and the isolated deployment test plan.

---

# v3.4 local QA evidence — October 13, 2026

## Automated tests

- **242 / 242 unittest executions passed** in the local Python 3.13 environment.
- Includes 100 deterministic-seed synthetic multi-round encounters and replay verification, plus the 12-event / 10-dice QA battle fixture from the previous release.
- All prior fixture, combat-runtime, dice, Airtable persistence/mock fault, full-history, connector adapter and recovery tests still pass.
- New `test_write_guard.py` validates (a) a legitimate twelve-event history accepts and verifies a thirteenth canonical transition, (b) a full six-operation player/NPC turn produces six unique, consecutively replayable events, and (c) historical prose-only/compound event entries block the next write before any mutation.
- Additional cases reject malformed JSON event payloads, missing/duplicate/pending ledger rows, extra unaudited dice, stale versions, unverified/mismatched genesis, player actions without player intent, and post-write reconciliation failures. No blind retries.
- Updated `test_connector_cli.py` ensures a read-only field-ID connector export with prose-only ledger entry reports `HALT_UNVERIFIED` and `UNREPLAYABLE_EVENT_PAYLOAD`.
- Python 3.11 and 3.12 local tests were not available; GitHub's configured Actions matrix must still test those versions after source publication.

## Separate earlier QA observations (not claims of this new release)

An isolated Airtable QA campaign used a 12-event, 10-dice, three-round GM-only battle. A synthetic full connector export correctly replayed its ledger under Python; the original real Airtable records were also independently read using the connector. That verified tactical state only, not an entire D&D rules engine. The genuine Blackglass cold-start pilot in a separate conversation successfully loaded the initial campaign and saved a plausible version-3 tactical checkpoint, but combined six mechanical transitions into three prose-only event records. Consequently, strict replay from genesis **fails**, even though the latest checkpoint/hash and HP/turn order are self-consistent. v3.4's tests reproduce and reject that exact class of failure.

## Safety and remaining gates

1. Public GitHub repository currently contains v3.2; publish the complete v3.4 source and observe **all** Python 3.11/3.12/3.13 Actions jobs pass.
2. Use a **clean isolated campaign** to perform a full independent cross-chat read → one-event-per-operation guarded write → fresh-chat replay. Do not overwrite the original broken Blackglass history.
3. Isolate game-state write permissions: a direct ChatGPT Airtable write can bypass all Python guards. Dedicated serialization/coordination and restricted service-token access are necessary.
4. No multi-record Airtable transaction guarantee, independent cryptographic evidence of random source, or full SRD 5e rule coverage is claimed.
5. Existing four optional Airtable QA integrity automations remain inactive.

**No real Airtable campaign was written, repaired, or reset in the v3.4 code-development pass.**
