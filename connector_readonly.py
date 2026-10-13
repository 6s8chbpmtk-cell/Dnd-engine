"""Read-only bridge from ChatGPT Airtable MCP table results to the RPG recovery engine.

The ChatGPT Airtable connector returns field-ID keyed ``cellValuesByFieldId``
with rich select/link objects. Recovery code intentionally consumes conventional
Airtable REST ``{id, fields:{name: value}}`` rows. This adapter translates
*explicit, complete, caller-supplied* MCP table exports. It never asks for a
personal access token, performs writes, or trusts assistant conversation text.

This is a transport adapter, NOT live continuous connector integration. The
caller must collect ALL pages from each of the six required tables and supply
fresh data from the same intended recovery operation. Values can change between
non-atomic reads; a single writer and version re-check remain essential.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from airtable_persistence import AirtablePort, BattleAnchor, PersistenceError
from recovery_bootstrap import RecoveryResult, recover_from_airtable

REQUIRED_TABLES = frozenset({
    'Campaigns', 'Encounters', 'Session Logs', 'Event Ledger',
    'Dice Audit v2', 'Combatant Turns v2',
})


def _normalize_cell(value: Any, field_type: str) -> Any:
    if field_type in ('singleSelect', 'singleCollaborator'):
        if isinstance(value, dict) and isinstance(value.get('name'), str):
            return value['name']
        if isinstance(value, str):
            return value
        raise PersistenceError('Malformed selection or collaborator value')
    if field_type == 'multipleRecordLinks':
        if not isinstance(value, list):
            raise PersistenceError('Malformed linked-record value')
        out=[]
        for link in value:
            if isinstance(link,dict):
                item=link.get('id')
            else:
                item=link
            if not isinstance(item,str) or not item.startswith('rec'):
                raise PersistenceError('Malformed record link reference')
            out.append(item)
        if len(set(out))!=len(out):
            raise PersistenceError('Duplicate link in record')
        return out
    if field_type=='multipleSelects':
        if not isinstance(value,list):
            raise PersistenceError('Malformed multi-select value')
        return [_normalize_cell(v,'singleSelect') for v in value]
    # All other values, including 0, False and serialized JSON, are preserved.
    return deepcopy(value)


@dataclass(frozen=True)
class _Table:
    rows: dict[str, dict]


class ConnectorReadOnlyPort:
    """Recover from complete MCP exports without exposing Airtable credentials.

    ``schemas`` format: {table_name: [{id, name, type}, ...]}
    ``pages`` format: {table_name: [{records:[{id,cellValuesByFieldId}],
       nextCursor?: null}, ...]}

    Each table must have at least one page, even if the result is empty. Omitted
    tables, unknown field IDs, duplicate records, and unsatisfied page cursors
    abort the entire snapshot. Intended for an end-to-end connector QA readback.
    """
    def __init__(self,schemas:Mapping[str,Any],pages:Mapping[str,Any]):
        if not isinstance(schemas, Mapping) or not isinstance(pages,Mapping):
            raise PersistenceError('Connector schema/pages must be mappings')
        if not REQUIRED_TABLES.issubset(schemas) or not REQUIRED_TABLES.issubset(pages):
            raise PersistenceError('Missing one or more required connector tables')
        self._tables: dict[str,_Table] = {}
        for table in REQUIRED_TABLES:
            fields=schemas[table]
            if not isinstance(fields,list) or not fields:
                raise PersistenceError(f'Empty/invalid schema for {table}')
            names={};seen_names=set()
            for f in fields:
                if not isinstance(f,dict) or not all(k in f for k in ('id','name','type')):
                    raise PersistenceError('Invalid connector schema entry')
                fid=f['id'];name=f['name'];kind=f['type']
                if not isinstance(fid,str) or not fid.startswith('fld') or not isinstance(name,str) or not name or not isinstance(kind,str):
                    raise PersistenceError('Invalid field identifier/name/type')
                if fid in names or name in seen_names:
                    raise PersistenceError('Duplicate field ID/name in schema')
                names[fid]=(name,kind);seen_names.add(name)
            chunks=pages[table]
            if not isinstance(chunks,list) or not chunks:
                raise PersistenceError('No pages loaded for '+table)
            entries={}
            for i,chunk in enumerate(chunks):
                if not isinstance(chunk,dict) or not isinstance(chunk.get('records'),list):
                    raise PersistenceError('Invalid connector page')
                # A nextCursor is only acceptable when another page follows.
                cursor=chunk.get('nextCursor')
                if cursor is not None and (not isinstance(cursor,str) or not cursor or i==len(chunks)-1):
                    raise PersistenceError('Connector pagination incomplete')
                if cursor is None and i<len(chunks)-1:
                    raise PersistenceError('Connector pagination has discontinuity')
                for row in chunk['records']:
                    if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'].startswith('rec') or not isinstance(row.get('cellValuesByFieldId'),dict):
                        raise PersistenceError('Malformed connector record')
                    rid=row['id']
                    if rid in entries:
                        raise PersistenceError('Duplicate record across connector pages')
                    cols={}
                    for fid,value in row['cellValuesByFieldId'].items():
                        if fid not in names:
                            raise PersistenceError('Unknown field ID in connector result')
                        name,kind=names[fid]
                        cols[name]=_normalize_cell(value,kind)
                    entries[rid]={'id':rid,'fields':cols}
            self._tables[table]=_Table(entries)

    def list(self,table:str)->list[dict]:
        if table not in self._tables:
            raise PersistenceError('Table not supplied in connector export')
        return deepcopy(list(self._tables[table].rows.values()))

    def get(self,table:str,record_id:str)->dict:
        if table not in self._tables:
            raise PersistenceError('Table not supplied in connector export')
        try:
            return deepcopy(self._tables[table].rows[record_id])
        except KeyError as exc:
            raise PersistenceError('Required record missing from connector export') from exc

    def create(self,table:str,fields:dict)->dict:
        raise PersistenceError('Read-only connector export cannot create game state')

    def update(self,table:str,record_id:str,fields:dict)->dict:
        raise PersistenceError('Read-only connector export cannot update game state')


def recover_from_connector_export(export:Mapping[str,Any],anchor:BattleAnchor)->RecoveryResult:
    """Strictly replay and reconcile a six-table, field-ID keyed export."""
    if not isinstance(export,Mapping) or not isinstance(export.get('schemas'),dict) or not isinstance(export.get('pages'),dict):
        raise PersistenceError('Invalid connector export envelope')
    return recover_from_airtable(ConnectorReadOnlyPort(export['schemas'],export['pages']),anchor)


def assemble_connector_export(schema_response: Mapping[str,Any],
                              pages_by_table: Mapping[str,Any]) -> dict:
    """Accept unmodified ``list_tables_for_base`` and per-table ``list_records`` responses.

    ``pages_by_table`` values are lists of consecutive connector page responses;
    the caller MUST keep requesting a new page until nextCursor becomes null.
    This function does not silently replace an empty/paginated table with guessed
    rows, nor does it permit incomplete field schemas.
    """
    if not isinstance(schema_response,Mapping) or not isinstance(schema_response.get('tables'),list):
        raise PersistenceError('Not a list_tables_for_base schema response')
    if not isinstance(pages_by_table,Mapping):
        raise PersistenceError('Connector table pages must be mapped by name')
    named={}
    for table in schema_response['tables']:
        if not isinstance(table,dict) or not isinstance(table.get('name'),str) or not isinstance(table.get('fields'),list):
            raise PersistenceError('Malformed table schema object')
        if table['name'] in named:
            raise PersistenceError('Duplicate table name in connector schema')
        named[table['name']]=table['fields']
    return {'schemas':{k:named[k] for k in REQUIRED_TABLES if k in named},
            'pages':{k:deepcopy(pages_by_table[k]) for k in REQUIRED_TABLES if k in pages_by_table}}
