"""Additive execution telemetry and offline, source-file read-only cost reports.

The reservation ledger remains the spending authority. This module never calls a
provider, claims a provider invoice, or manufactures missing historical run IDs.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from . import db


SCHEMA_VERSION = 1
MIN_PERCENTILE_SAMPLE = 20
_execution = ContextVar('seo_cost_execution', default=None)
NOTICE = ('Custos calculados usam a tarifa registrada, sem margem de reserva, e não são fatura. '
          'Histórico sem identidade não representa uma geração atual. Valores ausentes permanecem null. '
          'Infraestrutura, trabalho humano e serviços sem tarifa não são considerados gratuitos.')


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS cost_runs '
              '(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, data TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS cost_runs_entity ON cost_runs(entity_id)')
    c.execute('CREATE TABLE IF NOT EXISTS cost_events '
              '(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, run_id TEXT, data TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS cost_events_run ON cost_events(run_id)')


def current():
    """Metadata only: no persistence or application initialization."""
    return dict(_execution.get() or {})


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str, allow_nan=False).encode()).hexdigest()


@contextmanager
def run(job, *, scope='article', operation='generation', dependency_fingerprint=None,
        pipeline_version=None, metadata=None):
    """One invocation, including retries inside it; resuming starts another run.

    A new run does not renew the entity's durable spending allowance. The caller
    owns its operation's terminal status; status here describes the invocation.
    """
    ident = uuid.uuid4().hex
    parent = current()
    record = {'id': ident, 'run_id': ident, 'execution_id': ident,
              'entity_id': job['id'], 'scope': scope, 'operation': operation,
              'parent_run_id': parent.get('id'), 'pipeline_version': pipeline_version,
              'dependency_fingerprint': dependency_fingerprint,
              'started_at': db.now(), 'finished_at': None, 'duration_seconds': None,
              'status': 'running', 'metadata': metadata or {}}
    with db.connect() as c:
        init(c)
        c.execute('INSERT INTO cost_runs VALUES (?,?,?)',
                  (ident, job['id'], json.dumps(record, ensure_ascii=False, allow_nan=False)))
    token = _execution.set(record)
    started = time.monotonic()
    try:
        yield record
        outcome = str(record.get('outcome', '')).lower()
        failures = ('error', 'failed', 'budget', 'budget_exceeded', 'budget_exhausted',
                    'sources_unavailable', 'transcription_pending', 'needs_input', 'awaiting_key')
        record['status'] = ('cancelled' if outcome in ('cancelled', 'interrupted') else
                            'failed' if outcome in failures else 'completed')
    except BaseException as exc:
        record['status'] = ('cancelled' if isinstance(exc, (KeyboardInterrupt, GeneratorExit))
                            or type(exc).__name__ == 'CancelledError' else 'failed')
        # Exception messages may include a provider credential or source content.
        record['error_type'] = type(exc).__name__
        raise
    finally:
        _execution.reset(token)
        record.update(finished_at=db.now(), duration_seconds=max(0, time.monotonic() - started))
        with db.connect() as c:
            c.execute('UPDATE cost_runs SET data=? WHERE id=?',
                      (json.dumps(record, ensure_ascii=False, allow_nan=False), ident))


def _event(job, stage, values):
    context = current()
    ident = uuid.uuid4().hex
    entity_id = job['id'] if job else context.get('entity_id') or 'external:' + ident
    row = {'id': ident, 'attempt_id': ident, 'entity_id': entity_id,
           'run_id': context.get('id'), 'execution_id': context.get('id'),
           'scope': context.get('scope', 'unassigned'), 'stage': stage,
           'created_at': db.now(), 'pipeline_version': context.get('pipeline_version'),
           'dependency_fingerprint': context.get('dependency_fingerprint'), **values}
    with db.connect() as c:
        init(c)
        c.execute('INSERT INTO cost_events VALUES (?,?,?,?)',
                  (ident, entity_id, row['run_id'], json.dumps(row, ensure_ascii=False, allow_nan=False)))
    return row


def record_cache(job, stage, *, provider='application', origin='application_cache',
                 dependency_fingerprint=None, metadata=None):
    return _event(job, stage, {'provider': provider, 'origin': origin, 'state': 'reused',
                             'calculated_usd': 0.0, 'estimated_usd': None,
                             'infrastructure_usd': None, 'duration_seconds': None,
                             'dependency_fingerprint': dependency_fingerprint,
                             'metadata': metadata or {}})


def record_external(job, stage, *, provider, origin='external', amount_usd=None,
                    duration_seconds=None, metadata=None):
    for value in (amount_usd, duration_seconds):
        if value is not None and not _number(value):
            raise ValueError('Custos e durações externos devem ser finitos, não negativos ou null.')
    return _event(job, stage, {'provider': provider, 'origin': origin,
                             'state': 'registered' if amount_usd is not None else 'unmeasured',
                             'registered_usd': amount_usd, 'calculated_usd': None,
                             'estimated_usd': None, 'duration_seconds': duration_seconds,
                             'metadata': metadata or {}})


@contextmanager
def external_attempt(job, stage, *, provider, origin='external', dependency_fingerprint=None,
                     metadata=None):
    """Persist optional/local provider work before dispatch, without invented prices."""
    event = _event(job, stage, {'provider': provider, 'origin': origin,
                               'state': 'unmeasured_pending', 'registered_usd': None,
                               'calculated_usd': None, 'estimated_usd': None,
                               'duration_seconds': None, 'finished_at': None,
                               'dependency_fingerprint': dependency_fingerprint,
                               'metadata': metadata or {}})
    started = time.monotonic()
    try:
        yield event
        if event['state'] == 'unmeasured_pending':
            event['state'] = 'unmeasured'
    except BaseException as exc:
        event.update(state='uncertain', error_type=type(exc).__name__)
        raise
    finally:
        event.update(finished_at=db.now(), duration_seconds=max(0, time.monotonic() - started))
        with db.connect() as c:
            c.execute('UPDATE cost_events SET data=? WHERE id=?',
                      (json.dumps(event, ensure_ascii=False, allow_nan=False), event['id']))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


@contextmanager
def readonly_snapshot(database):
    """Copy stable database/WAL bytes, then let SQLite recover only the clone.

    Even opening a SQLite WAL database with mode=ro can touch its shared-memory
    sidecar. The original is therefore never opened with SQLite. Copying is
    retried if any DB/WAL bytes change; a busy source produces an explicit error.
    No -shm is copied because it is transient and SQLite rebuilds it in isolation.
    """
    source = Path(database).resolve()
    if not source.is_file():
        raise ValueError('Banco inexistente. Nenhum banco foi criado.')
    paths = (source, Path(str(source) + '-wal'), Path(str(source) + '-journal'))
    def capture():
        return {p.name: p.read_bytes() for p in paths if p.is_file()}
    captured = None
    for _ in range(4):
        try:
            first, second = capture(), capture()
        except FileNotFoundError:
            continue
        if first == second and source.name in first:
            captured = first
            break
    if captured is None:
        raise ValueError('O banco/WAL mudou durante a cópia. Repita a auditoria em uma janela estável.')
    with tempfile.TemporaryDirectory(prefix='seo-cost-audit-') as directory:
        target = Path(directory) / source.name
        for name, content in captured.items():
            (Path(directory) / name).write_bytes(content)
        connection = sqlite3.connect(target, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        try:
            connection.execute('BEGIN')
            if connection.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok':
                raise ValueError('A cópia do banco/WAL não passou na verificação de integridade.')
            yield connection, {name: hashlib.sha256(content).hexdigest()
                               for name, content in captured.items()}
        finally:
            connection.close()


def _table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def _rows(c, table, entity_id=None):
    if not _table(c, table):
        return []
    column = 'job_id' if table == 'spend_reservations' else 'entity_id'
    query = f'SELECT {column} AS entity_id,data FROM {table}'
    args = ()
    if entity_id is not None:
        query += f' WHERE {column}=?'
        args = (entity_id,)
    result = []
    for row in c.execute(query, args):
        payload = json.loads(row['data'])
        if not isinstance(payload, dict):
            raise ValueError('O relatório encontrou telemetria inválida; os dados originais foram preservados.')
        payload['entity_id'] = row['entity_id']
        result.append(payload)
    return result


def _costs(attempts):
    paid = [row for row in attempts if row.get('state') != 'reused'
            and row.get('state') != 'released']
    complete = bool(attempts) and all(_number(row.get('calculated_usd')) for row in paid)
    measured_complete = bool(attempts) and all(any(_number(row.get(field)) for field in
                                                  ('registered_usd', 'calculated_usd')) for row in paid)
    calculated = [row['calculated_usd'] for row in attempts if _number(row.get('calculated_usd'))]
    estimated = [row['estimated_usd'] for row in attempts if _number(row.get('estimated_usd'))]
    registered = [row['registered_usd'] for row in attempts if _number(row.get('registered_usd'))]
    total_measured = sum(row['registered_usd'] if _number(row.get('registered_usd')) else
                         row.get('calculated_usd', 0) or 0 for row in attempts)
    pending = [row for row in attempts if row.get('state') in ('reserved', 'uncertain')]
    token_rows = [row for row in paid if (row.get('pricing_snapshot') or {}).get('family') in ('text', 'image')
                  or str(row.get('model', '')).startswith('gpt-')
                  or any(type((row.get('usage') or {}).get(field)) is int
                         for field in ('input_tokens', 'output_tokens'))]
    cached_values = [(row.get('usage') or {}).get('cached_input_tokens') for row in token_rows]
    known_cached = [value for value in cached_values if type(value) is int and value >= 0]
    return {'calculated_usd': round(sum(calculated), 6) if complete else None,
            'known_calculated_usd': round(sum(calculated), 6) if calculated else None,
            'estimated_usd': round(sum(estimated), 6) if estimated and len(estimated) == len(paid) else None,
            'known_estimated_usd': round(sum(estimated), 6) if estimated else None,
            'registered_usd': round(sum(registered), 6) if registered else None,
            'total_measured_usd': round(total_measured, 6) if measured_complete else None,
            'reserved_usd': round(sum(row.get('reserved_usd', 0) for row in pending), 6),
            'inconclusive_reserve_usd': round(sum(row.get('reserved_usd', 0) for row in pending
                                                 if row.get('state') == 'uncertain'), 6),
            'unmeasured_events': sum(not any(_number(row.get(key)) for key in
                                            ('calculated_usd', 'estimated_usd', 'registered_usd'))
                                     and row.get('state') != 'released' for row in paid),
            'application_cache_hits': sum(row.get('state') == 'reused' for row in attempts),
            'provider_cached_input_tokens': sum(known_cached) if token_rows and len(known_cached) == len(token_rows) else None,
            'known_provider_cached_input_tokens': sum(known_cached) if known_cached else None,
            'provider_cache_coverage': {'applicable_calls': len(token_rows), 'measured_calls': len(known_cached)},
            'invoice_usd': None, 'infrastructure_usd': None, 'human_review_usd': None}


def _percentiles(values):
    known = sorted(value for value in values if _number(value))
    result = {'sample_size': len(known), 'minimum_sample': MIN_PERCENTILE_SAMPLE,
              'p50_usd': None, 'p95_usd': None}
    if len(known) >= MIN_PERCENTILE_SAMPLE:
        # Explicit nearest-rank estimator, without extrapolating tiny samples.
        result.update(p50_usd=known[math.ceil(.5 * len(known)) - 1],
                      p95_usd=known[math.ceil(.95 * len(known)) - 1])
    return result


def _duration_percentiles(values):
    values = sorted(value for value in values if _number(value))
    result = {'sample_size': len(values), 'minimum_sample': MIN_PERCENTILE_SAMPLE,
              'p50_seconds': None, 'p95_seconds': None}
    if len(values) >= MIN_PERCENTILE_SAMPLE:
        result.update(p50_seconds=values[math.ceil(.5 * len(values)) - 1],
                      p95_seconds=values[math.ceil(.95 * len(values)) - 1])
    return result


def _metrics(executions):
    eligible = [row for row in executions if row.get('status') == 'completed']
    metrics = _percentiles([row['costs']['total_measured_usd'] for row in eligible])
    dates = [row['started_at'] for row in executions if row.get('started_at')]
    stages, durations = {}, {}
    for execution in eligible:
        for stage, values in execution['by_stage'].items():
            stages.setdefault(stage, []).append(values['total_measured_usd'])
        for stage in execution['by_stage']:
            rows = [row for row in execution['attempts'] if row.get('stage') == stage]
            seconds = [row.get('duration_seconds') for row in rows]
            durations.setdefault(stage, []).append(sum(seconds) if seconds and all(_number(value) for value in seconds) else None)
    metrics.update(execution_count=len(executions),
                   window={'from': min(dates) if dates else None, 'to': max(dates) if dates else None},
                   pipeline_versions=sorted({str(row['pipeline_version']) for row in executions
                                             if row.get('pipeline_version') is not None}),
                   estimator='nearest_rank',
                   duration=_duration_percentiles([row.get('duration_seconds') for row in eligible]),
                   by_stage={stage: {**_percentiles(values),
                                     'duration': _duration_percentiles(durations.get(stage, []))}
                             for stage, values in stages.items()})
    return metrics


def read_report(database, *, job_id=None, run_id=None):
    """Read-only projection, usable from the CLI or a GET endpoint."""
    from . import spending
    with readonly_snapshot(database) as (connection, hashes):
        runs = _rows(connection, 'cost_runs', job_id)
        ledger = _rows(connection, 'spend_reservations', job_id)
        events = _rows(connection, 'cost_events', job_id)
        tracked = {row.get('response_id') for row in ledger if row.get('response_id')}
        tracked_ids = {row['id'] for row in ledger}
        tracked_images = {row.get('image_task_id') for row in ledger if row.get('image_task_id')}
        legacy = []
        for table, scope in (('jobs', 'article'), ('strategy_cycles', 'strategy_cycle')):
            if not _table(connection, table):
                continue
            query = f'SELECT data FROM {table}' + (' WHERE id=?' if job_id is not None else '')
            for dbrow in connection.execute(query, (job_id,) if job_id is not None else ()):
                entity = json.loads(dbrow['data'])
                seen = set()
                for usage in entity.get('usage', []):
                    key = usage.get('response_id') or usage.get('image_task_id') or fingerprint(usage)
                    if (key in seen or usage.get('response_id') in tracked
                            or usage.get('reservation_id') in tracked_ids
                            or usage.get('spend_reservation_id') in tracked_ids
                            or usage.get('image_task_id') in tracked_images):
                        continue
                    seen.add(key)
                    recorded = usage.get('estimated_usd')
                    estimate = recorded if _number(recorded) else spending.calculated_cost(usage, usage.get('model', ''))
                    legacy.append({'entity_id': entity['id'], 'scope': scope,
                                   'run_id': None, 'execution_id': None,
                                   'stage': usage.get('stage'), 'model': usage.get('model'),
                                   'origin': 'historical', 'state': 'legacy_estimate',
                                   'estimated_usd': float(estimate) if estimate is not None else None,
                                   'calculated_usd': None, 'registered_usd': None,
                                   'pricing_basis': 'recorded_local_estimate_with_possible_margin' if _number(recorded)
                                                    else 'current_tariff_projection_not_historical_invoice',
                                   'usage': usage, 'created_at': usage.get('created_at')})
        for row in ledger:
            if not row.get('run_id'):
                # Legacy charged_usd includes a safety margin; expose its meaning
                # without retroactively turning it into a provider calculation.
                row = {**row, 'guard_accounted_usd': row.get('charged_usd'),
                       'calculated_usd': None, 'origin': row.get('origin', 'legacy_ledger')}
                legacy.append(row)
        executions = []
        all_attempts = ledger + events
        for record in runs:
            if run_id is not None and record['id'] != run_id:
                continue
            attempts = [row for row in all_attempts if row.get('run_id') == record['id']]
            groups = {}
            for row in attempts:
                groups.setdefault(row.get('stage', 'unknown'), []).append(row)
            stage_costs = {stage: _costs(rows) for stage, rows in groups.items()}
            # Attempts beyond the first request with the same dependency and
            # stage are retries; separate intentional differently-shaped calls.
            seen = set()
            retries = []
            for row in attempts:
                key = (row.get('stage'), row.get('dependency_fingerprint'))
                if row.get('dependency_fingerprint') and key in seen and row.get('state') != 'reused':
                    retries.append(row)
                seen.add(key)
            executions.append({**record, 'attempts': attempts, 'costs': _costs(attempts),
                               'by_stage': stage_costs, 'retry_costs': _costs(retries),
                               'retry_attempts': len(retries),
                               'review_costs': _costs([row for row in attempts
                                                      if 'review' in str(row.get('stage', ''))
                                                      or 'audit' in str(row.get('stage', ''))]),
                               'efficiency': None})
        cohorts = {}
        for execution in executions:
            key = (f"{execution.get('scope', 'unassigned')}:{execution.get('pipeline_version') or 'unknown'}"
                   f":{execution.get('operation') or 'unknown'}")
            cohorts.setdefault(key, []).append(execution)
        cohort_metrics = {key: {'scope': rows[0].get('scope'),
                                'operation': rows[0].get('operation'),
                                'pipeline_version': rows[0].get('pipeline_version'), **_metrics(rows)}
                          for key, rows in cohorts.items()}
        metrics = _metrics(executions)
        if len(cohorts) > 1:
            metrics.update(p50_usd=None, p95_usd=None, by_stage={},
                           duration={'sample_size': 0, 'minimum_sample': MIN_PERCENTILE_SAMPLE,
                                     'p50_seconds': None, 'p95_seconds': None})
        metrics.update(aggregation='single_cohort' if len(cohorts) <= 1 else 'cross_scope_version_or_operation_no_percentiles',
                       cohorts=cohort_metrics)
        unassigned = [row for row in events if not row.get('run_id')]
        return {'schema_version': SCHEMA_VERSION, 'executions': executions,
                'legacy': legacy if run_id is None else [], 'legacy_costs': _costs(legacy if run_id is None else []),
                'unassigned_events': unassigned if run_id is None else [],
                'unassigned_costs': _costs(unassigned if run_id is None else []),
                'metrics': metrics, 'source_sha256': hashes, 'notice': NOTICE,
                'generated_at': db.now()}


def recover_inflight():
    """Startup-only recovery after exclusive ownership of the application process.

    A process crash cannot prove non-billing. Holds remain unchanged; interrupted
    durations stay unknown. This never initializes or creates telemetry tables.
    """
    path = Path(os.getenv('DATA_DIR', './data')) / 'seo.sqlite3'
    counts = {'reservations': 0, 'runs': 0, 'external_events': 0, 'invalid_rows': 0}
    if not path.is_file():
        return counts
    timestamp = db.now()
    transitions = (('spend_reservations', 'reserved', 'uncertain', 'reservations'),
                   ('cost_runs', 'running', 'interrupted', 'runs'),
                   ('cost_events', 'unmeasured_pending', 'uncertain', 'external_events'))
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for table, before, after, key in transitions:
            if not _table(connection, table):
                continue
            for dbrow in connection.execute(f'SELECT id,data FROM {table}').fetchall():
                try:
                    row = json.loads(dbrow['data'])
                    if not isinstance(row, dict):
                        raise ValueError('Telemetry row is not an object.')
                    field = 'status' if table == 'cost_runs' else 'state'
                    if row.get(field) != before:
                        continue
                    row.update({field: after, 'recovered_at': timestamp,
                                'recovery_reason': 'process_restart_billing_unknown',
                                'duration_seconds': None})
                    connection.execute(f'UPDATE {table} SET data=? WHERE id=?',
                                       (json.dumps(row, ensure_ascii=False, allow_nan=False), dbrow['id']))
                    counts[key] += 1
                except (ValueError, TypeError):
                    counts['invalid_rows'] += 1
    return counts
