"""SQLite history: bounded forward observations, never backfilled uptime."""
import hashlib
import json
import sqlite3
import threading
import zlib
from pathlib import Path
from .model import seconds, utc, worst

DAY = 86400
RETENTION = 180 * DAY
RAW_RETENTION = 7 * DAY
RAW_LIMIT = 32 * 1024 * 1024

def encode(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',',':'))

class History:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path,check_same_thread=False,timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.migrate()

    def migrate(self):
        with self.lock, self.db:
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2: raise RuntimeError('History schema is newer than this plugin; no changes made')
            if version < 1:
                self.db.execute('CREATE TABLE snapshots(surface TEXT NOT NULL, at REAL NOT NULL, expires REAL NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(surface,at))')
                self.db.execute('CREATE TABLE incidents(surface TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,seen REAL NOT NULL,PRIMARY KEY(surface,id,kind))')
                self.db.execute('CREATE TABLE raw(hash TEXT PRIMARY KEY,at REAL NOT NULL,body BLOB NOT NULL)')
                self.db.execute('PRAGMA user_version=1')
            if version < 2:
                self.db.execute('CREATE TABLE runtime(surface TEXT PRIMARY KEY,payload TEXT NOT NULL)')
                self.db.execute('CREATE INDEX snapshots_time ON snapshots(at)')
                self.db.execute('PRAGMA user_version=2')

    def runtime(self,surface):
        with self.lock:
            row = self.db.execute('SELECT payload FROM runtime WHERE surface=?',(surface,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_runtime(self,surface,value):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO runtime VALUES(?,?)',(surface,encode(value)))

    def record(self,envelope,at,expires,raw=()):
        # Snapshots keep compact state+components; official text has a separate upsert table.
        payload = {k:v for k,v in envelope.items() if k not in ('incidents','maintenances','feed_events')}
        semantic = {k:v for k,v in payload.items() if k != 'source'}
        semantic['source_health'] = {k:envelope.get('source',{}).get(k) for k in ('error','stale','history_error','parser_version')}
        fingerprint = hashlib.sha256(encode(semantic).encode()).hexdigest()
        surface = envelope['surface_id']
        with self.lock,self.db:
            last=self.db.execute('SELECT at,expires,fingerprint FROM snapshots WHERE surface=? ORDER BY at DESC LIMIT 1',(surface,)).fetchone()
            if last and last[2]==fingerprint and last[0]<at<=last[1] and at-last[0]<3600:
                self.db.execute('UPDATE snapshots SET expires=? WHERE surface=? AND at=?',(expires,surface,last[0]))
            else:
                self.db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)',(surface,at,expires,fingerprint,encode(payload)))
            if not envelope.get('source',{}).get('error'):
                self._upsert_events(surface,envelope,at)
            for body in raw:
                digest = hashlib.sha256(body).hexdigest()
                self.db.execute('INSERT INTO raw VALUES(?,?,?) ON CONFLICT(hash) DO UPDATE SET at=excluded.at',(digest,at,zlib.compress(body)))

    def record_events(self,surface,envelope,at):
        """Official history can arrive without a successful current observation."""
        with self.lock,self.db:
            self._upsert_events(surface,envelope,at)

    def _upsert_events(self,surface,envelope,at):
        for kind,key in [('incident','incidents'),('maintenance','maintenances'),('feed','feed_events')]:
            for item in envelope.get(key,[]):
                self.db.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?)',(surface,item['id'],kind,encode(item),at))

    def official_timeline(self,surface,start,end,step,component=None):
        """Published incident spans, never inferred availability or uptime.

        Unknown-ended/resolved records and feed publications are point markers.
        An active span stops at its last successful retrieval, not at query time.
        """
        with self.lock:
            rows=self.db.execute('SELECT kind,payload,seen FROM incidents WHERE surface=?',(surface,)).fetchall()
        runtime=self.runtime(surface) or {}
        syncs=runtime.get('history_sync',{})
        primary=syncs.get('history') or syncs.get('combined') or {}
        window_start=seconds(primary.get('window_start'))
        fetched=seconds(primary.get('fetched_at'))
        interval=runtime.get('envelope',{}).get('source',{}).get('interval_seconds',300)
        usable=(window_start is not None and fetched is not None and
                0<=end-fetched<=max(3660,interval*2+60) and
                not any(s.get('error') for s in syncs.values()))
        buckets=[]
        cursor=start
        while cursor<end:
            buckets.append({'from':utc(cursor),'to':utc(min(end,cursor+step)),'state':'unknown','event_count':0,'point_count':0,'unknown_impact_count':0,'_states':[]})
            cursor+=step
        count=0; uncertain_after=float('inf')
        for kind,payload,seen in rows:
            item=json.loads(payload)
            if component is not None and component not in item.get('component_ids',[]): continue
            begin=seconds(item.get('started_at') or item.get('scheduled_for') or item.get('display_at'))
            finish=seconds(item.get('resolved_at') or item.get('scheduled_until'))
            point=kind=='feed' or item.get('time_basis')=='publication'
            if finish is None:
                if item.get('state') in ('investigating','identified','monitoring','in_progress','verifying'):
                    finish=seen
                    uncertain_after=min(uncertain_after,seen)
                else: point=True
            if finish is not None and begin is not None and finish<=begin: point=True
            if begin is None or begin>=end: continue
            if point:
                if begin<start: continue
                left=right=int((begin-start)//step)
            else:
                if finish<=start: continue
                left=max(0,int((begin-start)//step))
                right=min(len(buckets)-1,int((min(finish,end)-start-0.000001)//step))
            value='maintenance' if kind=='maintenance' else {'none':'informational','minor':'degraded','major':'partial_outage','critical':'major_outage'}.get(item.get('impact'),'unknown')
            count+=1
            for idx in range(left,right+1):
                b=buckets[idx]; b['event_count']+=1; b['point_count']+=int(point)
                b['unknown_impact_count']+=int(value=='unknown'); b['_states'].append(value)
        for b in buckets:
            b['state']=worst(b.pop('_states'))
            if not b['event_count'] and usable and seconds(b['to'])<=uncertain_after and seconds(b['from'])>=max(window_start,end-RETENTION):
                b['state']='no_reported_incidents'
        return {'basis':'official_incident_history','buckets':buckets,'event_count':count,
                'retrieved_window_start':primary.get('window_start'),'history_fetched_at':primary.get('fetched_at'),
                'completeness':'Green means no reported incident in the retrieved publication window, not measured uptime. Gray means unavailable/outside that window or unknown severity. Severity describes the incident; point markers have no asserted duration.'}

    def prune(self,now):
        with self.lock,self.db:
            self.db.execute('DELETE FROM snapshots WHERE at<?',(now-RETENTION,))
            self.db.execute('DELETE FROM raw WHERE at<?',(now-RAW_RETENTION,))
            # Resolved records expire by their event timestamp, not by repeated fetch.
            for surface,id_,kind,payload,seen in self.db.execute('SELECT * FROM incidents').fetchall():
                item=json.loads(payload)
                end=seconds(item.get('resolved_at') or item.get('scheduled_until') or item.get('display_at'))
                if (end is not None and end < now-RETENTION) or (end is None and seen<now-RETENTION):
                    self.db.execute('DELETE FROM incidents WHERE surface=? AND id=? AND kind=?',(surface,id_,kind))
            total=self.db.execute('SELECT COALESCE(SUM(length(body)),0) FROM raw').fetchone()[0]
            for hash_,size in self.db.execute('SELECT hash,length(body) FROM raw ORDER BY at').fetchall():
                if total<=RAW_LIMIT: break
                self.db.execute('DELETE FROM raw WHERE hash=?',(hash_,)); total-=size
        with self.lock: self.db.execute('PRAGMA wal_checkpoint(PASSIVE)')

    def events(self,surface,start,end,limit=200):
        with self.lock:
            rows=self.db.execute('SELECT kind,payload FROM incidents WHERE surface=? ORDER BY seen DESC',(surface,)).fetchall()
        result=[]
        for kind,payload in rows:
            item=json.loads(payload)
            begin=seconds(item.get('started_at') or item.get('scheduled_for') or item.get('display_at'))
            finish=seconds(item.get('resolved_at') or item.get('scheduled_until'))
            if begin is not None and begin<=end and (finish is None or finish>=start):
                if kind=='feed' and begin<start: continue
                result.append(dict(item,kind=kind))
        result.sort(key=lambda i:i.get('started_at') or i.get('scheduled_for') or i.get('display_at') or '',reverse=True)
        return {'items':result[:limit],'total':len(result),'truncated':len(result)>limit,'basis':'official_incident_history','completeness':'Published window only; absence of an incident is not proof of uptime.'}

    def timeline(self,surface,start,end,step,component=None):
        with self.lock:
            rows=self.db.execute('SELECT at,expires,payload FROM snapshots WHERE surface=? AND at<=? AND expires>? ORDER BY at',(surface,end,start)).fetchall()
        buckets=[]
        cursor=start
        while cursor<end:
            buckets.append({'from':utc(cursor),'to':utc(min(end,cursor+step)),'state':'unknown','coverage':0.0,'_states':[],'_known':0.0,'_good':0.0})
            cursor+=step
        for n,(at,expires,payload) in enumerate(rows):
            finish=min(expires,rows[n+1][0] if n+1<len(rows) else end,end)
            begin=max(at,start)
            if finish<=begin: continue
            doc=json.loads(payload)
            source=doc.get('source',{})
            value=doc['overall']['state'] if component is None else next((c['state'] for c in doc['components'] if c['id']==component),'unknown')
            if source.get('error') or source.get('stale'): value='unknown'
            left=max(0,int((begin-start)//step)); right=min(len(buckets)-1,int((finish-start)//step))
            for idx in range(left,right+1):
                duration=max(0,min(finish,start+(idx+1)*step)-max(begin,start+idx*step))
                if duration and value!='unknown':
                    b=buckets[idx]; b['_states'].append(value); b['_known']+=duration
                    if value=='operational': b['_good']+=duration
        known=good=0
        for idx,b in enumerate(buckets):
            width=min(step,end-(start+idx*step)); known+=b['_known']; good+=b['_good']
            b['state']=worst(b.pop('_states'))
            b['coverage']=round(min(1,b.pop('_known')/width),4)
            b.pop('_good')
        return {'basis':'locally_observed_availability','buckets':buckets,'coverage_percent':round(100*known/(end-start),2),'operational_percent':round(100*good/known,2) if known else None,'known_seconds':round(known),'unknown_seconds':round(end-start-known)}

    def close(self):
        with self.lock: self.db.close()
