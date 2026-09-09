"""Capture only public, fixed official endpoints; no authentication or cookies."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import httpx

URLS = {
 'github_summary':'https://www.githubstatus.com/api/v2/summary.json',
 'github_history':'https://www.githubstatus.com/api/v2/incidents.json',
 'github_maintenance':'https://www.githubstatus.com/api/v2/scheduled-maintenances.json',
 'openai_status':'https://status.openai.com/api/v2/status.json',
 'openai_components':'https://status.openai.com/api/v2/components.json',
 'openai_history':'https://status.openai.com/api/v2/incidents.json',
 'openai_feed':'https://status.openai.com/history.rss',
 'anthropic_summary':'https://status.claude.com/api/v2/summary.json',
 'anthropic_history':'https://status.claude.com/api/v2/incidents.json',
 'anthropic_maintenance':'https://status.claude.com/api/v2/scheduled-maintenances.json',
 'cursor_summary':'https://status.cursor.com/api/v2/summary.json',
 'cursor_history':'https://status.cursor.com/api/v2/incidents.json',
 'cursor_maintenance':'https://status.cursor.com/api/v2/scheduled-maintenances.json',
 'xai_feed':'https://status.x.ai/feed.xml',
 'workspace_products':'https://www.google.com/appsstatus/dashboard/products.json',
 'workspace_history':'https://www.google.com/appsstatus/dashboard/incidents.json',
 'cloud_products':'https://status.cloud.google.com/products.json',
 'cloud_history':'https://status.cloud.google.com/incidents.json',
 'studio_html':'https://aistudio.google.com/status',
}

def capture(item):
    name, url = item
    root = Path(__file__).parent / 'tests' / 'fixtures' / 'live'
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
        response = client.get(url, headers={'User-Agent':'AI-Status-Board/0.1 public-status-monitor'})
    suffix = '.json' if url.endswith('.json') else '.xml' if url.endswith(('.rss','.xml')) else '.html'
    (root / (name + suffix)).write_bytes(response.content)
    meta = {'url':url, 'status':response.status_code, 'headers':{k:v for k,v in response.headers.items() if k in ('date','age','etag','last-modified','content-type','retry-after')}, 'sha256':hashlib.sha256(response.content).hexdigest()}
    (root / (name + '.meta.json')).write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return name, response.status_code, len(response.content)

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(capture, URLS.items()): print(*result)
