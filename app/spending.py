"""Durable entity-wide USD reservations shared by text, search, audio and images.

Tariffs declared in the preexisting ledger on 2026-10-09 are frozen per attempt;
pricing_snapshot labels which were reverified. This conservative application
ledger is separate from provider billing. Unknown prices fail before dispatch.
"""
import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from . import db


class SpendLimitExceeded(ValueError):
    pass


# Input / cached input / output USD per million tokens; context ceiling.
TEXT_RATES = {
    'gpt-4.1-mini': (.4, .1, 1.6, 1047576),
    'gpt-4.1-nano': (.1, .025, .4, 1047576),
    'gpt-4.1': (2, .5, 8, 1047576),
    'gpt-4o-mini': (.15, .075, .6, 128000),
    'gpt-4o': (2.5, 1.25, 10, 128000),
    'gpt-4o-2024-05-13': (5, 5, 15, 128000),
}
IMAGE_RATES = {
    'gpt-image-2': (5, 8, 30), 'gpt-image-1.5': (5, 8, 32),
    'gpt-image-1': (5, 10, 40), 'gpt-image-1-mini': (2, 2.5, 8),
    'chatgpt-image-latest': (5, 8, 32),
}
SAFETY = Decimal('1.15')
DEFAULT_LIMIT = Decimal('1')
PRICING_VERSION = 'openai-standard-2026-10-09'
NOTICE = ('Controle conservador em US$: inclui todas as rodadas deste artigo, pesquisas, '
          'retentativas e imagens. Não é o extrato da OpenAI. Valores sem confirmação '
          'continuam reservados; o orçamento não é renovado ao retomar.')


def amount(value):
    return Decimal(str(value)).quantize(Decimal('.000001'), rounding=ROUND_CEILING)


def canonical(model, prices):
    if not isinstance(model, str):
        raise SpendLimitExceeded('O modelo não informa uma tarifa válida. Nenhuma chamada foi enviada.')
    if model in prices:
        return model
    for name in sorted(prices, key=len, reverse=True):
        if re.fullmatch(re.escape(name) + r'-\d{4}-\d{2}-\d{2}', model):
            return name
    raise SpendLimitExceeded('O modelo não tem uma tarifa validada no controle financeiro. '
                             'Nenhuma chamada foi enviada. Escolha um modelo com tarifa cadastrada.')


def limit(job, c):
    # Use the same transaction. store.profile() also writes a snapshot and would
    # deadlock a second writer while reserve holds BEGIN IMMEDIATE.
    row = c.execute('SELECT value FROM settings WHERE key=?', ('editorial_profile',)).fetchone()
    profile = json.loads(row['value']) if row else {}
    value = job.get('financial_budget_usd', profile.get('max_spend_usd', DEFAULT_LIMIT))
    try:
        result = amount(value)
        if result.is_finite() and result > 0:
            return result
    except (ValueError, ArithmeticError):
        pass
    raise SpendLimitExceeded('O orçamento financeiro configurado é inválido. Nenhuma chamada foi enviada.')


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS spend_reservations '
              '(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, data TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS spend_reservations_job ON spend_reservations(job_id)')


def pricing_snapshot(model):
    """Freeze rates used by this attempt; the provider invoice remains separate."""
    for family, prices in (('text', TEXT_RATES), ('image', IMAGE_RATES)):
        try:
            name = canonical(model, prices)
            return {'version': PRICING_VERSION, 'provider': 'openai', 'service_tier': 'default',
                    'declared_at': '2026-10-09', 'family': family, 'model': name,
                    'rates': list(prices[name]), 'unit': 'usd_per_million_tokens',
                    'reservation_safety_multiplier': float(SAFETY),
                    'web_search_usd_per_call': .01 if family == 'text' else None,
                    'web_search_fixed_input_tokens': 8000 if name in ('gpt-4.1-mini', 'gpt-4o-mini') else 0,
                    'verification': 'verified_official_2026-10-10' if name == 'gpt-4.1-mini'
                                    else 'preexisting_declared_tariff',
                    'source': 'https://developers.openai.com/api/docs/models/gpt-4.1-mini'
                              if name == 'gpt-4.1-mini' else 'https://developers.openai.com/api/docs/pricing'}
        except SpendLimitExceeded:
            continue
    if model == 'whisper-1':
        return {'version': PRICING_VERSION, 'provider': 'openai', 'family': 'audio',
                'model': model, 'usd_per_minute': .006, 'declared_at': '2026-10-09',
                'verification': 'verified_official_2026-10-10',
                'source': 'https://developers.openai.com/api/docs/models/whisper-1'}
    return {'version': None, 'model': model, 'provider': 'unknown', 'verification': 'unmeasured'}


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def calculated_cost(usage, model, snapshot=None):
    """Tariff calculation without reservation margin; incomplete billing data is null."""
    snapshot = snapshot or pricing_snapshot(model)
    if snapshot.get('family') == 'audio':
        duration = usage.get('provider_duration_seconds')
        if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
            return amount(Decimal(str(duration)) * amount(snapshot['usd_per_minute']) / 60)
        return None
    incoming, outgoing = _tokens(usage.get('input_tokens')), _tokens(usage.get('output_tokens'))
    rates = snapshot.get('rates')
    if incoming is None or outgoing is None or not rates:
        return None
    if snapshot['family'] == 'image':
        text, image = _tokens(usage.get('text_input_tokens')), _tokens(usage.get('image_input_tokens'))
        cached = _tokens(usage.get('cached_input_tokens'))
        if text is None or image is None or text + image != incoming or cached != 0:
            return None
        value = (Decimal(text) * amount(rates[0]) + Decimal(image) * amount(rates[1])
                 + Decimal(outgoing) * amount(rates[2])) / 1000000
    elif snapshot['family'] == 'text':
        cached = _tokens(usage.get('cached_input_tokens'))
        tools = _tokens(usage.get('web_search_calls'))
        if cached is None or cached > incoming or tools is None:
            return None
        value = (Decimal(incoming - cached) * amount(rates[0]) + Decimal(cached) * amount(rates[1])
                 + Decimal(outgoing) * amount(rates[2])) / 1000000
        value += Decimal(tools) * (amount(snapshot.get('web_search_usd_per_call', .01))
                                  + Decimal(snapshot.get('web_search_fixed_input_tokens', 0))
                                  * amount(rates[0]) / 1000000)
    else:
        return None
    return amount(value)


def historical_cost(row, *, snapshot=None):
    if row.get('estimated_usd') is not None:
        try:
            recorded = amount(row['estimated_usd'])
            return recorded if recorded.is_finite() and recorded >= 0 else None
        except (ValueError, ArithmeticError):
            return None
    model = str(row.get('model', ''))
    incoming, outgoing = _tokens(row.get('input_tokens')), _tokens(row.get('output_tokens'))
    if incoming is None or outgoing is None:
        return None
    try:
        if row.get('images') or row.get('stage') == 'image_generation':
            rates = snapshot['rates'] if snapshot and snapshot.get('family') == 'image' else IMAGE_RATES[canonical(model, IMAGE_RATES)]
            # Old image telemetry does not distinguish image/text inputs.
            image_input = row.get('image_input_tokens')
            text_input = row.get('text_input_tokens')
            if type(image_input) is int and type(text_input) is int and image_input + text_input == incoming:
                cost = (amount(image_input) * amount(rates[1]) + amount(text_input) * amount(rates[0])) / 1000000
            else:
                cost = amount(incoming) * amount(max(rates[:2])) / 1000000
            cost += amount(outgoing) * amount(rates[2]) / 1000000
        else:
            rates = snapshot['rates'] if snapshot and snapshot.get('family') == 'text' else TEXT_RATES[canonical(model, TEXT_RATES)]
            cached = min(incoming, _tokens(row.get('cached_input_tokens')) or 0)
            cost = (amount(incoming - cached) * amount(rates[0]) + amount(cached) * amount(rates[1])
                    + amount(outgoing) * amount(rates[2])) / 1000000
            # Historical extractor entries can hide a search call. Conservative
            # allowance; new entries record exact web_search_calls independently.
            tools = _tokens(row.get('web_search_calls'))
            if tools is None:
                tools = 2 if row.get('stage') in ('research', 'extractor') else 0
            cost += amount(tools) * (Decimal('.01') + amount(8000 * rates[0] / 1000000))
        return amount(cost * SAFETY)
    except SpendLimitExceeded:
        # Legacy unknown prices must not silently permit a fresh $1 budget.
        return None


def _summary(job, c, *, persist=True):
    if persist:
        init(c)
    exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spend_reservations'").fetchone()
    records = ([json.loads(row['data']) for row in c.execute(
        'SELECT data FROM spend_reservations WHERE job_id=?', (job['id'],))] if exists else [])
    stored = c.execute('SELECT data FROM jobs WHERE id=?', (job['id'],)).fetchone()
    persisted = json.loads(stored['data']) if stored else {}
    tracked = {r.get('response_id') for r in records if r.get('response_id')}
    tracked_images = {r.get('image_task_id') for r in records if r.get('image_task_id')}
    tracked_reservations = {r['id'] for r in records}
    rows = {}
    for index, row in enumerate(persisted.get('usage', []) + job.get('usage', [])):
        if (row.get('response_id') in tracked or row.get('image_task_id') in tracked_images
                or row.get('reservation_id') in tracked_reservations):
            continue
        key = row.get('response_id') or row.get('image_task_id') or json.dumps(row, sort_keys=True)
        rows[key] = row
    # Import the old telemetry once into the durable ledger. A stale job save
    # cannot erase a bill or grant the article a fresh allowance afterwards.
    cap = limit(job, c)
    for key, row in rows.items():
        ident = hashlib.sha256((job['id'] + ':legacy:' + str(key)).encode()).hexdigest()
        estimated = historical_cost(row)
        record = {'id': ident, 'state': 'completed' if estimated is not None else 'uncertain',
                  'charged_usd': float(estimated) if estimated is not None else None,
                  'reserved_usd': float(cap) if estimated is None else 0,
                  'unmeasured_historical': estimated is None,
                  'model': row.get('model'), 'stage': row.get('stage'), 'origin': 'historical',
                  'response_id': row.get('response_id'), 'image_task_id': row.get('image_task_id'),
                  'created_at': db.now()}
        if persist:
            c.execute('INSERT OR IGNORE INTO spend_reservations VALUES (?,?,?)',
                      (ident, job['id'], json.dumps(record)))
        if not any(r['id'] == ident for r in records):
            records.append(record)
    spent = Decimal(0)
    reserved = Decimal(0)
    for record in records:
        if record['state'] == 'completed':
            spent += amount(record['charged_usd'])
        elif record['state'] in ('reserved', 'uncertain'):
            reserved += amount(record['reserved_usd'])
    unmeasured = any(r.get('unmeasured_historical') and r['state'] != 'released' for r in records)
    return {'limit_usd': float(cap), 'spent_usd': float(spent), 'reserved_usd': float(reserved),
            'remaining_usd': 0.0 if unmeasured else float(max(Decimal(0), cap - spent - reserved)), 'accounting_notice': NOTICE,
            'pricing_declared_at': '2026-10-09',
            'pricing_verification': {'gpt-4.1-mini': 'official_2026-10-10',
                                     'whisper-1': 'official_2026-10-10',
                                     'other_models': 'preexisting_declared_tariffs'},
            'historical_estimate': any(r.get('origin') == 'historical' for r in records),
            'unmeasured_historical': unmeasured}


def summary(job, *, persist=True):
    with db.connect() as c:
        if persist:
            c.execute('BEGIN IMMEDIATE')
        return _summary(job, c, persist=persist)


def reserve(job, dollars, model, stage, *, downstream=0, image_task_id=None, metadata=None):
    dollars = amount(dollars)
    if not dollars.is_finite() or dollars <= 0:
        raise SpendLimitExceeded('A estimativa financeira da chamada é inválida. Nenhuma chamada foi enviada.')
    refusal = None
    ident = None
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = _summary(job, c)
        rates = TEXT_RATES.get(model)
        floor = Decimal('.025') if rates and rates[0] <= .4 else Decimal('.10')
        held_for_delivery = amount(floor * max(0, downstream))
        if current.get('unmeasured_historical'):
            refusal = ('Há consumo histórico sem preço/uso mensurado. Reconcilie com evidência '
                       'antes de autorizar novas chamadas pagas; o orçamento não foi renovado.')
        elif dollars + held_for_delivery > amount(current['remaining_usd']):
            refusal = ('O saldo financeiro deste artigo não comporta a próxima chamada '
                f'com margem de segurança (teto US$ {current["limit_usd"]:.2f}). '
                'As entregas foram preservadas; nenhuma chamada foi enviada nesta tentativa.')
        else:
            from . import cost_observability
            context = cost_observability.current()
            ident = uuid.uuid4().hex
            data = {'id': ident, 'entity_id': job['id'], 'model': model, 'stage': stage, 'state': 'reserved',
                    'reserved_usd': float(dollars), 'created_at': db.now(),
                    'run_id': context.get('id'), 'execution_id': context.get('id'),
                    'attempt_id': ident, 'scope': context.get('scope', 'unassigned'),
                    'provider': 'openai', 'origin': 'provider_call',
                    'dependency_fingerprint': context.get('dependency_fingerprint'),
                    'pipeline_version': context.get('pipeline_version'),
                    'pricing_snapshot': pricing_snapshot(model),
                    'calculated_usd': None, 'estimated_usd': None,
                    'duration_seconds': None, **(metadata or {})}
            if image_task_id:
                data['image_task_id'] = image_task_id
            c.execute('INSERT INTO spend_reservations VALUES (?,?,?)',
                      (ident, job['id'], json.dumps(data)))
    # Commit historical imports even when the new request is refused. Throwing
    # inside the transaction would roll back bills imported from old telemetry.
    if refusal:
        raise SpendLimitExceeded(refusal)
    return ident


def finish(ident, usage=None, *, response_id=None, failed_unbilled=False, evidence=None, observed_usage=None):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM spend_reservations WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('Reserva não encontrada.')
        record = json.loads(row['data'])
        if record['state'] in ('completed', 'released'):
            return record
        if failed_unbilled:
            record.update(state='released', reserved_usd=0, finished_at=db.now(),
                          release_evidence=evidence or {'basis': 'definite_provider_request_rejection'})
        elif usage is not None:
            cost = historical_cost({**usage, 'model': record['model']}, snapshot=record.get('pricing_snapshot'))
            calculated = calculated_cost(usage, record['model'], record.get('pricing_snapshot'))
            if calculated is not None:
                # A later measurement may exceed a local duration estimate.
                # Known usage must never disappear behind a smaller estimate.
                measured_guard = amount(calculated * SAFETY)
                cost = max(cost, measured_guard) if cost is not None else measured_guard
            record.update(state='completed' if cost is not None else 'uncertain',
                          charged_usd=float(cost) if cost is not None else None,
                          calculated_usd=float(calculated) if calculated is not None else None,
                          estimated_usd=float(amount(cost / SAFETY)) if cost is not None and calculated is None else None,
                          response_id=response_id, usage=usage, finished_at=db.now())
        else:
            record.update(state='uncertain', response_id=response_id, finished_at=db.now())
            if observed_usage is not None:
                record['usage'] = observed_usage
        try:
            record['duration_seconds'] = max(0, (datetime.fromisoformat(record['finished_at'])
                                                 - datetime.fromisoformat(record['created_at'])).total_seconds())
        except (ValueError, TypeError, KeyError):
            record['duration_seconds'] = None
        c.execute('UPDATE spend_reservations SET data=? WHERE id=?', (json.dumps(record), ident))
    return record


def response_usage(response):
    usage = getattr(response, 'usage', None)
    details = getattr(usage, 'input_tokens_details', None)
    outputs = getattr(response, 'output', None)
    return {'input_tokens': _tokens(getattr(usage, 'input_tokens', None)),
            'output_tokens': _tokens(getattr(usage, 'output_tokens', None)),
            'cached_input_tokens': _tokens(getattr(details, 'cached_tokens', None)),
            'web_search_calls': (sum(getattr(item, 'type', '') == 'web_search_call' for item in outputs)
                                 if isinstance(outputs, (list, tuple)) else None)}


def confirmed_usage(response):
    usage = getattr(response, 'usage', None)
    values = (getattr(usage, 'input_tokens', None), getattr(usage, 'output_tokens', None))
    return all(type(value) is int and value >= 0 for value in values) and sum(values) > 0


def create_response(job, api, request, stage, *, downstream=0):
    """Reserve the entire bounded response before generation; retries reserve anew."""
    from openai import APIStatusError
    from . import cost_observability
    request = {**request, 'service_tier': 'default'}
    model = canonical(request['model'], TEXT_RATES)
    rates = TEXT_RATES[model]
    measured = {k: request[k] for k in ('model', 'instructions', 'input', 'text', 'tools', 'tool_choice') if k in request}
    # For text-only inputs every BPE token consumes at least one UTF-8 byte.
    # Include the complete schema/tools and generous framing allowance.
    incoming = len(json.dumps(measured, ensure_ascii=False).encode('utf-8')) + 4096
    try:
        count = api.responses.input_tokens.count(**measured, timeout=5).input_tokens
        if type(count) is int and count > 0:
            incoming = count + 256
    except Exception:
        pass  # Count unavailable: retain the conservative offline bound.
    tools = request.get('max_tool_calls', 0) if request.get('tools') else 0
    # Search can append hidden context. Fixed block for mini models; otherwise
    # reserve the model context ceiling per tool rather than guessing page size.
    search_tokens = 8000 if model in ('gpt-4.1-mini', 'gpt-4o-mini') else rates[3]
    extra_input = tools * search_tokens
    outgoing = request['max_output_tokens']
    dollars = ((amount(incoming + extra_input) * amount(rates[0])
                + amount(outgoing) * amount(rates[2])) / 1000000 + amount(tools) * Decimal('.01')) * SAFETY
    if tools:
        available = summary(job)['remaining_usd']
        if dollars > amount(available) / 10:
            raise SpendLimitExceeded('A pesquisa opcional ultrapassa sua parcela financeira. '
                                     'O saldo foi reservado às entregas principais; nenhuma pesquisa foi enviada.')
    ident = reserve(job, dollars, request['model'], stage, downstream=downstream,
                    metadata={'input_bound': incoming + extra_input, 'output_bound': outgoing, 'tools_bound': tools,
                              'dependency_fingerprint': cost_observability.fingerprint(request)})
    try:
        response = api.responses.create(**request)
    except BaseException as exc:
        # Timeouts/disconnects/5xx may already have been billed. Keep their full
        # reservation across restarts; only definite request rejection releases it.
        finish(ident, failed_unbilled=isinstance(exc, APIStatusError) and exc.status_code in (400, 401, 403, 404, 422, 429),
               evidence={'basis': 'provider_http_rejection', 'error_type': type(exc).__name__,
                         'status_code': getattr(exc, 'status_code', None)})
        raise
    observed = response_usage(response)
    if not request.get('tools'):
        # The submitted request proves that no search tool could be billed.
        observed['web_search_calls'] = 0
    if not confirmed_usage(response) or observed['web_search_calls'] is None:
        record = finish(ident, response_id=getattr(response, 'id', None), observed_usage=observed)
    else:
        record = finish(ident, observed, response_id=response.id)
    return response, record


def reconcile(ident, *, actor, evidence, usage=None, registered_usd=None, unbilled=False):
    """Reconcile an uncertain ledger entry only with a attributable evidence record.

    Evidence references an independently checked provider receipt/support record;
    this function does not infer non-billing from timeout or contact the provider.
    """
    allowed = {'provider_usage', 'provider_billing_record', 'provider_request_rejection',
               'provider_support_confirmation'}
    if (not isinstance(actor, str) or not actor.strip() or not isinstance(evidence, dict)
            or evidence.get('kind') not in allowed
            or not isinstance(evidence.get('reference'), str) or not evidence['reference'].strip()):
        raise ValueError('Reconciliação exige responsável e referência verificável de evidência do provedor.')
    choices = int(usage is not None) + int(registered_usd is not None) + int(bool(unbilled))
    if choices != 1:
        raise ValueError('Informe exatamente uso confirmado, valor registrado ou prova de não cobrança.')
    if unbilled and evidence['kind'] not in ('provider_request_rejection', 'provider_support_confirmation',
                                            'provider_billing_record'):
        raise ValueError('Uso ausente não comprova ausência de cobrança.')
    value = amount(registered_usd) if registered_usd is not None else None
    if value is not None and (not value.is_finite() or value < 0):
        raise ValueError('Valor registrado inválido.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM spend_reservations WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('Reserva não encontrada.')
        record = json.loads(row['data'])
        if record['state'] != 'uncertain':
            raise ValueError('Somente reservas incertas podem ser reconciliadas por este procedimento.')
        before = {'state': record['state'], 'reserved_usd': record.get('reserved_usd'),
                  'charged_usd': record.get('charged_usd')}
        if unbilled:
            record.update(state='released', reserved_usd=0, release_evidence=evidence)
        elif value is not None:
            record.update(state='completed', registered_usd=float(value), charged_usd=float(value),
                          calculated_usd=None, estimated_usd=None)
        else:
            calculated = calculated_cost(usage, record['model'], record.get('pricing_snapshot'))
            if calculated is None:
                raise ValueError('A evidência de uso não permite calcular o custo completo da chamada.')
            record.update(state='completed', charged_usd=float(amount(calculated * SAFETY)),
                          calculated_usd=float(calculated), estimated_usd=None, usage=usage)
        record['unmeasured_historical'] = False
        record.setdefault('reconciliations', []).append({'actor': actor.strip(), 'evidence': evidence,
                                                        'at': db.now(), 'before': before,
                                                        'after_state': record['state']})
        record['reconciled_at'] = db.now()
        c.execute('UPDATE spend_reservations SET data=? WHERE id=?', (json.dumps(record), ident))
        return record
