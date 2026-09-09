"""Bounded HTTPS transport, conditional GET and explicit source failures."""
import hashlib
import time
from email.utils import parsedate_to_datetime
import httpx
from .model import SourceError

MAX_BODY=4*1024*1024

class Transport:
    def __init__(self,allowlist,client=None,clock=time.time):
        self.allowlist=set(allowlist)
        self.client=client or httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False)
        self.cache={}
        self.clock=clock

    def get(self,url):
        if url not in self.allowlist: raise SourceError('unsupported','URL is outside the fixed endpoint allowlist')
        headers={'User-Agent':'AI-Status-Board/0.1 public-status-monitor','Accept':'application/json, application/rss+xml, text/html;q=0.8'}
        cached=self.cache.get(url)
        if cached:
            if cached[1].get('etag'): headers['If-None-Match']=cached[1]['etag']
            if cached[1].get('last-modified'): headers['If-Modified-Since']=cached[1]['last-modified']
        try:
            with self.client.stream('GET',url,headers=headers) as r:
                metadata={k:v for k,v in r.headers.items() if k in ('date','age','etag','last-modified','retry-after','content-type')}
                metadata['http_status']=r.status_code
                if r.status_code==304:
                    if not cached: raise SourceError('parse_error','304 without a validated cache')
                    merged={**cached[1],**metadata}; body=cached[0]
                elif r.status_code==429:
                    raw=r.headers.get('retry-after','')
                    try: wait=float(raw)
                    except ValueError:
                        try: wait=parsedate_to_datetime(raw).timestamp()-self.clock()
                        except (ValueError,TypeError): wait=0
                    raise SourceError('rate_limited','Official source returned HTTP 429',min(86400,max(0,wait)))
                elif r.status_code in (401,403): raise SourceError('auth_required','Official source refused anonymous access (HTTP '+str(r.status_code)+')')
                elif r.status_code in (404,410): raise SourceError('unsupported','Official endpoint is unavailable (HTTP '+str(r.status_code)+')')
                elif r.status_code!=200: raise SourceError('source_unavailable','Official source returned HTTP '+str(r.status_code))
                else:
                    chunks=[]; size=0
                    for part in r.iter_bytes():
                        size+=len(part)
                        if size>MAX_BODY: raise SourceError('parse_error','Response exceeds 4 MiB limit')
                        chunks.append(part)
                    body=b''.join(chunks); merged=metadata
                try: age=float(merged.get('age',0))
                except ValueError: raise SourceError('parse_error','Invalid HTTP Age')
                if age>3600: raise SourceError('source_unavailable','Source cache is more than one hour old')
                merged['sha256']=hashlib.sha256(body).hexdigest()
                self.cache[url]=(body,merged)
                return body,merged
        except httpx.TimeoutException: raise SourceError('source_unavailable','Official source timed out')
        except httpx.HTTPError: raise SourceError('source_unavailable','Official source connection failed')

    def invalidate(self,urls):
        for url in urls: self.cache.pop(url,None)
    def close(self): self.client.close()
