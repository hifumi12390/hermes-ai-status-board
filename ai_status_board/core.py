"""Backend polling and common API orchestration; one in-flight poll per surface."""
import copy
import concurrent.futures
import random
import threading
import time
from .adapters import ADAPTERS
from .history import History
from .model import SourceError, utc, seconds
from .transport import Transport

class Board:
    @staticmethod
    def history_window_start(events,now):
        # A retrieved list is bounded, not a promise of complete historical uptime.
        dates=[seconds(i.get('started_at')) for i in events.get('incidents',[])]
        return utc(min([d for d in dates if d is not None and d<=now],default=now))

    def __init__(self,path,adapters=None,transport=None,clock=time.time):
        self.adapters=adapters or ADAPTERS
        self.clock=clock
        self.history=History(path)
        self.transport=transport or Transport([u for a in self.adapters for u in a.endpoints().values()],clock=clock)
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=4,thread_name_prefix='ai-status-board')
        self.busy=set(); self.last={}; self.failures={}; self.next={}; self.history_sync={}
        self.thread=None
        self.backend_error=None
        for a in self.adapters:
            saved=self.history.runtime(a.id)
            if saved:
                self.last[a.id]=saved['envelope']
                self.next[a.id]=saved.get('next_poll',0)
                self.failures[a.id]=saved.get('failures',0)
                self.history_sync[a.id]=saved.get('history_sync',{})
                # On restart re-read the published window; retain endpoint retry deadlines.
                for sync in self.history_sync[a.id].values():
                    if not sync.get('error'): sync['next']=0
            else: self.next[a.id]=0

    def start(self):
        with self.lock:
            if self.thread is not None: return
            self.thread=threading.Thread(target=self._run,name='ai-status-board-scheduler',daemon=True)
            self.thread.start()

    def _run(self):
        previous=self.clock(); last_prune=0
        while not self.stop_event.is_set():
            now=self.clock()
            with self.lock:
                if now<previous-60:
                    # Clock rollback must not defer every provider indefinitely.
                    for a in self.adapters: self.next[a.id]=min(self.next[a.id],now+a.interval)
            previous=now
            self.refresh()
            if now-last_prune>3600:
                try: self.history.prune(now)
                except Exception: self.backend_error='History retention failed; check local storage permissions and free space.'
                last_prune=now
            self.stop_event.wait(5)

    def refresh(self,manual=False):
        now=self.clock(); scheduled=[]
        with self.lock:
            for a in self.adapters:
                if a.id in self.busy: continue
                due=self.next.get(a.id,0)
                old=self.last.get(a.id,{}).get('source',{})
                # Never bypass Retry-After, backoff, RSS TTL, or HTML low-frequency limit.
                minimum=a.interval if a.kind in ('rss','html') or self.failures.get(a.id,0) else 30
                can_manual=manual and now-old.get('attempted_epoch',0)>=minimum and not self.failures.get(a.id,0)
                if now>=due or can_manual:
                    self.busy.add(a.id); scheduled.append(a.id)
                    self.executor.submit(self._poll,a)
        return {'scheduled':scheduled,'next_poll':dict(self.next)}

    def _empty(self,a):
        return {'schema_version':1,'surface_id':a.id,'provider_id':a.provider,'display_name':a.name,'overall':{'state':'unknown','summary':'No validated current data'},'components':[],'incidents':[],'maintenances':[],'warnings':[a.warning] if a.warning else [],'capabilities':{'current':False,'components':False,'incidents':False,'official_uptime_series':False}}

    def _record(self,envelope,now,expires,raw):
        try: self.history.record(envelope,now,expires,raw)
        except Exception: self.backend_error='Local history write failed; current source status is independent. Check storage.'

    def _sync_history(self,a,attempted,raw):
        syncs=self.history_sync.setdefault(a.id,{})
        previous=self.last.get(a.id,{}).get('source',{}).get('attempted_epoch',0)
        resumed=attempted-previous>a.interval*2+60
        for key,url in a.history_endpoints().items():
            sync=syncs.setdefault(key,{})
            if attempted<sync.get('next',0) and (sync.get('error') or not resumed): continue
            try:
                body,meta=self.transport.get(url)
                events=a.normalize_history(key,body)
            except Exception as exc:
                error=exc if isinstance(exc,SourceError) else SourceError('parse_error','Official history could not be normalized')
                if error.kind=='parse_error': self.transport.invalidate([url])
                failures=sync.get('failures',0)+1
                sync.update(error={'kind':error.kind,'message':str(error)},failures=failures,
                            next=attempted+max(error.retry_after,min(21600,a.interval*2**min(failures-1,6))))
                continue
            try: self.history.record_events(a.id,events,self.clock())
            except Exception:
                self.backend_error='Official history write failed; check local storage permissions and free space.'
                sync['next']=attempted+a.interval
                continue
            raw.append(body)
            sync.update(fetched_at=utc(self.clock()),next=attempted+3600,error=None,failures=0,http=meta,
                        window_start=self.history_window_start(events,self.clock()))

    def _poll(self,a):
        attempted=self.clock(); raw=[]; metadata={}; bundle={}
        try:
            # Independent history sync still works when the current-status endpoint fails.
            self._sync_history(a,attempted,raw)
            for key,url in a.endpoints().items():
                if key in a.history_endpoints(): continue
                body,meta=self.transport.get(url)
                bundle[key]=body; metadata[key]=meta; raw.append(body)
            envelope=a.normalize(bundle)
            now=self.clock()
            history_error=next((s['error'] for s in self.history_sync.get(a.id,{}).values() if s.get('error')),None)
            source={'status_page_url':a.origin,'type':a.kind,'scope':'public official status','auth':'none','fetched_at':utc(now),'fetched_epoch':now,'attempted_at':utc(attempted),'attempted_epoch':attempted,'source_updated_at':envelope.pop('source_updated_at',None),'parser_version':'1','stale':False,'error':None,'history_error':history_error,'http':metadata,'interval_seconds':a.interval,'stale_after_seconds':a.interval*2+60}
            envelope['source']=source
            if history_error: envelope['warnings'].append('Official history sync failed; current status remains separate.')
            if a.kind in ('google','rss'):
                self.history_sync[a.id]={'combined':{'fetched_at':utc(now),'error':None,'next':now+a.interval,
                                                   'window_start':self.history_window_start(envelope,now)}}
            self.failures[a.id]=0
            due=now+a.interval+random.uniform(0,min(30,a.interval*.05))
            # TTL covers one expected poll plus scheduling jitter, not the full stale window.
            self._record(envelope,now,now+a.interval+30,raw)
        except Exception as exc:
            now=self.clock()
            error=exc if isinstance(exc,SourceError) else SourceError('parse_error','Response schema could not be normalized')
            if a.kind in ('google','rss'):
                self.history_sync.setdefault(a.id,{}).setdefault('combined',{})['error']={'kind':error.kind,'message':str(error)}
            if error.kind=='parse_error': self.transport.invalidate(a.endpoints().values())
            with self.lock: envelope=copy.deepcopy(self.last.get(a.id) or self._empty(a))
            envelope.setdefault('source',{}).update(status_page_url=a.origin,type=a.kind,auth='none',attempted_at=utc(attempted),attempted_epoch=attempted,stale=True,error={'kind':error.kind,'message':str(error)},interval_seconds=a.interval,stale_after_seconds=a.interval*2+60)
            failures=self.failures.get(a.id,0)+1; self.failures[a.id]=failures
            due=now+max(error.retry_after,min(21600,a.interval*2**min(failures-1,6)))+random.uniform(0,15)
            # Failed observations stop continuity without replacing last validated status.
            self._record(envelope,now,now+a.interval+30,raw)
        finally:
            try:
                with self.lock:
                    if 'envelope' in locals():
                        syncs=copy.deepcopy(self.history_sync.get(a.id,{}))
                        envelope['source']['history_sync']=syncs
                        envelope['source']['history_error']=next((s['error'] for s in syncs.values() if s.get('error')),None)
                        self.last[a.id]=envelope; self.next[a.id]=due
                        try: self.history.save_runtime(a.id,{'envelope':envelope,'next_poll':due,'failures':self.failures.get(a.id,0),'history_sync':syncs})
                        except Exception: self.backend_error='Local runtime checkpoint failed; check storage before restarting.'
            finally:
                with self.lock: self.busy.discard(a.id)

    def snapshot(self,hours=24,step=1800):
        now=self.clock(); start=now-hours*3600
        rows=[]
        with self.lock:
            values=copy.deepcopy(self.last); busy=list(self.busy); next_=dict(self.next)
        for a in self.adapters:
            e=values.get(a.id) or self._empty(a)
            source=e.setdefault('source',{})
            source.setdefault('status_page_url',a.origin)
            age=now-source.get('fetched_epoch',0)
            source['stale']=bool(source.get('error')) or age>a.interval*2+60 or age<0
            source['next_poll_at']=utc(next_.get(a.id,now))
            e['timeline']=self.history.timeline(a.id,start,now,step)
            e['official_timeline']=self.history.official_timeline(a.id,start,now,step)
            e['events']=self.history.events(a.id,start,now)
            # Detailed history comes only through the bounded event view.
            e.pop('incidents',None); e.pop('maintenances',None); e.pop('feed_events',None)
            rows.append(e)
        return {'schema_version':1,'generated_at':utc(now),'hours':hours,'step_seconds':step,'polling':busy,'surfaces':rows,'retention_days':180,'backend_error':self.backend_error}

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=6)
        self.executor.shutdown(wait=True,cancel_futures=True)
        self.transport.close(); self.history.close()
