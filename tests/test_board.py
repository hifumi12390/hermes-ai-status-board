import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_status_board.adapters import ADAPTERS
from ai_status_board.core import Board
from ai_status_board.history import History, RETENTION, RAW_RETENTION
from ai_status_board.model import SourceError
from ai_status_board.transport import Transport, MAX_BODY

from fixture_data import bundle
def adapter(id_): return next(a for a in ADAPTERS if a.id==id_)
def envelope(at=1000,value='operational',components=None):
    return {'surface_id':'test','overall':{'state':value},'components':components or [],'incidents':[],'source':{'fetched_epoch':at,'error':None,'stale':False}}

class Parsers(unittest.TestCase):
    def test_provider_fixtures(self):
        for a in ADAPTERS:
            with self.subTest(a=a.id):
                if a.kind=='html':
                    with self.assertRaises(SourceError) as e:a.normalize(bundle(a.id))
                    self.assertEqual(e.exception.kind,'unsupported')
                else:
                    result=a.normalize(bundle(a.id))
                    self.assertEqual(result['surface_id'],a.id)
                    self.assertTrue(result['components'])
                    self.assertIn(result['overall']['state'],('operational','unknown','degraded','partial_outage','major_outage','maintenance'))

    def test_degraded_incident_maintenance(self):
        a=adapter('github'); b=bundle('github'); doc=json.loads(b['current'])
        doc['components'][0]['status']='degraded_performance'
        sample=json.loads(b['history'])['incidents'][0]
        sample.update(status='investigating',resolved_at=None)
        doc['incidents']=[sample]
        b['current']=json.dumps(doc).encode()
        r=a.normalize(b)
        self.assertEqual(r['overall']['state'],'degraded')
        self.assertEqual(next(i for i in r['incidents'] if i['id']==sample['id'])['state'],'investigating')
        doc['components'][0]['status']='operational'
        doc['scheduled_maintenances']=[{'id':'m1','name':'Maintenance','status':'in_progress','components':[]}]
        b['current']=json.dumps(doc).encode()
        self.assertEqual(a.normalize(b)['overall']['state'],'maintenance')

    def test_schema_drift_and_malformed(self):
        for id_,raw in [('github',b'{broken'),('xai',b'<rss><channel>'),('xai',b'<!DOCTYPE rss><rss><channel/></rss>')]:
            with self.assertRaises(SourceError):adapter(id_).normalize({'current':raw})
        b=bundle('github'); doc=json.loads(b['current']); del doc['components']; b['current']=json.dumps(doc).encode()
        with self.assertRaises(SourceError):adapter('github').normalize(b)
        b=bundle('github'); doc=json.loads(b['current']);doc['components'][0]['status']='new_unrecognized_value';b['current']=json.dumps(doc).encode()
        self.assertEqual(adapter('github').normalize(b)['components'][0]['state'],'unknown')

    def test_openai_complete_ids(self):
        r=adapter('openai').normalize(bundle('openai'))
        self.assertEqual(len(r['components']),len(json.loads(bundle('openai')['components'])['components']))
        self.assertEqual(len({c['id'] for c in r['components']}),len(r['components']))

    def test_google_id_drift(self):
        b=bundle('google_workspace_gemini'); b['products']=b'{"products":[]}'
        with self.assertRaises(SourceError):adapter('google_workspace_gemini').normalize(b)

    def test_xai_no_false_green(self):
        r=adapter('xai').normalize(bundle('xai'))
        self.assertEqual(r['overall']['state'],'unknown')
        self.assertGreater(len(r['incidents']),0)
        self.assertTrue(all(i['impact']=='unknown' for i in r['incidents']))

    def test_statuspage_state_matrix(self):
        for id_ in ('github','openai','anthropic','cursor'):
            for raw,expected in [('degraded_performance','degraded'),('partial_outage','partial_outage'),('major_outage','major_outage'),('under_maintenance','maintenance')]:
                with self.subTest(provider=id_,state=raw):
                    b=bundle(id_);key='components' if id_=='openai' else 'current';d=json.loads(b[key]);d['components'][0]['status']=raw;b[key]=json.dumps(d).encode()
                    self.assertEqual(adapter(id_).normalize(b)['overall']['state'],expected)

    def test_google_active_incidents(self):
        for id_ in ('google_workspace_gemini','google_cloud_gemini'):
            a=adapter(id_);b=bundle(id_)
            b['current']=json.dumps([{'id':'incident-a','begin':'2026-09-09T01:00:00Z','end':None,'external_desc':'Test outage','affected_products':[{'id':a.product}],'most_recent_update':{'status':'SERVICE_OUTAGE'},'updates':[]}]).encode()
            self.assertEqual(a.normalize(b)['overall']['state'],'partial_outage')

    def test_xai_active_not_invented_severity(self):
        b=bundle('xai');b['current']=b['current'].replace(b'Status: RESOLVED',b'Status: INVESTIGATING',1)
        r=adapter('xai').normalize(b)
        self.assertEqual(r['incidents'][0]['state'],'investigating');self.assertEqual(r['incidents'][0]['impact'],'unknown')

    def test_external_text_never_executable_url(self):
        b=bundle('github');doc=json.loads(b['history']);doc['incidents'][0]['shortlink']='javascript:alert(1)';b['history']=json.dumps(doc).encode()
        self.assertEqual(adapter('github').normalize(b)['incidents'][0]['url'],adapter('github').origin)

class TransportTests(unittest.TestCase):
    url='https://status.example.test/status'
    def transport(self,handler):return Transport([self.url],httpx.Client(transport=httpx.MockTransport(handler)),clock=lambda:1000)
    def test_http_failures(self):
        for status,kind in [(429,'rate_limited'),(500,'source_unavailable'),(503,'source_unavailable'),(401,'auth_required'),(403,'auth_required'),(404,'unsupported'),(302,'source_unavailable')]:
            with self.subTest(status=status):
                t=self.transport(lambda r:httpx.Response(status,headers={'retry-after':'600'}))
                with self.assertRaises(SourceError) as e:t.get(self.url)
                self.assertEqual(e.exception.kind,kind)
                if status==429:self.assertEqual(e.exception.retry_after,600)
                t.close()
    def test_timeout(self):
        def fail(r):raise httpx.ReadTimeout('timeout',request=r)
        t=self.transport(fail)
        with self.assertRaises(SourceError) as e:t.get(self.url)
        self.assertEqual(e.exception.kind,'source_unavailable');t.close()
    def test_conditional_and_stale_cache(self):
        calls=[]
        def get(r):
            calls.append(r)
            return httpx.Response(200,content=b'{}',headers={'etag':'v1'}) if len(calls)==1 else httpx.Response(304)
        t=self.transport(get); self.assertEqual(t.get(self.url)[0],t.get(self.url)[0]);self.assertEqual(calls[1].headers['if-none-match'],'v1');t.close()
        t=self.transport(lambda r:httpx.Response(200,content=b'{}',headers={'age':'3601'}))
        with self.assertRaises(SourceError):t.get(self.url)
        t.close()
    def test_body_limit_and_allowlist(self):
        t=self.transport(lambda r:httpx.Response(200,content=b'x'*(MAX_BODY+1)))
        with self.assertRaises(SourceError):t.get(self.url)
        with self.assertRaises(SourceError):t.get('http://localhost/private')
        t.close()

class Storage(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.h=History(Path(self.tmp.name)/'history.db')
    def tearDown(self):self.h.close();self.tmp.cleanup()
    def test_duplicate_retention(self):
        e=envelope();self.h.record(e,1000,1300,[b'raw']);self.h.record(e,1000,1300,[b'raw'])
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)
        self.h.prune(1001+RAW_RETENTION);self.assertEqual(self.h.db.execute('SELECT count(*) FROM raw').fetchone()[0],0)
        self.h.prune(1001+RETENTION);self.assertEqual(self.h.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],0)
    def test_sleep_gap_and_time_weighting(self):
        self.h.record(envelope(),1000,1300);self.h.record(envelope(2000,'degraded'),2000,2300)
        t=self.h.timeline('test',1000,2300,300)
        self.assertEqual(t['known_seconds'],600);self.assertEqual(t['unknown_seconds'],700);self.assertEqual(t['operational_percent'],50)
    def test_components_add_remove(self):
        self.h.record(envelope(1000,components=[{'id':'a','name':'Old name','state':'operational'}]),1000,1300)
        self.h.record(envelope(1300,components=[{'id':'b','name':'New component','state':'degraded'}]),1300,1600)
        t=self.h.timeline('test',1000,1600,300,'a')
        self.assertEqual([b['state'] for b in t['buckets']],['operational','unknown'])
    def test_error_not_outage(self):
        e=envelope();e['source']['error']={'kind':'rate_limited'};e['source']['stale']=True
        self.h.record(e,1000,1300);self.assertIsNone(self.h.timeline('test',1000,1300,300)['operational_percent'])
    def test_migration(self):
        self.h.db.execute('DROP TABLE runtime');self.h.db.execute('DROP INDEX snapshots_time');self.h.db.execute('PRAGMA user_version=1');self.h.db.commit()
        self.h.migrate();self.assertEqual(self.h.db.execute('PRAGMA user_version').fetchone()[0],2)
        self.h.db.execute('PRAGMA user_version=999')
        with self.assertRaises(RuntimeError):self.h.migrate()
    def test_sparse_checkpoint_and_no_gap_bridge(self):
        self.h.record(envelope(),1000,1330);self.h.record(envelope(1300),1300,1630)
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],1)
        self.h.record(envelope(2000),2000,2330)
        self.assertEqual(self.h.db.execute('SELECT count(*) FROM snapshots').fetchone()[0],2)
        self.assertEqual(self.h.timeline('test',1000,2330,300)['unknown_seconds'],370)

    def test_official_events_do_not_backfill_observations(self):
        e=envelope();e['incidents']=[{'id':'i1','started_at':'1970-01-01T00:00:10Z','resolved_at':'1970-01-01T00:01:00Z','state':'resolved'}]
        self.h.record(e,1000,1330)
        self.assertEqual(self.h.events('test',0,100)['total'],1)
        self.assertEqual(self.h.timeline('test',0,100,50)['known_seconds'],0)

    def test_reopen_persistence(self):
        self.h.record(envelope(),1000,1300)
        self.h.close();self.h=History(Path(self.tmp.name)/'history.db')
        self.assertEqual(self.h.timeline('test',1000,1300,300)['known_seconds'],300)

class Polling(unittest.TestCase):
    def test_sleep_resume_schedules_once_and_respects_ttl(self):
        a=adapter('xai');now=[1000]
        t=Transport(a.endpoints().values(),httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=bundle('xai')['current']))))
        with tempfile.TemporaryDirectory() as folder:
            b=Board(Path(folder)/'h.db',[a],t,lambda:now[0]);b._poll(a)
            self.assertEqual(b.refresh(manual=True)['scheduled'],[])
            calls=[]
            original=b.executor
            class Executor:
                def submit(self,*args):calls.append(args)
            b.executor=Executor();now[0]=10000
            self.assertEqual(b.refresh()['scheduled'],['xai'])
            self.assertEqual(b.refresh()['scheduled'],[])
            self.assertEqual(len(calls),1)
            b.executor=original;b.close()

    def test_failure_stale_restore_and_retry(self):
        a=adapter('github');values={a.endpoints()[k]:v for k,v in bundle('github').items()};now=[1000]
        def handler(r):return httpx.Response(200,content=values[str(r.url)])
        t=Transport(values,httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as folder:
            b=Board(Path(folder)/'h.db',[a],t,lambda:now[0]);b._poll(a)
            self.assertFalse(b.last[a.id]['source']['stale'])
            t.client.close();t.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,headers={'retry-after':'900'})))
            now[0]=1400;b._poll(a)
            self.assertTrue(b.last[a.id]['source']['stale']);self.assertEqual(b.last[a.id]['source']['fetched_epoch'],1000)
            self.assertGreaterEqual(b.next[a.id],2300);self.assertEqual(b.refresh(manual=True)['scheduled'],[])
            self.assertEqual(b.history.timeline(a.id,1000,1500,300)['known_seconds'],330)
            b.close()

class Api(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        spec=importlib.util.spec_from_file_location('board_api_test',Path(__file__).parent.parent/'dashboard'/'plugin_api.py')
        self.api=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.api)
        a=adapter('github');data={a.endpoints()[k]:v for k,v in bundle('github').items()}
        t=Transport(data,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=data[str(r.url)]))))
        self.b=Board(Path(self.tmp.name)/'h.db',[a],t)
        self.b._poll(a);self.api._board=self.b
        app=FastAPI();app.include_router(self.api.router,prefix='/api/plugins/ai-status-board')
        self.client=TestClient(app)
    def tearDown(self):self.client.close();self.b.close();self.tmp.cleanup()
    def test_all_ranges(self):
        for hours,step in [(6,300),(24,1800),(168,1800),(720,21600),(2160,21600),(4320,86400)]:
            r=self.client.get(f'/api/plugins/ai-status-board/board?hours={hours}&step={step}')
            self.assertEqual(r.status_code,200);self.assertLessEqual(len(r.json()['surfaces'][0]['timeline']['buckets']),600)
    def test_invalid_queries(self):
        for query in ['hours=-1','hours=4320&step=300','step=0','hours=abc']:
            self.assertEqual(self.client.get('/api/plugins/ai-status-board/board?'+query).status_code,422)
    def test_component_and_refresh(self):
        id_=self.b.last['github']['components'][0]['id']
        self.assertEqual(self.client.get('/api/plugins/ai-status-board/component',params={'surface':'github','component':id_}).status_code,200)
        self.assertEqual(self.client.post('/api/plugins/ai-status-board/refresh').status_code,200)
        self.assertEqual(self.client.get('/api/plugins/ai-status-board/component',params={'surface':'missing','component':id_}).status_code,404)

if __name__=='__main__':unittest.main()
