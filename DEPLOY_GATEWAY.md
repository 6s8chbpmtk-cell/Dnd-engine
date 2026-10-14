# v3.5 QA — Restricted writer service deployment (one instance)

**Status: prototype, locally tested. NOT live until you deploy it.**

v3.4 `VerifiedTurnGateway` protects only calls that use it. This v3.5 QA web
wrapper is the first step toward preventing a ChatGPT session from silently
writing compound, unauditable Airtable mutations. It exposes only two narrowly
scoped operations: read verified tactical state and commit **one** supported
mechanical action. It does not allow arbitrary Airtable record edits or
client-provided dice results. Every request loads and replays history.

## Secure architecture

1. **Private writer secret:** A new Airtable personal access token limited to
   the Narrative RPG Engine base and the least record scopes required, stored
   in the hosting platform's secret environment; never in GitHub, the chat or
   a public OpenAPI schema. The writer may edit gameplay records.
2. **Restricted GPT Action:** The custom GPT uses only the gateway API.
   GPT Actions and apps are mutually exclusive in a custom GPT; do not attach
   the broad Airtable app to this GPT. A different general ChatGPT chat with
   unrestricted Airtable app access remains capable of direct edits; revoke
   or restrict those other credentials separately if enforcing *all* writers.
3. **Separate gateway API key:** A second independently generated 32+ character
   secret configured in the hosting environment and in the GPT Action's API-key
   authentication (Bearer). This is NOT the Airtable token.
4. **One service process:** Single instance, no autoscaling or additional
   workers. Airtable's multi-table writes are not atomic; failures require
   operator reconciliation. One-process serialization does NOT prevent
   competing external editors or concurrent deploy replicas.
5. **Public HTTPS URL:** Expose only the `/health`, `/openapi.json`, `/v1/state`
   and `/v1/turn` routes. The latter two require the Bearer key. Never share
   private campaign exports or service credentials.

## Example Render staging deployment

1. On the Render dashboard select **New → Web Service** and connect the
   GitHub `Dnd-engine` repository. Use a QA-only service. A free instance is
   adequate for an experiment but not a production availability guarantee.
2. Start command: `python gateway_service.py`. Build command:
   `python -m compileall -q .`. Use Python 3.11+ and **one instance**.
3. Add these four environment variables via Render's **secret** settings:

   - `AIRTABLE_BASE_ID`: the ID of your *test-only* Airtable base.
   - `AIRTABLE_TOKEN`: a new **writer PAT**, restricted to the test base and
     only necessary scopes. Do NOT reuse the broad ChatGPT connector account.
   - `DND_GATEWAY_API_KEY`: random, independently generated secret (32+ chars).
   - `PUBLIC_BASE_URL`: full public HTTPS origin assigned by Render, for example
     `https://YOUR-SERVICE.onrender.com` (no trailing slash).

4. Deploy; `/health` should say `running`, which is **not** proof of Airtable
   connectivity or ledger correctness. The server refuses to boot without
   required environment values; do not bypass this.
5. Open `/openapi.json` to obtain the OpenAPI 3.0 schema for the exact public
   HTTPS host. Import this schema in the Custom GPT editor's **Create new
   action** screen and set its authentication to **API key → Bearer** using
   `DND_GATEWAY_API_KEY`. *Never paste the Airtable PAT into the GPT editor.*
6. Test `/v1/state` against an **isolated fresh campaign** with a canonical
   v0 genesis, matching projections, and no prior bad ledger. It should yield
   `TACTICAL_HISTORY_VERIFIED`; old Blackglass v3 must yield
   `HALT_UNVERIFIED` and **must not be auto-repaired**.
7. Test a single `move` in the **isolated test campaign**. Confirm exactly
   one `Verified` Event Ledger entry, one version increment, matching combatant
   projections, and exact re-read of state. Confirm a mixed or unaudited action
   is rejected. *Do not turn on the gateway for real ongoing campaigns yet.*

### Required GPT operating instruction

```
Use only the verified-gateway Action to read/commit tactical encounter state.
Do not use the direct Airtable app in this GPT. At campaign load call
loadVerifiedBattleState and stop on anything other than TACTICAL_HISTORY_VERIFIED.
For each user-declared turn, split movement/attack/end-turn into separate,
sequential calls to commitVerifiedTacticalAction. Each call must contain a
unique, stable campaign-namespaced event key and the current expected_version.
Never provide or invent die faces; the gateway generates and audits its dice.
Only act on the player character when the user explicitly declares intent.
For reactions/visibility not covered by verified mechanics, stop for adjudication.
On 409 HALT_UNVERIFIED or uncertain acknowledgement, STOP. Never reroll/retry
an ambiguous request, never fabricate save confirmations, and never edit Airtable
through any other tool. When unsupported, explain what mechanic is missing.
```

## Endpoint contract

### `POST /v1/state`

```json
{
  "campaign_id":"qa:isolated",
  "campaign_record_id":"rec...",
  "encounter_record_id":"rec...",
  "edition":"2014"
}
```

Returns a **read-only tactical** state plus verified history count/fingerprint.
This is not a full spellbook, inventory, NPC knowledge or narrative scene export.

### `POST /v1/turn`

Supply identity keys above plus:

- `event_key`: unique namespaced idempotency key.
- `expected_version`: last independently verified version.
- `actor_id`: exact active actor ID.
- `player_intent`: the player's actual words when controlling the PC; can be
  empty for GM-controlled NPC actions.
- `operation`: `move`, `end_turn`, `disengage`, or `melee_attack`.
- for `move`, add `path` with consecutive grid coordinates `[[x,y],...]`;
- for `melee_attack`, add `target_id` and
  `reaction_window_confirmed:true` only after GM confirms no outstanding
  reaction window. No rolls or damage amounts may be sent by the client.

**One operation = one event.** `melee_attack` is intentionally only the
basic adjacent melee subset. Missing/risky D&D features, full LOS and
complex reactions remain unsupported. The server generates OS-backed rolls
and then uses `VerifiedTurnGateway.commit` to persist and replay them.

## Remaining risks before production

- **Direct Airtable connector bypass:** Another connected app/chat account
  may still have write privileges. An Action-only custom GPT reduces the attack
  surface but does not revoke permissions everywhere. Make a read-only PAT
  for any other client you control; review broad OAuth grants and collaborator
  roles. Do not assume ChatGPT's approval prompts are access controls.
- **Non-atomic, remote Airtable writes:** No distributed transaction or
  compare-and-swap, no cross-instance lock, no auto-repair, no retry.
- **Identity and player consent:** A client-provided `player_intent` string is
  not a cryptographic proof of what the human said. User-confirmed Actions can
  strengthen workflow, but not fully solve identity/consent problems.
- **Limited rules:** Current basic melee uses the GM's
  `reaction_window_confirmed` declaration; special concealment/cover,
  reactions, spells, class abilities and other 5e edge cases still need
  automated checks. Not a complete GM or character manager.
- **Hosting limits:** Public hosting costs/availability and GPT Action request
  timeouts may interrupt play. A timeout after a write is *uncertain*, never
  proof that the action failed. Use human reconciliation.

### Safe rollback

To disable a deployed gateway, stop the hosting service or revoke its secret.
Do not roll back Airtable ledger rows after partially completed writes. The
original Blackglass records serve as read-only regression evidence.
