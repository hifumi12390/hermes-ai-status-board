"""Versioned common contract. External values are bounded and rendered as text."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re

STATES = ('unknown', 'operational', 'informational', 'maintenance', 'degraded', 'partial_outage', 'major_outage')
ERRORS = ('source_unavailable', 'parse_error', 'auth_required', 'rate_limited', 'unsupported')
MAPPING = {'none':'operational','minor':'degraded','major':'partial_outage','critical':'major_outage',
           'degraded_performance':'degraded','under_maintenance':'maintenance', **{x:x for x in STATES}}

class SourceError(Exception):
    def __init__(self, kind, message, retry_after=0):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after

def text(value, limit=2000):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or ''))[:limit]

def stamp(value):
    if value is None or value == '': return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None: return None
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    except (ValueError, TypeError): return None

def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')

def seconds(value):
    normalized = stamp(value)
    return datetime.fromisoformat(normalized.replace('Z','+00:00')).timestamp() if normalized else None

def state(value):
    return MAPPING.get(value, 'unknown')

def worst(values):
    values = list(values)
    known = [v for v in values if v in STATES and v != 'unknown']
    bad = [v for v in known if v != 'operational']
    return max(bad, key=STATES.index) if bad else ('unknown' if not values or 'unknown' in values else 'operational')

def safe_url(value, origin):
    try:
        p, base = urlsplit(value or ''), urlsplit(origin)
        return value if p.scheme == 'https' and p.hostname == base.hostname and not p.username and not p.password else origin
    except ValueError: return origin

def require(value, typ, message):
    if not isinstance(value, typ): raise SourceError('parse_error', message)
    return value
