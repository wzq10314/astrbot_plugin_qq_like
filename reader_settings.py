"""Validate reader links against the administrator's configured public URL."""
import re
from urllib.parse import urlsplit


def validate_reader_url(url, settings):
    base = urlsplit(str(settings.get('public_base', '')).rstrip('/'))
    result = urlsplit(str(url))
    if (base.scheme != 'https' or not base.hostname or base.username or base.password
            or base.query or base.fragment):
        raise ValueError('Configure reader-client.json public_base with your HTTPS reader URL')
    if (result.scheme != 'https' or result.netloc != base.netloc
            or result.username or result.password or result.query or result.fragment):
        raise ValueError('Unexpected reader URL')
    prefix = base.path.rstrip('/') + '/r/'
    if not result.path.startswith(prefix) or not re.fullmatch(r'[A-Za-z0-9_-]{32}/', result.path[len(prefix):]):
        raise ValueError('Unexpected reader path')
    return url
