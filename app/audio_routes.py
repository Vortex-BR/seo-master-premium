"""Persist connection observations without keeping proxy credentials in diagnostics."""
import time

from . import db, transcripts


SUCCESS_TTL = 1800


def key(proxy):
    return transcripts.route_key('youtube_audio', proxy or 'direct')


def record(proxy, outcome):
    column = {'attempt': 'last_attempt', 'success': 'last_success', 'failure': 'last_failure'}[outcome]
    # Column is selected only from this internal allowlist.
    with db.connect() as conn:
        conn.execute(f'INSERT INTO audio_route_history (key,{column}) VALUES (?,?) '
                     f'ON CONFLICT(key) DO UPDATE SET {column}=excluded.{column}', (key(proxy), time.time()))


def ordered(routes):
    """Recent successes first; otherwise try the least recently attempted route."""
    with db.connect() as conn:
        history = {row['key']: dict(row) for row in conn.execute('SELECT * FROM audio_route_history')}
    now = time.time()
    def priority(proxy):
        if proxy is None:
            return (-1, 0)  # Honor auto mode's explicit direct-first contract.
        row = history.get(key(proxy), {})
        success = row.get('last_success', 0)
        if success > row.get('last_failure', 0) and now - success < SUCCESS_TTL:
            return (0, -success)
        return (1, row.get('last_attempt', 0))
    return sorted(routes, key=priority)
