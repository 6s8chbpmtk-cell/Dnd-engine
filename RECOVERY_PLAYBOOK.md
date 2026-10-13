# Game-session cold-load: mandatory fail-closed procedure

**No chat transcript is authoritative for tactical state.** The operator must load and verify these Airtable records before resuming a campaign:

1. Select exactly one Campaigns record with the user-specified Campaign ID. Confirm edition and named active rules profile. Do not silently switch 2014/2024 or narrative rules.
2. Resolve one matching Encounters record and validate its serialized `battle-v3` snapshot, game status, location and version.
3. Locate a **unique persisted genesis** Session Logs row (`Session Log ID=<campaign-id>:genesis`, `State Version=0`, `Recap / What Happened` is serialized `battle-v3`), not a summary invented from current memory.
4. Load every Event Ledger entry for that campaign, reject duplicates and anything other than `Verified`; validate contiguous state versions and replay full event chain from genesis.
5. Fetch Dice Audit v2 rows, demand 1:1 linkage of all recorded dice, authentic audited fields, valid face arithmetic/ranges, and `Reconciled with Event`. Source/digest only prove internal consistency; they do not independently attest fairness of claimed entropy.
6. Require one Combatant Turns v2 row per active encounter combatant with matching campaign/encounter and **exact** version, HP, action flags, positions, round and initiative. Reject omitted, duplicated and stale actor projections.
7. Compare recomputed final fingerprint, round/version and active actor with Encounter and Campaign state. Report any mismatch and refuse mechanical actions until reconciled.
8. Load Characters, NPCs, Inventory & Resources, Scene Objects v2, Quests & Threads and pinned Rules Profiles separately. A tactical checkpoint cannot substitute for complete PC and NPC sheets, spell slots, inventory, player-known geography or GM secrets.
9. Start/continue gameplay only after explicit user action (for PCs), rule legality, reaction windows, and event single-writer safeguards. GM-controlled NPCs can act autonomously. Record dice plus mechanics before narrating final consequences.

## Programmatic offline / REST-assisted recovery

```python
from airtable_persistence import AirtableRESTPort, BattleAnchor
from recovery_bootstrap import recover_from_airtable

# Requires external, intentionally injected AIRTABLE_BASE_ID and AIRTABLE_TOKEN;
# never put secrets in code, GitHub, play logs, or ChatGPT public artifacts.
port = AirtableRESTPort.from_env()
anchor = BattleAnchor(
    campaign_record_id="<campaign Airtable record ID>",
    encounter_record_id="<encounter Airtable record ID>",
    campaign_id="<campaign-id>", expected_rules="2014"
)
loaded = recover_from_airtable(port, anchor)  # Strictly read-only
print(loaded.history)
```

This package has no always-on tool runner. The separate ChatGPT Airtable plugin uses field IDs and app-scoped permissions and can execute this procedure turn-by-turn. Multi-row Airtable writes **are not atomic**; concurrent writers remain blocked. The function checks only the tactical subset and does not make a campaign broadly play-ready on its own.


## ChatGPT connector-only recovery (v3.3, read-only)

When the REST token is unavailable (normal in ChatGPT), use the connected Airtable
plugin to retrieve the complete schemas and **all pages** of the required tables.
Their direct results are field-ID keyed and select/link cell values are objects.
`connector_readonly.assemble_connector_export(schema_response, pages_by_table)`
creates the adapter payload, where pages_by_table maps table names to ordered,
complete `list_records_for_table` response pages. Then:

```python
from airtable_persistence import BattleAnchor
from connector_readonly import recover_from_connector_export
anchor = BattleAnchor('rec...campaign', 'rec...encounter', 'exact-campaign-id', '2014')
loaded = recover_from_connector_export(payload, anchor)
```

Never write from this adapter. A new unrelated Airtable write after the snapshot
could make the validated state stale: re-read Campaign/Encounter/actor versions
immediately before any mutation. This tactical recovery does NOT certify full
character sheets, GM-secret compartmentalization, scene LOS or spell legality.


## v3.4 Blackglass genesis compatibility

The genesis loader recognizes **exactly** `<campaign-id>:genesis` and
`<campaign-id>:session01:genesis`, unique and version zero, from verified
Session Logs. `State Reconciliation Notes` is preferred when it contains
canonical battle-v3 JSON; a prose recap is not itself a checkpoint. Version
zero can be `Prepared` or `Active`; for `Active`, the active actor must match.
A legacy ledger of natural-language descriptions still fails replay regardless
of matching state-version and fingerprint fields. No unverified action follows.
