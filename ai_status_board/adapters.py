"""Fixed public sources -> common schema. No network calls in parsers."""
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .model import SourceError, require, safe_url, stamp, state, text, worst

class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def plain(value):
    p = PlainHTML(); p.feed(value); return ' '.join(p.parts)

def json_data(raw):
    try: return json.loads(raw)
    except (ValueError, UnicodeError): raise SourceError('parse_error', 'Invalid JSON')

def array(obj, key):
    return require(obj.get(key), list, 'Missing or changed ' + key)

def identifier(item):
    value = item.get('id')
    if not isinstance(value, str) or not value or len(value)>256:
        raise SourceError('parse_error', 'Missing or invalid stable ID')
    return value

def rss_date(value):
    try: return stamp(parsedate_to_datetime(value).isoformat())
    except (ValueError, TypeError, OverflowError): return None

@dataclass(frozen=True)
class Adapter:
    id: str
    provider: str
    name: str
    origin: str
    kind: str
    interval: int = 300
    product: str = ''
    warning: str = ''

    def endpoints(self):
        if self.kind == 'statuspage':
            return {'current': self.origin+'api/v2/summary.json', 'history':self.origin+'api/v2/incidents.json', 'maintenance':self.origin+'api/v2/scheduled-maintenances.json'}
        if self.kind == 'openai':
            return {'current':self.origin+'api/v2/status.json','components':self.origin+'api/v2/components.json','history':self.origin+'api/v2/incidents.json','feed':self.origin+'history.rss'}
        if self.kind == 'rss': return {'current':self.origin+'feed.xml'}
        if self.kind == 'google': return {'current':self.origin+'incidents.json','products':self.origin+'products.json'}
        return {'current':self.origin}

    def normalize(self, bundle):
        if self.kind in ('statuspage','openai'): result = self._statuspage(bundle)
        elif self.kind == 'google': result = self._google(bundle)
        elif self.kind == 'rss': result = self._rss(bundle['current'])
        else: result = self._html(bundle['current'])
        result.update(schema_version=1, provider_id=self.provider, surface_id=self.id, display_name=self.name)
        result.setdefault('warnings', [])
        if self.warning: result['warnings'].append(self.warning)
        result.setdefault('maintenances', [])
        result.setdefault('source_updated_at', None)
        result['capabilities'] = {'current':self.kind not in ('rss',), 'components':bool(result['components']), 'incidents':self.kind!='html', 'maintenance':self.kind=='statuspage', 'official_uptime_series':False}
        return result

    def _incident(self, item):
        require(item, dict, 'Invalid incident')
        raw = text(item.get('status'), 50)
        updates = array(item, 'incident_updates')
        return {'id':identifier(item),'title':text(item.get('name')),'state':raw if raw in ('investigating','identified','monitoring','resolved','postmortem') else 'unknown',
                'impact':item.get('impact') if item.get('impact') in ('none','minor','major','critical') else 'unknown',
                'started_at':stamp(item.get('started_at') or item.get('created_at')), 'resolved_at':stamp(item.get('resolved_at')),
                'updated_at':stamp(item.get('updated_at')), 'component_ids':[identifier(c) for c in item.get('components',[])],
                'url':safe_url(item.get('shortlink'), self.origin),
                'updates':[{'id':identifier(u),'state':text(u.get('status'),50),'body':text(u.get('body'),10000),'display_at':stamp(u.get('display_at') or u.get('created_at'))} for u in updates[:200]]}

    def _statuspage(self, bundle):
        current = require(json_data(bundle['current']), dict, 'Invalid current status')
        status = require(current.get('status'), dict, 'Missing status')
        if not isinstance(status.get('indicator'),str): raise SourceError('parse_error','Missing status indicator')
        components_doc = json_data(bundle['components']) if self.kind=='openai' else current
        comps = []
        for c in array(components_doc,'components'):
            require(c,dict,'Invalid component')
            if not isinstance(c.get('status'),str) or not isinstance(c.get('name'),str): raise SourceError('parse_error','Changed component schema')
            comps.append({'id':identifier(c),'name':text(c['name'],200),'group':text(c.get('group_id'),256) or None,'state':state(c['status']),'raw_state':text(c['status'],80),'showcase':c.get('showcase',True),'updated_at':stamp(c.get('updated_at'))})
        history = json_data(bundle['history']) if 'history' in bundle else current
        incidents = {identifier(i):self._incident(i) for i in array(history,'incidents')}
        # Summary's active set is authoritative for activity; old history must not overwrite it.
        for i in current.get('incidents',[]): incidents[identifier(i)] = self._incident(i)
        maint = {}
        for doc in ([json_data(bundle['maintenance'])] if 'maintenance' in bundle else []) + [current]:
            for m in doc.get('scheduled_maintenances',[]):
                maint[identifier(m)] = {'id':identifier(m),'title':text(m.get('name')),'state':text(m.get('status'),50),'scheduled_for':stamp(m.get('scheduled_for')),'scheduled_until':stamp(m.get('scheduled_until')),'component_ids':[identifier(c) for c in m.get('components',[])],'url':safe_url(m.get('shortlink'), self.origin)}
        overall = worst([state(status['indicator'])]+[c['state'] for c in comps])
        if any(m.get('status') in ('in_progress','verifying') for m in current.get('scheduled_maintenances',[])): overall = worst([overall,'maintenance'])
        warnings = []
        if any(c['state']=='unknown' for c in comps): warnings.append('Unrecognized component status; shown as Unknown.')
        feed_events = self._generic_feed(bundle['feed']) if 'feed' in bundle else []
        # RSS is supplementary event history, never a current-state input.
        return {'overall':{'state':overall,'raw_state':status['indicator'],'summary':text(status.get('description'))},'components':comps,'incidents':list(incidents.values()),'feed_events':feed_events,'maintenances':list(maint.values()),'source_updated_at':stamp(current.get('page',{}).get('updated_at')),'warnings':warnings}

    def _google(self,bundle):
        products = array(require(json_data(bundle['products']),dict,'Invalid products'),'products')
        product = next((p for p in products if p.get('id')==self.product),None)
        if product is None: raise SourceError('parse_error','Configured stable product ID disappeared')
        data = require(json_data(bundle['current']),list,'Invalid Google incident array')
        incidents = []
        for i in data:
            require(i,dict,'Invalid Google incident')
            affected = array(i,'affected_products')
            if self.product not in [p.get('id') for p in affected]: continue
            if not stamp(i.get('begin')) or 'end' not in i: raise SourceError('parse_error','Changed Google incident timestamps')
            updates = array(i,'updates')
            severity = {'SERVICE_DISRUPTION':'minor','SERVICE_OUTAGE':'major','AVAILABLE':'none','SERVICE_INFORMATION':'none'}.get(i.get('most_recent_update',{}).get('status'),'unknown')
            incidents.append({'id':identifier(i),'title':text(i.get('external_desc')),'state':'resolved' if i.get('end') else 'investigating','impact':severity,'started_at':stamp(i['begin']),'resolved_at':stamp(i.get('end')),'updated_at':stamp(i.get('modified')),'component_ids':[self.product],'url':safe_url(self.origin+i.get('uri',''),self.origin),'updates':[{'id':text(u.get('when'),100),'state':text(u.get('status'),80),'display_at':stamp(u.get('when')),'body':text(u.get('text'),10000)} for u in updates[:200]]})
        active = [i for i in incidents if i['state']!='resolved']
        current = worst([state(i['impact']) if i['impact']!='unknown' else 'unknown' for i in active]) if active else 'operational'
        return {'overall':{'state':current,'summary':'Public incident-derived status; not an individual availability probe.'},'components':[{'id':self.product,'name':text(product.get('title'),200),'state':current,'showcase':True}], 'incidents':incidents,'source_updated_at':max((i['updated_at'] for i in incidents if i['updated_at']),default=None)}

    def _xml(self, raw):
        if re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I): raise SourceError('parse_error','XML declarations are not accepted')
        try: root = ET.fromstring(raw)
        except (ET.ParseError, ValueError): raise SourceError('parse_error','Invalid XML')
        channel = root.find('channel')
        if root.tag != 'rss' or channel is None: raise SourceError('parse_error','Changed RSS schema')
        return channel

    def _generic_feed(self,raw):
        channel = self._xml(raw)
        return [{'id':text(i.findtext('guid') or i.findtext('link'),256),'title':text(i.findtext('title')),'display_at':rss_date(i.findtext('pubDate')),'body':text(plain(i.findtext('description') or ''),10000),'url':safe_url(i.findtext('link'),self.origin)} for i in channel.findall('item')[:500]]

    def _rss(self,raw):
        channel = self._xml(raw)
        incidents, components = [], {}
        for item in channel.findall('item')[:1000]:
            title = item.findtext('title') or ''
            body = plain(item.findtext('description') or '')
            match = re.search(r'Status:\s*([A-Z_]+)',body)
            if not match or not item.findtext('guid'): raise SourceError('parse_error','Changed xAI item schema')
            status = match[1].lower()
            resolved = re.search(r'Resolved:\s*(.*?GMT)',body)
            comp = urlsplit(safe_url(item.findtext('link'),self.origin)).path.strip('/').split('/')[0]
            name = re.match(r'\[([^]]+)\]', title)
            if comp: components[comp] = {'id':comp,'name':name[1] if name else comp,'state':'unknown','showcase':True}
            dates = re.findall(r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+GMT',body)
            incidents.append({'id':text(item.findtext('guid'),256),'title':text(title),'state':status if status in ('resolved','investigating','identified','monitoring') else 'unknown','impact':'unknown','started_at':rss_date(item.findtext('pubDate')),'resolved_at':rss_date(resolved[1]) if resolved else None,'updated_at':max(filter(None,map(rss_date,dates)),default=rss_date(item.findtext('pubDate'))),'component_ids':[comp] if comp else [],'url':safe_url(item.findtext('link'),self.origin),'updates':[{'id':text(item.findtext('guid'),256)+':feed','state':status,'display_at':max(filter(None,map(rss_date,dates)),default=None),'body':text(body,10000)}]})
        return {'overall':{'state':'unknown','summary':'RSS incident history available; current availability and severity are not asserted.'},'components':list(components.values()),'incidents':incidents,'source_updated_at':max((i['updated_at'] for i in incidents if i['updated_at']),default=None),'warnings':['Components are discovered from published incidents, not a complete current inventory. RSS severity is not authoritative.']}

    def _html(self,raw):
        visible = plain(raw.decode('utf-8', errors='strict'))
        # A JS application shell cannot substantiate an operational status.
        if 'All Systems Operational' not in visible:
            raise SourceError('unsupported','Official page does not expose supported server-rendered status; open official source.')
        labels = ('API','Multimodal Live API','Google AI Studio')
        if not all(x in visible for x in labels): raise SourceError('parse_error','AI Studio component layout changed')
        return {'overall':{'state':'operational','summary':'Official HTML overview'},'components':[{'id':x.lower().replace(' ','_'),'name':x,'state':'unknown','showcase':True} for x in labels],'incidents':[],'warnings':['HTML overview only; per-component traffic class, incident history and timezone cannot be safely reconstructed.']}

ADAPTERS = [
 Adapter('github','github','GitHub','https://www.githubstatus.com/','statuspage'),
 Adapter('openai','openai','ChatGPT / OpenAI','https://status.openai.com/','openai',warning='Aggregate official status. All component IDs remain distinct; API/ChatGPT grouping is not inferred from duplicate names.'),
 Adapter('anthropic','anthropic','Claude / Anthropic','https://status.claude.com/','statuspage'),
 Adapter('cursor','cursor','Cursor','https://status.cursor.com/','statuspage'),
 Adapter('xai','xai','Grok / xAI','https://status.x.ai/','rss',1800),
 Adapter('google_workspace_gemini','google','Gemini · Workspace','https://www.google.com/appsstatus/dashboard/','google',300,'npdyhgECDJ6tB66MxXyo','Workspace scope; does not establish availability for all consumer Gemini users.'),
 Adapter('google_ai_studio','google','Gemini · AI Studio / API','https://aistudio.google.com/status','html',900,warning='Experimental public HTML adapter. No internal RPC or credentials.'),
 Adapter('google_cloud_gemini','google','Gemini · Google Cloud','https://status.cloud.google.com/','google',300,'Z0FZJAMvEB4j3NbCJs6B','Public broad incidents only; project-specific Personalized Service Health is outside this unauthenticated MVP.'),
]
