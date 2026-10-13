"""Adversarial contract tests for the read-only ChatGPT Airtable connector bridge."""
from __future__ import annotations
from copy import deepcopy
import unittest

from airtable_persistence import BattleAnchor, PersistenceError, actor_projection, decode_checkpoint
from connector_readonly import ConnectorReadOnlyPort, REQUIRED_TABLES, recover_from_connector_export, assemble_connector_export
from test_history_v4_fixture import make_persisted_fixture


class ConnectorReadbackTests(unittest.TestCase):
    def fixture(self):
        port, anchor, plan = make_persisted_fixture()
        state=decode_checkpoint(plan['final_checkpoint'])
        port.store['Session Logs']={'recGenesisSynthetic':{'id':'recGenesisSynthetic','fields':{
            'Session Log ID':plan['campaign_id']+':genesis',
            'Campaign ID':plan['campaign_id'], 'State Version':0,
            'Recap / What Happened':plan['initial_checkpoint']}}}
        port.store['Combatant Turns v2']={}
        for a in state.combatants:
            f=actor_projection(state,a.actor_id)
            f['Campaign ID']=anchor.campaign_id
            f['Combatant Integrity']='VALID'
            f['Encounter Record Link']=[anchor.encounter_record_id]
            port.store['Combatant Turns v2']['recActor-'+a.actor_id]={
                'id':'recActor-'+a.actor_id,'fields':f}
        # FakePort's IDs (e.g. 'campaign-record') are not Airtable-shaped.
        # Export synthetic but well-formed rec IDs, preserving all linked refs.
        id_map={}
        for table_rows in port.store.values():
            for rid in table_rows:
                if rid not in id_map:
                    id_map[rid]='rec'+str(len(id_map)+1).zfill(14)
        anchor=BattleAnchor(id_map[anchor.campaign_record_id],
                            id_map[anchor.encounter_record_id],
                            anchor.campaign_id,anchor.expected_rules)
        export={'schemas':{},'pages':{}}
        counter=1
        for table in sorted(REQUIRED_TABLES):
            all_names=set()
            for row in port.store[table].values():
                all_names.update(row['fields'])
            names=sorted(all_names)
            desc=[];mapping={}
            for name in names:
                fid='fld'+str(counter).zfill(14);counter+=1
                typ=('multipleRecordLinks' if name in ('Source Event Ledger Link','Encounter Record Link')
                      else 'singleSelect' if name in ('Rules Profile','Game Mode','Commit Status','Status',
                          'Controller','Action Available','Bonus Action Available','Reaction Available','Turn Status','Roll Mode','Source')
                      else 'number' if name in ('State Version','State Version Before','State Version After','Current State Version','Round Number','Current HP','Maximum HP')
                      else 'multilineText')
                mapping[name]=(fid,typ)
                desc.append({'id':fid,'name':name,'type':typ})
            export['schemas'][table]=desc
            raw=[]
            for row in port.store[table].values():
                cells={}
                for name,value in row['fields'].items():
                    fid,typ=mapping[name]
                    if typ=='singleSelect' and value is not None:
                        value={'id':'sel-synthetic','name':value}
                    if typ=='multipleRecordLinks':
                        value=[{'id':id_map[item],'name':'test'} for item in value]
                    cells[fid]=deepcopy(value)
                raw.append({'id':id_map[row['id']],'cellValuesByFieldId':cells})
            export['pages'][table]=[{'records':raw}]
        return export,anchor,plan

    def test_complete_snapshot_replays_12_events_10_dice(self):
        export,anchor,plan=self.fixture()
        loaded=recover_from_connector_export(export,anchor)
        self.assertEqual((loaded.history.checked_events,loaded.history.checked_rolls),(12,10))
        self.assertEqual(loaded.battle.fingerprint(),plan['final_fingerprint'])
        self.assertEqual((loaded.battle.round_no, loaded.battle.active.actor_id),(4,'qa:raider'))

    def test_missing_schema_blocks(self):
        export,anchor,_=self.fixture()
        del export['schemas']['Dice Audit v2']
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_missing_page_blocks(self):
        export,anchor,_=self.fixture()
        del export['pages']['Event Ledger']
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_unfetched_pagination_blocks(self):
        export,anchor,_=self.fixture()
        export['pages']['Event Ledger'][0]['nextCursor']='remaining'
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_duplicate_record_blocks(self):
        export,anchor,_=self.fixture()
        pg=export['pages']['Dice Audit v2'][0]['records']
        pg.append(deepcopy(pg[0]))
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_unknown_field_id_blocks(self):
        export,anchor,_=self.fixture()
        raw=export['pages']['Campaigns'][0]['records'][0]['cellValuesByFieldId']
        raw['fld_not_in_schema']='danger'
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_bad_selection_fails_closed(self):
        export,anchor,_=self.fixture()
        field=next(f['id'] for f in export['schemas']['Campaigns'] if f['name']=='Rules Profile')
        export['pages']['Campaigns'][0]['records'][0]['cellValuesByFieldId'][field]={'id':'only-select-id'}
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_duplicate_field_id_blocks(self):
        export,anchor,_=self.fixture()
        export['schemas']['Campaigns'][1]['id']=export['schemas']['Campaigns'][0]['id']
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_duplicate_field_name_blocks(self):
        export,anchor,_=self.fixture()
        export['schemas']['Campaigns'][1]['name']=export['schemas']['Campaigns'][0]['name']
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_stale_combatant_turn_blocks(self):
        export,anchor,_=self.fixture()
        sv=next(f['id'] for f in export['schemas']['Combatant Turns v2'] if f['name']=='State Version')
        export['pages']['Combatant Turns v2'][0]['records'][0]['cellValuesByFieldId'][sv]=11
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_stale_action_flag_blocks(self):
        export,anchor,_=self.fixture()
        key=next(f['id'] for f in export['schemas']['Combatant Turns v2'] if f['name']=='Action Available')
        raw=export['pages']['Combatant Turns v2'][0]['records'][0]['cellValuesByFieldId']
        raw[key]={'name':'No' if raw[key]['name']=='Yes' else 'Yes'}
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_missing_die_roll_blocks(self):
        export,anchor,_=self.fixture()
        export['pages']['Dice Audit v2'][0]['records'].pop()
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_incorrect_historical_event_blocks(self):
        export,anchor,_=self.fixture()
        name=next(f['id'] for f in export['schemas']['Event Ledger'] if f['name']=='Commit Status')
        export['pages']['Event Ledger'][0]['records'][0]['cellValuesByFieldId'][name]={'name':'Proposed'}
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_rejects_writes(self):
        export,anchor,_=self.fixture()
        port=ConnectorReadOnlyPort(export['schemas'],export['pages'])
        with self.assertRaises(PersistenceError):port.create('Event Ledger',{})
        with self.assertRaises(PersistenceError):port.update('Campaigns',anchor.campaign_record_id,{})

    def test_returns_deep_copies(self):
        export,anchor,_=self.fixture()
        port=ConnectorReadOnlyPort(export['schemas'],export['pages'])
        first=port.get('Campaigns',anchor.campaign_record_id)
        first['fields']['State Version']=99
        self.assertEqual(port.get('Campaigns',anchor.campaign_record_id)['fields']['State Version'],12)

    def test_explicit_complete_two_pages(self):
        export,anchor,plan=self.fixture()
        parts=export['pages']['Event Ledger'][0]['records']
        export['pages']['Event Ledger']=[{'records':parts[:4],'nextCursor':'cursor2'},
                                           {'records':parts[4:]}]
        self.assertEqual(recover_from_connector_export(export,anchor).history.state_version,12)


    def test_assemble_raw_connector_tool_results(self):
        export,anchor,_=self.fixture()
        fake_schema={'tables':[{'name':n,'fields':f} for n,f in export['schemas'].items()]}
        assembled=assemble_connector_export(fake_schema,export['pages'])
        self.assertEqual(recover_from_connector_export(assembled,anchor).history.state_version,12)

    def test_assemble_missing_table_fails_closed(self):
        export,anchor,_=self.fixture()
        fake_schema={'tables':[{'name':n,'fields':f} for n,f in export['schemas'].items()
                               if n!='Session Logs']}
        assembled=assemble_connector_export(fake_schema,export['pages'])
        with self.assertRaises(PersistenceError):recover_from_connector_export(assembled,anchor)

    def test_zero_values_are_not_dropped(self):
        export,anchor,_=self.fixture()
        port=ConnectorReadOnlyPort(export['schemas'],export['pages'])
        row=port.list('Combatant Turns v2')[0]['fields']
        self.assertEqual(row['Death Save Successes'],0)
        self.assertEqual(row['Death Save Failures'],0)

    def test_mislinked_actor_row_blocks(self):
        export,anchor,_=self.fixture()
        fid=next(f['id'] for f in export['schemas']['Combatant Turns v2'] if f['name']=='Encounter Record Link')
        row=export['pages']['Combatant Turns v2'][0]['records'][0]['cellValuesByFieldId']
        row[fid]=[{'id':'rec00000000000000','name':'wrong'}]
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_noncontiguous_page_sequence_fails_closed(self):
        export,anchor,_=self.fixture()
        page=export['pages']['Event Ledger'][0]
        export['pages']['Event Ledger']=[page, {'records':[]}]
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

    def test_missing_required_combatant_record_blocks(self):
        export,anchor,_=self.fixture()
        export['pages']['Combatant Turns v2'][0]['records'].pop()
        with self.assertRaises(PersistenceError):recover_from_connector_export(export,anchor)

if __name__=='__main__':unittest.main()
