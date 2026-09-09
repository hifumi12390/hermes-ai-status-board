"""Small synthetic provider responses; no captured traffic or real incident text.

The Google product IDs are public identifiers required by their adapters.
All incident IDs, component examples, descriptions and dates below are test data.
"""
import json


def encoded(value):
    return json.dumps(value).encode('utf-8')


def component(id_, name):
    return {'id': id_, 'name': name, 'status': 'operational',
            'showcase': True, 'updated_at': '2026-01-01T00:00:00Z'}


def incident():
    return {
        'id': 'synthetic-incident', 'name': 'Example resolved incident',
        'status': 'resolved', 'impact': 'minor',
        'started_at': '2026-01-01T00:00:00Z',
        'resolved_at': '2026-01-01T00:30:00Z',
        'updated_at': '2026-01-01T00:30:00Z', 'components': [],
        'incident_updates': [{'id': 'synthetic-update', 'status': 'resolved',
                              'body': 'Synthetic service recovery for parser testing.',
                              'display_at': '2026-01-01T00:30:00Z'}],
    }


def statuspage():
    return {
        'page': {'updated_at': '2026-01-01T00:30:00Z'},
        'status': {'indicator': 'none', 'description': 'All Systems Operational'},
        'components': [component('example-api', 'Example API'),
                       component('example-web', 'Example Web')],
        'incidents': [], 'scheduled_maintenances': [],
    }


XAI = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Synthetic RSS fixture</title><ttl>30</ttl>
<item><guid>synthetic-rss-incident</guid><title>[Example API] Test incident</title>
<link>https://status.x.ai/example-api</link>
<pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate>
<description><![CDATA[<p>Status: RESOLVED</p>
<p>Resolved: Thu, 01 Jan 2026 00:30:00 GMT</p>
<p>Synthetic incident for tests only.</p>]]></description></item>
</channel></rss>'''

OPENAI_FEED = b'''<rss version="2.0"><channel><title>Synthetic RSS fixture</title>
<item><guid>synthetic-feed-update</guid><title>Example feed update</title>
<link>https://status.openai.com/</link>
<pubDate>Thu, 01 Jan 2026 00:30:00 GMT</pubDate>
<description>Synthetic supplementary event.</description></item>
</channel></rss>'''


def bundle(surface):
    """Return fresh raw responses so tests can mutate them independently."""
    if surface in ('github', 'anthropic', 'cursor'):
        return {'current': encoded(statuspage()),
                'history': encoded({'incidents': [incident()]}),
                'maintenance': encoded({'scheduled_maintenances': []})}
    if surface == 'openai':
        current = statuspage()
        # The overview is intentionally incomplete. Duplicate names have distinct IDs.
        current['components'] = [component('example-login-a', 'Login')]
        return {'current': encoded(current),
                'components': encoded({'components': [component('example-login-a', 'Login'),
                                                       component('example-login-b', 'Login'),
                                                       component('example-api', 'API')]}),
                'history': encoded({'incidents': [incident()]}), 'feed': OPENAI_FEED}
    if surface == 'xai':
        return {'current': XAI}
    if surface in ('google_workspace_gemini', 'google_cloud_gemini'):
        product = ('npdyhgECDJ6tB66MxXyo' if surface == 'google_workspace_gemini'
                   else 'Z0FZJAMvEB4j3NbCJs6B')
        return {'current': encoded([]),
                'products': encoded({'products': [{'id': product, 'title': 'Example Gemini surface'}]})}
    if surface == 'google_ai_studio':
        return {'current': b'<html><body><div id="app"></div><script>/* Example application shell. */</script></body></html>'}
    raise KeyError(surface)
