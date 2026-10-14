"""v3.5 QA: narrow authenticated HTTP facade for v3.4 VerifiedTurnGateway.

This file is a reference/test service, not an audited hosted deployment. Only a
single-process instance and *separate, exclusive Airtable writer credential*
can approach enforcing the policy. Writes are not transactionally atomic in Airtable.

No client-provided roll payload or generic arbitrary mutation params are accepted.
"""
from __future__ import annotations

from dataclasses import asdict
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from typing import Callable
from urllib.parse import urlsplit

from airtable_persistence import AirtableRESTPort, AirtablePort, BattleAnchor, PersistenceError, WriteUncertain
from combat_runtime import cell_distance_ft
from engine_core import RulesError, critical_damage_expression, d20_result, roll
from recovery_bootstrap import recover_from_airtable
from write_guard import VerifiedTurnGateway

MAX_BODY_BYTES = 12_000
API_VERSION = '0.1-qa'
OPS = frozenset({'move', 'end_turn', 'disengage', 'melee_attack'})
BASE_KEYS = frozenset({'campaign_id', 'campaign_record_id', 'encounter_record_id', 'edition'})
TURN_BASE_KEYS = BASE_KEYS | {'event_key', 'expected_version', 'operation', 'actor_id', 'player_intent'}


class BadRequest(ValueError):
    pass


def _exact(payload: dict, required: frozenset[str], optional: frozenset[str] = frozenset()) -> None:
    if not isinstance(payload, dict) or not required.issubset(payload) or not set(payload).issubset(required | optional):
        raise BadRequest('Missing or unrecognized request fields; one verified operation only')


def _safe_string(value: object, label: str, *, limit: int = 350) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(c in value for c in '\r\n\x00'):
        raise BadRequest(f'Invalid {label}')
    return value


def _anchor(request: dict) -> BattleAnchor:
    cid = _safe_string(request['campaign_id'], 'campaign_id')
    crid = _safe_string(request['campaign_record_id'], 'campaign_record_id')
    erid = _safe_string(request['encounter_record_id'], 'encounter_record_id')
    edition = request['edition']
    if not crid.startswith('rec') or not erid.startswith('rec') or edition not in ('2014', '2024'):
        raise BadRequest('Invalid Airtable record IDs or locked edition')
    return BattleAnchor(crid, erid, cid, edition)


def _tactical_state(loaded) -> dict:
    battle = loaded.battle
    return {
        'status': 'TACTICAL_HISTORY_VERIFIED',
        'scope': 'LIMITED 5e tactical only; full character/scene/GM facts not certified',
        'encounter_id': battle.encounter_id,
        'version': battle.version,
        'round': battle.round_no,
        'encounter_status': battle.status,
        'active_actor': battle.active.actor_id if battle.status == 'active' else None,
        'fingerprint': battle.fingerprint(),
        'checked_events': loaded.history.checked_events,
        'checked_rolls': loaded.history.checked_rolls,
        'combatants': [
            {'actor_id': a.actor_id, 'controller': a.controller, 'hp': a.hp,
             'max_hp': a.max_hp, 'temp_hp': a.temp_hp, 'ac': a.ac,
             'x': a.x, 'y': a.y, 'move_left': a.move_left,
             'action': a.action, 'bonus': a.bonus, 'reaction': a.reaction,
             'initiative': a.initiative} for a in battle.combatants
        ],
        'map': {'blocked': [list(t) for t in battle.blocked],
                'difficult': [list(t) for t in battle.difficult],
                'traversable': [list(t) for t in battle.traversable]},
    }


def _dice_expression(count_name: str, modifier: int) -> str:
    return f'{count_name}{modifier:+d}'


def _operation(request: dict, loaded) -> tuple[str, dict, dict]:
    """Whitelist a SINGLE operation and build its validated engine params.

    Dice originate *only* from server-side secrets via engine_core.roll. A GPT
    cannot supply invented faces or override the damage/attack modifiers.
    """
    operation = request['operation']
    if operation not in OPS:
        raise BadRequest('Operation unsupported by narrow gateway')
    b = loaded.battle
    actor_id = _safe_string(request['actor_id'], 'actor_id')
    if b.status != 'active' or actor_id != b.active.actor_id:
        raise BadRequest('Only the active actor can act')
    actor = b.active
    if actor.controller == 'player' and not request['player_intent'].strip():
        raise BadRequest('Player-controlled action requires explicit player intent')

    extras = {
        'move': frozenset({'path'}),
        'melee_attack': frozenset({'target_id', 'reaction_window_confirmed'}),
        'end_turn': frozenset(),
        'disengage': frozenset(),
    }[operation]
    _exact(request, TURN_BASE_KEYS | extras)
    params = {'actor_id': actor_id}
    rolls: dict = {}
    if operation == 'move':
        path = request['path']
        if (not isinstance(path, list) or len(path) > 20 or not path or
            any(not isinstance(p, list) or len(p) != 2 or any(type(c) is not int for c in p) for p in path)):
            raise BadRequest('Explicit bounded list of 5-foot-square coordinates required')
        params['path'] = path
    if operation == 'melee_attack':
        target_id = _safe_string(request['target_id'], 'target_id')
        possible = [a for a in b.combatants if a.actor_id == target_id]
        if len(possible) != 1 or possible[0].team == actor.team:
            raise BadRequest('Target must be an opposing known combatant')
        target = possible[0]
        if (not actor.action or target.hp == 0 or actor.hp == 0 or
            cell_distance_ft(actor, target) > actor.reach_ft):
            raise BadRequest('Attack actor/resource or melee reach invalid; no dice generated')
        # This confirms only the engine's finite melee-grid assumptions, not
        # unusual concealment, reactions, spells, diagonal vision or other 5e rules.
        if request['reaction_window_confirmed'] is not True:
            raise BadRequest('GM must explicitly adjudicate reaction window before attack')
        if target.concentration_save_pending or actor.concentration_save_pending:
            raise BadRequest('Pending concentration save must be resolved first')
        attack = roll(_dice_expression('d20', actor.attack_bonus))
        outcome = d20_result(attack, target.ac, 'attack', b.edition)
        damage = (roll(critical_damage_expression(actor.damage_expression)
                       if outcome['critical_hit'] else actor.damage_expression)
                  if outcome['success'] else None)
        params = {'actor_id': actor_id, 'target_id': target_id,
                  'attack': attack, 'damage': damage,
                  'visibility_source': 'v3.5 QA adjacent grid melee only; GM must verify other visibility rules',
                  'reaction_window': 'confirmed', 'attack_style': 'melee'}
        rolls = {'attack': attack.to_dict(), 'attack_resolution': outcome,
                 'damage': damage.to_dict() if damage else None}
        operation = 'attack'
    return operation, params, rolls


class GatewayAPI:
    """Framework-independent handler: all stateful calls use one exclusive server.

    For the real deployment the Airtable token MUST be write capable and the
    ChatGPT Airtable connector / any other competing writer MUST NOT be able
    to mutate the same records. This class cannot revoke OAuth permissions.
    """
    def __init__(self, *, api_key: str, port_factory: Callable[[], AirtablePort], public_url: str = ''):
        if len(api_key) < 32:
            raise ValueError('Gateway API key must contain at least 32 characters')
        self.api_key = api_key
        self.port_factory = port_factory
        self.public_url = public_url.rstrip('/')

    def dispatch(self, method: str, path: str, authorization: str, body: bytes = b'') -> tuple[int, dict]:
        if method == 'GET' and path == '/health':
            return 200, {'service': 'dnd-gateway', 'version': API_VERSION, 'status': 'running',
                         'note': 'Not proof of Airtable connectivity or transactional writes'}
        if method == 'GET' and path == '/openapi.json':
            return 200, self.openapi()
        # Never accept unauthenticated mutating requests, including if URL is wrong.
        if not (authorization.startswith('Bearer ') and
                compare_digest(authorization[7:], self.api_key)):
            return 401, {'status': 'UNAUTHORIZED', 'message': 'Gateway authentication required'}
        if method != 'POST' or path not in ('/v1/state', '/v1/turn'):
            return 404, {'status': 'NOT_FOUND'}
        if len(body) > MAX_BODY_BYTES:
            return 413, {'status': 'BAD_REQUEST', 'message': 'Request is too large'}
        try:
            try:
                request = json.loads(body)
            except (ValueError, TypeError):
                raise BadRequest('Expected valid JSON object')
            if not isinstance(request, dict):
                raise BadRequest('Expected JSON object')
            if path == '/v1/state':
                _exact(request, BASE_KEYS)
            else:
                _exact(request, TURN_BASE_KEYS,
                       frozenset({'path', 'target_id', 'reaction_window_confirmed'}))
                if (type(request['expected_version']) is not int or request['expected_version'] < 0 or
                    not isinstance(request['player_intent'], str) or len(request['player_intent']) > 1200):
                    raise BadRequest('Invalid expected_version or player_intent')
                event_key = _safe_string(request['event_key'], 'event_key', limit=500)
                if not event_key.startswith(request['campaign_id'] + ':'):
                    raise BadRequest('Event key must be namespaced to campaign')
            anchor = _anchor(request)
            port = self.port_factory()
            if path == '/v1/state':
                return 200, _tactical_state(recover_from_airtable(port, anchor))
            # Perform read-only, full-history recovery BEFORE any dice generation.
            loaded = recover_from_airtable(port, anchor)
            if loaded.battle.version != request['expected_version']:
                raise BadRequest('Stale expected_version: reload full verified state')
            operation, params, rolls = _operation(request, loaded)
            gate = VerifiedTurnGateway(port, anchor)
            result = gate.commit(event_key=event_key,
                                 expected_version=request['expected_version'],
                                 operation=operation, params=params,
                                 player_intent=request['player_intent'])
            verified = recover_from_airtable(port, anchor)
            return 200, {'status': 'VERIFIED_COMMITTED' if result.result == 'VERIFIED' else result.result,
                         'event_key': result.event_key, 'event_record_id': result.event_record_id,
                         'version': result.version, 'dice': rolls,
                         'state': _tactical_state(verified)}
        except WriteUncertain:
            return 409, {'status': 'HALT_UNVERIFIED', 'reason': 'WRITE_OUTCOME_UNCERTAIN',
                         'do_not_retry': True,
                         'message': 'Airtable may have persisted the action. Reconcile manually; do not reroll.'}
        except BadRequest as exc:
            return 422, {'status': 'REJECTED_NO_WRITE', 'message': str(exc)}
        except (PersistenceError, RulesError) as exc:
            return 409, {'status': 'HALT_UNVERIFIED', 'do_not_retry': True,
                         'message': str(exc)[:400]}
        except Exception:
            return 503, {'status': 'HALT_UNVERIFIED', 'do_not_retry': True,
                         'message': 'Gateway failure. Do not assume success or repeat the request.'}

    def openapi(self) -> dict:
        # GPT Actions import using the public HTTPS service's /openapi.json URL.
        # Keep URL generic rather than reflecting untrusted Host headers.
        base = self.public_url or 'https://REPLACE-WITH-YOUR-GATEWAY.example'
        identity = {k: {'type': 'string'} for k in BASE_KEYS}
        identity['edition']['enum'] = ['2014', '2024']
        base_schema = {'type': 'object', 'additionalProperties': False,
                       'required': sorted(BASE_KEYS), 'properties': identity}
        props = {**identity,
                 'event_key': {'type': 'string'}, 'expected_version': {'type': 'integer', 'minimum': 0},
                 'operation': {'type': 'string', 'enum': sorted(OPS)},
                 'actor_id': {'type': 'string'}, 'player_intent': {'type': 'string'},
                 'target_id': {'type': 'string'}, 'reaction_window_confirmed': {'type': 'boolean'},
                 'path': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer'},
                                                    'minItems': 2, 'maxItems': 2}}}
        turn_schema = {'type': 'object', 'additionalProperties': False,
                       'required': sorted(TURN_BASE_KEYS), 'properties': props}
        def endpoint(op_id: str, summary: str, schema: dict) -> dict:
            return {'summary': summary, 'operationId': op_id,
                    'security': [{'BearerAuth': []}],
                    'requestBody': {'required': True, 'content': {
                        'application/json': {'schema': schema}}},
                    'responses': {'200': {'description': 'Verified result'},
                                  '409': {'description': 'HALT; state or write uncertain'},
                                  '422': {'description': 'Rejected input, no write'}}}
        return {
            'openapi': '3.0.3',
            'info': {'title': 'D&D Verified Turn Gateway (QA)', 'version': API_VERSION,
                     'description': 'Narrow v3.5 tactical read/write gateway. Does not certify full D&D 5e rules.'},
            'servers': [{'url': base}],
            'paths': {
                '/v1/state': {'post': endpoint('loadVerifiedBattleState',
                                               'Verify full tactical history and load current battle', base_schema)},
                '/v1/turn': {'post': endpoint('commitVerifiedTacticalAction',
                                              'One mechanical transition per request. Server generates dice.', turn_schema)}
            },
            'components': {'securitySchemes': {'BearerAuth': {'type': 'http', 'scheme': 'bearer'}}}
        }


def main() -> None:
    api_key = os.environ.get('DND_GATEWAY_API_KEY', '')
    if len(api_key) < 32:
        raise SystemExit('Refusing to start: DND_GATEWAY_API_KEY requires 32+ characters')
    if not os.environ.get('AIRTABLE_BASE_ID') or not os.environ.get('AIRTABLE_TOKEN'):
        raise SystemExit('Refusing to start: AIRTABLE_BASE_ID and AIRTABLE_TOKEN required')
    public = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if not public.startswith('https://') or '/' in public[len('https://'):]:
        raise SystemExit('Refusing to start: PUBLIC_BASE_URL must be a public HTTPS origin')
    api = GatewayAPI(api_key=api_key, port_factory=AirtableRESTPort.from_env, public_url=public)
    port = int(os.environ.get('PORT', '8000'))

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, payload):
            data = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path)
            self._reply(*api.dispatch('GET', path.path, self.headers.get('Authorization', '')))

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self._reply(400, {'status': 'BAD_REQUEST'})
                return
            if size < 0 or size > MAX_BODY_BYTES:
                self._reply(413, {'status': 'BAD_REQUEST', 'message': 'Request too large'})
                return
            raw = self.rfile.read(size)
            path = urlsplit(self.path)
            self._reply(*api.dispatch('POST', path.path,
                                      self.headers.get('Authorization', ''), raw))

        def log_message(self, *args):
            # Avoid printing sensitive request URL fragments or GM notes.
            pass

    # HTTPServer intentionally services one request at a time in this *one*
    # process; this does not protect against multiple deployed replicas or
    # unrelated Airtable editors. Deploy exactly one instance; never autoscale.
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
