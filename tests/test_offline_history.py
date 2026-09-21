"""Offline catch-up never invents observations, duration or severity."""
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from ai_status_board.adapters import ADAPTERS
from ai_status_board.core import Board
from ai_status_board.history import History
from ai_status_board.model import utc
from ai_status_board.transport import Transport
from fixture_data import bundle, incident


class OfficialTimeline(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.h=History(Path(self.tmp.name)/'history.db')

    def tearDown(self):
        self.h.close(); self.tmp.cleanup()

    def save(self,**changes):
        item={'id':'incident','started_at':utc(100),'resolved_at':utc(200),
              'state':'resolved','impact':'major','component_ids':['api']}
        item.update(changes)
        self.h.record_events('test',{'incidents':[item]},300)

    def test_offline_interval_and_component_scope(self):
        self.save()
        result=self.h.official_timeline('test',0,400,100)
        self.assertEqual([b['state'] for b in result['buckets']],['unknown','partial_outage','unknown','unknown'])
        self.assertEqual(self.h.timeline('test',0,400,100)['known_seconds'],0)
        self.assertEqual(self.h.official_timeline('test',0,400,100,'api')['event_count'],1)
        self.assertEqual(self.h.official_timeline('test',0,400,100,'other')['event_count'],0)

    def synced(self,**changes):
        sync={'window_start':utc(100),'fetched_at':utc(400),'error':None}
        sync.update(changes)
        self.h.save_runtime('test',{'history_sync':{'history':sync},'envelope':{'source':{'interval_seconds':300}}})

    def test_quiet_green_inside_retrieved_window_only(self):
        self.save(); self.synced()
        result=self.h.official_timeline('test',0,400,100)
        self.assertEqual([b['state'] for b in result['buckets']],
                         ['unknown','partial_outage','no_reported_incidents','no_reported_incidents'])
        self.assertEqual(self.h.timeline('test',0,400,100)['known_seconds'],0)
        self.assertEqual(self.h.official_timeline('test',0,400,100,'other')['buckets'][2]['state'],'no_reported_incidents')

    def test_failed_stale_and_unsupported_history_not_green(self):
        for changes,end in [({'error':{'kind':'rate_limited'}},400),({},5000),({'window_start':None},400)]:
            with self.subTest(changes=changes,end=end):
                self.synced(**changes)
                self.assertTrue(all(b['state']=='unknown' for b in self.h.official_timeline('test',200,end,100)['buckets']))

    def test_unknown_impact_and_unresolved_tail_not_green(self):
        self.save(impact='unknown',resolved_at=None,state='investigating'); self.synced()
        r=self.h.official_timeline('test',100,400,100)
        self.assertTrue(all(b['state']=='unknown' for b in r['buckets']))

    def test_empty_history_does_not_invent_historical_window(self):
        self.assertEqual(Board.history_window_start({'incidents':[]},400),utc(400))
        self.assertEqual(Board.history_window_start({'incidents':[{'started_at':utc(100)}]},400),utc(100))

    def test_fractional_query_end_does_not_create_gray_last_bucket(self):
        self.synced()
        r=self.h.official_timeline('test',200,400.1234567,100)
        self.assertEqual(r['buckets'][-1]['state'],'no_reported_incidents')

    def test_correction_and_duplicate_upsert(self):
        self.save(); self.save(); self.save(resolved_at=utc(150),impact='critical')
        result=self.h.official_timeline('test',100,200,25)
        self.assertEqual(result['event_count'],1)
        self.assertEqual([b['state'] for b in result['buckets']],['major_outage','major_outage','unknown','unknown'])

    def test_active_stops_at_last_retrieval(self):
        self.save(started_at=utc(50),resolved_at=None,state='investigating')
        r=self.h.official_timeline('test',100,500,100)
        self.assertEqual([b['event_count'] for b in r['buckets']],[1,1,0,0])

    def test_unknown_duration_and_publication_are_points(self):
        for changes in ({'resolved_at':None},{'time_basis':'publication'},{'resolved_at':utc(90)}):
            with self.subTest(changes=changes):
                self.save(**changes)
                r=self.h.official_timeline('test',0,400,100)
                self.assertEqual([b['point_count'] for b in r['buckets']],[0,1,0,0])

    def test_unknown_severity_visible_without_false_color(self):
        self.save(impact='unknown')
        b=self.h.official_timeline('test',100,200,100)['buckets'][0]
        self.assertEqual((b['state'],b['event_count'],b['unknown_impact_count']),('unknown',1,1))

    def test_maintenance_feed_and_no_list_truncation(self):
        self.h.record_events('test',{'maintenances':[{'id':'m','state':'completed','scheduled_for':utc(100),'scheduled_until':utc(200)}],
                          'feed_events':[{'id':str(i),'display_at':utc(250)} for i in range(201)]},300)
        r=self.h.official_timeline('test',0,400,100)
        self.assertEqual(r['event_count'],202)
        self.assertEqual(r['buckets'][1]['state'],'maintenance')
        self.assertEqual(r['buckets'][2]['point_count'],201)
        self.assertTrue(self.h.events('test',0,400)['truncated'])

    def test_failed_observation_does_not_extend_active_incident(self):
        self.save(resolved_at=None,state='monitoring')
        self.h.record({'surface_id':'test','overall':{'state':'operational'},'components':[],
                       'source':{'error':{'kind':'source_unavailable'}},
                       'incidents':[{'id':'incident','started_at':utc(100),'state':'monitoring','impact':'major'}]},500,600)
        self.assertEqual(self.h.official_timeline('test',300,600,100)['event_count'],0)

    def test_google_offline_incident_and_xai_publication(self):
        for a in [a for a in ADAPTERS if a.kind in ('google','rss')]:
            data=bundle(a.id)
            if a.kind=='google':
                data['current']=json.dumps([{'id':'offline','begin':utc(100),'end':utc(200),
                    'external_desc':'Synthetic outage','affected_products':[{'id':a.product}],
                    'most_recent_update':{'status':'SERVICE_OUTAGE'},'updates':[]}]).encode()
            result=a.normalize(data)
            self.h.record_events(a.id,result,300)
            if a.kind=='google':
                self.assertEqual(self.h.official_timeline(a.id,0,400,100)['event_count'],1)
            else:
                self.assertEqual(result['incidents'][0]['time_basis'],'publication')


class Catchup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'history.db'
        self.a=next(a for a in ADAPTERS if a.id=='github')
        self.now=1000; self.calls=[]; self.status={}; self.data=bundle('github')
        self.data['history']=b'{"incidents":[]}'
        self.b=self.board()

    def board(self):
        def handler(request):
            key=next(k for k,u in self.a.endpoints().items() if u==str(request.url))
            self.calls.append(key)
            return httpx.Response(self.status.get(key,200),content=self.data[key],headers={'retry-after':'3600'})
        t=Transport(self.a.endpoints().values(),httpx.Client(transport=httpx.MockTransport(handler)))
        return Board(self.path,[self.a],t,lambda:self.now)

    def tearDown(self):
        self.b.close(); self.tmp.cleanup()

    def add_offline_incident(self):
        item=incident(); item.update(started_at=utc(1400),resolved_at=utc(1600),impact='critical')
        self.data['history']=json.dumps({'incidents':[item]}).encode()

    def test_restart_recovers_resolved_offline_incident(self):
        self.b._poll(self.a); self.b.close()
        self.add_offline_incident(); self.now=2000; self.b=self.board(); self.b._poll(self.a)
        r=self.b.history.official_timeline('github',1300,1900,100)
        self.assertEqual(r['event_count'],1)
        self.assertEqual(r['buckets'][1]['state'],'major_outage')
        self.assertEqual(self.b.history.timeline('github',1400,1900,100)['known_seconds'],0)
        self.assertEqual(self.b.snapshot(hours=.25,step=100)['surfaces'][0]['official_timeline']['event_count'],1)

    def test_resume_before_hourly_deadline_refetches_history(self):
        self.b._poll(self.a); self.calls.clear(); self.add_offline_incident(); self.now=2000
        self.b._poll(self.a)
        self.assertIn('history',self.calls)
        self.assertEqual(self.b.history.events('github',1300,1900)['total'],1)

    def test_history_survives_current_failure(self):
        self.add_offline_incident(); self.status['current']=503; self.now=2000
        self.b._poll(self.a)
        self.assertEqual(self.b.history.events('github',1300,1900)['total'],1)
        self.assertEqual(self.b.last['github']['source']['error']['kind'],'source_unavailable')

    def test_history_429_persists_across_restart_and_manual_poll(self):
        self.status['history']=429; self.b._poll(self.a); self.b.close()
        self.now=2000; self.b=self.board(); self.calls.clear(); self.b._poll(self.a)
        self.assertNotIn('history',self.calls)
        self.assertIsNone(self.b.last['github']['source']['error'])
        self.assertEqual(self.b.last['github']['source']['history_error']['kind'],'rate_limited')
        self.now=4700; self.status['history']=200; self.add_offline_incident(); self.b._poll(self.a)
        self.assertIsNone(self.b.last['github']['source']['history_error'])

    def test_malformed_history_does_not_poison_current_or_stored_records(self):
        self.add_offline_incident(); self.b._poll(self.a)
        self.now=5000; self.data['history']=b'{broken'; self.b._poll(self.a)
        self.assertIsNone(self.b.last['github']['source']['error'])
        self.assertEqual(self.b.last['github']['source']['history_error']['kind'],'parse_error')
        self.assertEqual(self.b.history.events('github',1300,1900)['total'],1)


if __name__=='__main__': unittest.main()
