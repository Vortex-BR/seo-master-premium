"""Durable article-wide USD reservations, shared by text, search and images.

Rates: OpenAI standard pricing, checked 2026-10-09. This is a conservative
application ledger, not the provider invoice. Unknown prices fail before billing.
"""
import hashlib
import json
import re
import uuid
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
HARD_LIMIT = Decimal('1')
NOTICE = ('Controle conservador em US$: inclui todas as rodadas deste artigo, pesquisas, '
          'retentativas e imagens. Não é o extrato da OpenAI. Valores sem confirmação '
          'continuam reservados; o orçamento não é renovado ao retomar.')


def amount(value):
    return Decimal(str(value)).quantize(Decimal('.000001'), rounding=ROUND_CEILING)


def canonical(model, prices):
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
    value = profile.get('max_spend_usd', 1)
    try:
        result = amount(value)
        return min(HARD_LIMIT, max(Decimal('.01'), result)) if result.is_finite() else HARD_LIMIT
    except (ValueError, ArithmeticError):
        return HARD_LIMIT


def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS spend_reservations '
              '(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, data TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS spend_reservations_job ON spend_reservations(job_id)')


def historical_cost(row):
    if row.get('estimated_usd') is not None:
        return amount(row['estimated_usd'])
    model = str(row.get('model', ''))
    incoming, outgoing = row.get('input_tokens', 0) or 0, row.get('output_tokens', 0) or 0
    try:
        if row.get('images') or row.get('stage') == 'image_generation':
            rates = IMAGE_RATES[canonical(model, IMAGE_RATES)]
            # Old image telemetry does not distinguish image/text inputs.
            image_input = row.get('image_input_tokens')
            text_input = row.get('text_input_tokens')
            if type(image_input) is int and type(text_input) is int and image_input + text_input == incoming:
                cost = (amount(image_input) * amount(rates[1]) + amount(text_input) * amount(rates[0])) / 1000000
            else:
                cost = amount(incoming) * amount(max(rates[:2])) / 1000000
            cost += amount(outgoing) * amount(rates[2]) / 1000000
        else:
            rates = TEXT_RATES[canonical(model, TEXT_RATES)]
            cached = min(incoming, max(0, row.get('cached_input_tokens', 0) or 0))
            cost = (amount(incoming - cached) * amount(rates[0]) + amount(cached) * amount(rates[1])
                    + amount(outgoing) * amount(rates[2])) / 1000000
            # Historical extractor entries can hide a search call. Conservative
            # allowance; new entries record exact web_search_calls independently.
            tools = row.get('web_search_calls')
            if tools is None:
                tools = 2 if row.get('stage') in ('research', 'extractor') else 0
            cost += amount(tools) * (Decimal('.01') + amount(8000 * rates[0] / 1000000))
        return amount(cost * SAFETY)
    except SpendLimitExceeded:
        # Legacy unknown prices must not silently permit a fresh $1 budget.
        return HARD_LIMIT


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
    for key, row in rows.items():
        ident = hashlib.sha256((job['id'] + ':legacy:' + str(key)).encode()).hexdigest()
        record = {'id': ident, 'state': 'completed', 'charged_usd': float(historical_cost(row)),
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
    cap = limit(job, c)
    return {'limit_usd': float(cap), 'spent_usd': float(spent), 'reserved_usd': float(reserved),
            'remaining_usd': float(max(Decimal(0), cap - spent - reserved)), 'accounting_notice': NOTICE,
            'pricing_checked_at': '2026-10-09',
            'historical_estimate': any(r.get('origin') == 'historical' for r in records)}


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
        if dollars + held_for_delivery > amount(current['remaining_usd']):
            refusal = ('O saldo financeiro deste artigo não comporta a próxima chamada '
                f'com margem de segurança (teto US$ {current["limit_usd"]:.2f}). '
                'As entregas foram preservadas; nenhuma chamada foi enviada nesta tentativa.')
        else:
            ident = uuid.uuid4().hex
            data = {'id': ident, 'model': model, 'stage': stage, 'state': 'reserved',
                    'reserved_usd': float(dollars), 'created_at': db.now(), **(metadata or {})}
            if image_task_id:
                data['image_task_id'] = image_task_id
            c.execute('INSERT INTO spend_reservations VALUES (?,?,?)',
                      (ident, job['id'], json.dumps(data)))
    # Commit historical imports even when the new request is refused. Throwing
    # inside the transaction would roll back bills imported from old telemetry.
    if refusal:
        raise SpendLimitExceeded(refusal)
    return ident


def finish(ident, usage=None, *, response_id=None, failed_unbilled=False):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM spend_reservations WHERE id=?', (ident,)).fetchone()
        record = json.loads(row['data'])
        if record['state'] == 'completed':
            return record
        if failed_unbilled:
            record.update(state='released', reserved_usd=0)
        elif usage is not None:
            cost = historical_cost({**usage, 'model': record['model']})
            record.update(state='completed', charged_usd=float(cost), response_id=response_id,
                          usage=usage, finished_at=db.now())
        else:
            record.update(state='uncertain', finished_at=db.now())
        c.execute('UPDATE spend_reservations SET data=? WHERE id=?', (json.dumps(record), ident))
    return record


def response_usage(response):
    usage = getattr(response, 'usage', None)
    details = getattr(usage, 'input_tokens_details', None)
    outputs = getattr(response, 'output', []) or []
    def tokens(value):
        return value if type(value) is int and value >= 0 else 0
    return {'input_tokens': tokens(getattr(usage, 'input_tokens', 0)),
            'output_tokens': tokens(getattr(usage, 'output_tokens', 0)),
            'cached_input_tokens': tokens(getattr(details, 'cached_tokens', 0)),
            'web_search_calls': sum(getattr(item, 'type', '') == 'web_search_call' for item in outputs)}


def confirmed_usage(response):
    usage = getattr(response, 'usage', None)
    values = (getattr(usage, 'input_tokens', None), getattr(usage, 'output_tokens', None))
    return all(type(value) is int and value >= 0 for value in values) and sum(values) > 0


def create_response(job, api, request, stage, *, downstream=0):
    """Reserve the entire bounded response before generation; retries reserve anew."""
    from openai import APIStatusError
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
        if dollars > min(Decimal('.05'), amount(available) / 10):
            raise SpendLimitExceeded('A pesquisa opcional ultrapassa sua parcela financeira. '
                                     'O saldo foi reservado às entregas principais; nenhuma pesquisa foi enviada.')
    ident = reserve(job, dollars, model, stage, downstream=downstream,
                    metadata={'input_bound': incoming + extra_input, 'output_bound': outgoing, 'tools_bound': tools})
    try:
        response = api.responses.create(**request)
    except Exception as exc:
        # Timeouts/disconnects/5xx may already have been billed. Keep their full
        # reservation across restarts; only definite request rejection releases it.
        finish(ident, failed_unbilled=isinstance(exc, APIStatusError) and exc.status_code in (400, 401, 403, 404, 422, 429))
        raise
    if not confirmed_usage(response):
        record = finish(ident)
    else:
        record = finish(ident, response_usage(response), response_id=response.id)
    return response, record
