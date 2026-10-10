"""Freeze editorial baseline evidence without provider calls or application writes.

This tool reports incomplete samples; it never supplies permissions, timestamps,
human scores or missing costs. Application fingerprints include uncommitted code.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CATEGORIES = ('tutorial', 'interview', 'personal_account', 'technical_analysis', 'short', 'long')
CHALLENGES = ('imperfect_transcript', 'ambiguous_statement', 'visual_demonstration')
RUBRIC = {'fidelity': 30, 'utility': 25, 'naturalness_clarity': 20,
          'precision_attribution': 15, 'seo_presentation': 10}
SOURCE_FIELDS = ('id', 'video_id', 'url', 'title', 'author', 'language', 'provider',
                 'generated_captions', 'extracted_at', 'input_origin', 'notice', 'segments',
                 'medium', 'transcription_model', 'whisper_model', 'whisper_compute_type', 'audio_sha256',
                 'audio_duration', 'transcription_warnings', 'transcription_quality',
                 'normalization_version', 'provider_adapter_version', 'provider_cache_legacy')
JOB_FIELDS = ('id', 'created_at', 'updated_at', 'status', 'brief', 'article', 'review',
              'dossier', 'apuration', 'plan', 'coverage', 'editorial',
              'article_needs_generation', 'generation_complete', 'baseline_origin')
WINDOWS_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL'} | {
    f'{prefix}{index}' for prefix in ('COM', 'LPT') for index in range(1, 10)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def video_id(url):
    if not isinstance(url, str):
        raise ValueError('URL de vídeo ausente ou inválida.')
    parts = urlsplit(url or '')
    if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Use uma URL HTTPS pública do YouTube sem credenciais.')
    if parts.hostname == 'youtu.be':
        ident = parts.path.strip('/')
    elif parts.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        ident = (parse_qs(parts.query).get('v', [''])[0] if parts.path == '/watch'
                 else parts.path.split('/')[-1] if parts.path.startswith(('/shorts/', '/embed/', '/live/')) else '')
    else:
        ident = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
        raise ValueError('URL de vídeo inválida.')
    return ident


def fingerprint(root):
    """Hash source bytes, rather than treating HEAD as the current dirty tree."""
    files = sorted(p for p in (root / 'app').rglob('*')
                   if p.is_file() and p.suffix in {'.py', '.js', '.css', '.html', '.json'}
                   and '__pycache__' not in p.parts)
    files += [root / name for name in ('requirements.txt', 'requirements-dev.txt', 'Dockerfile')
              if (root / name).is_file()]
    rows = [{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(files)]
    def git(*args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    return {'app_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            'files': rows, 'git_head': git('rev-parse', 'HEAD'),
            'git_status': git('status', '--short'),
            'notice': 'Inclui alterações locais. Não certifica a versão que gerou um artigo anterior.'}


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def candidate_configuration(root, database=None):
    """Describe this checkout without claiming it generated an older article."""
    def match_file(name, pattern, cast=str):
        path = root / name
        match = re.search(pattern, path.read_text(encoding='utf-8')) if path.is_file() else None
        return cast(match.group(1)) if match else None
    model = None
    model_origin = 'not_verified'
    if database and database.is_file():
        from app.cost_observability import readonly_snapshot
        with readonly_snapshot(database) as (conn, _):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone():
                row = conn.execute("SELECT value FROM settings WHERE key='model'").fetchone()
                if row:
                    value = json.loads(row[0])
                    if isinstance(value, str) and value.strip():
                        model, model_origin = value, 'local_database_setting'
    if model is None:
        model = match_file('app/main.py', r"get_setting\('model',\s*os\.getenv\('OPENAI_MODEL',\s*'([^']+)'\)")
        if model:
            model_origin = 'source_default_runtime_not_verified'
    return {'app_version': match_file('app/main.py', r'version=[\"\']([^\"\']+)[\"\']'),
            'editorial_version': match_file('app/generation.py', r'EDITORIAL_VERSION\s*=\s*(\d+)', int),
            'evidence_flow_version': match_file('app/editorial/workflow.py', r'\bVERSION\s*=\s*(\d+)', int),
            'model_candidate': model, 'model_origin': model_origin,
            'actual_generation_verified': False,
            'notice': 'Configuração candidata do checkout/local. Modelo efetivamente utilizado pertence ao recibo da execução; produção não foi consultada.'}


def inspect_manifest(manifest):
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('cases'), list):
        raise ValueError('Manifesto deve ter schema_version=1 e cases como lista.')
    requirements = manifest.get('requirements', {})
    if not isinstance(requirements, dict):
        raise ValueError('requirements precisa ser um objeto.')
    minimum = requirements.get('minimum_unique_videos', 1)
    per_category = requirements.get('minimum_per_category', 0)
    required_challenges = requirements.get('required_challenges', [])
    required_ids = requirements.get('required_case_ids')
    if (type(minimum) is not int or minimum < 1 or type(per_category) is not int or per_category < 0
            or not isinstance(required_challenges, list)
            or any(c not in CHALLENGES for c in required_challenges)
            or required_ids is not None and (not isinstance(required_ids, list) or not required_ids
                                            or any(not isinstance(i, str) for i in required_ids))):
        raise ValueError('Requisitos da amostra inválidos.')
    issues, cases, seen_ids, seen_videos = [], [], set(), set()
    for case in manifest['cases']:
        if not isinstance(case, dict):
            raise ValueError('Cada caso precisa ser um objeto.')
        ident = case.get('id', '')
        if (not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident)
                or ident.casefold() in seen_ids or ident.upper() in WINDOWS_RESERVED_NAMES):
            raise ValueError('IDs dos casos devem ser únicos e seguros para nomes de arquivos.')
        seen_ids.add(ident.casefold())
        if (case.get('permission') is not None and not isinstance(case['permission'], dict)
                or case.get('run') is not None and not isinstance(case['run'], dict)):
            raise ValueError('permission e run precisam ser objetos ou null.')
        for field in ('run_id', 'execution_id'):
            value = (case.get('run') or {}).get(field)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 128):
                raise ValueError('Identidade da execução precisa ser string não vazia ou null.')
        if not isinstance(case.get('essential_excerpts', []), list) or not isinstance(case.get('challenges', []), list):
            raise ValueError('essential_excerpts e challenges precisam ser listas.')
        if (case.get('category') is not None and not isinstance(case['category'], str)
                or any(not isinstance(c, str) for c in case.get('challenges', []))
                or case.get('transcript_snapshot') is not None and not isinstance(case['transcript_snapshot'], dict)):
            raise ValueError('Categoria, desafios ou referência de snapshot têm formato inválido.')
        errors = []
        try:
            vid = video_id(case.get('url'))
            if vid in seen_videos:
                errors.append('Vídeo repetido; não conta como nova fonte da amostra.')
            seen_videos.add(vid)
        except (TypeError, ValueError):
            vid = None
            errors.append('URL de vídeo ausente ou inválida.')
        permission = case.get('permission') or {}
        authorized = (permission.get('status') == 'authorized'
                      and isinstance(permission.get('basis'), str) and bool(permission['basis'].strip())
                      and isinstance(permission.get('reference'), str) and bool(permission['reference'].strip()))
        if not authorized:
            errors.append('Base de uso/permissão ainda não documentada como autorizada.')
        if per_category and case.get('category') not in CATEGORIES:
            errors.append('Categoria da amostra ainda não confirmada.')
        if not case.get('essential_excerpts'):
            errors.append('Trechos essenciais e gabarito humano ainda não registrados.')
        cases.append({'id': ident, 'video_id': vid, 'authorized': authorized,
                      'required': required_ids is None or ident in required_ids, 'issues': errors})
    if required_ids is not None and not set(required_ids).issubset(c['id'] for c in cases):
        raise ValueError('required_case_ids contém um caso ausente do manifesto.')
    forbidden_ids = {c['video_id'] for c, original in zip(cases, manifest['cases'])
                     if c['video_id'] and (original.get('permission') or {}).get('status') in ('denied', 'restricted')}
    for case in cases:
        if case['video_id'] in forbidden_ids:
            case['authorized'] = False
            case['issues'].append('Fonte com restrição ou declarações de autorização conflitantes no manifesto.')
    required_videos = {c['video_id'] for c in cases if c['required'] and c['video_id']}
    required_cases = [original for original, inspection in zip(manifest['cases'], cases)
                      if inspection['required'] and inspection['authorized'] and inspection['video_id']]
    categories = Counter(c.get('category') for c in required_cases)
    if len(required_videos) < minimum:
        issues.append(f'Amostra obrigatória tem {len(required_videos)} vídeos identificáveis; mínimo definido: {minimum}.')
    for category in CATEGORIES:
        if categories[category] < per_category:
            issues.append(f'Categoria {category}: {categories[category]} de {per_category} casos previstos.')
    for challenge in required_challenges:
        if not any(challenge in c.get('challenges', []) for c in required_cases):
            issues.append(f'Desafio {challenge}: nenhum caso real confirmado.')
    return {'schema_version': 1, 'status': 'incomplete', 'unique_videos': len(seen_videos),
            'required_videos': len(required_videos), 'minimum_unique_videos': minimum,
            'authorized_cases': sum(c['authorized'] for c in cases),
            'category_counts': {k: categories[k] for k in CATEGORIES}, 'issues': issues, 'cases': cases}


def read_job(database, job_id):
    """Opening a missing database must not create one or initialize app tables."""
    if not database.is_file():
        raise ValueError('Banco inexistente. Nenhum banco foi criado.')
    from app.cost_observability import readonly_snapshot
    with readonly_snapshot(database) as (conn, _):
        row = conn.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Artigo não encontrado no banco indicado.')
        return json.loads(row[0])


def validate_job(job):
    if not isinstance(job, dict):
        raise ValueError('Snapshot de trabalho precisa ser um objeto.')
    for field in ('sources', 'usage'):
        rows = job.get(field, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f'{field} precisa conter uma lista de objetos.')
    seen_segment_ids = set()
    for source in job.get('sources', []):
        segments = source.get('segments', [])
        if not isinstance(segments, list) or any(not isinstance(s, dict) for s in segments):
            raise ValueError('segments precisa conter uma lista de objetos.')
        if any(not isinstance(s.get('text'), str) for s in segments):
            raise ValueError('Texto de cada segmento precisa ser uma string.')
        for segment in segments:
            ident = segment.get('id')
            if not isinstance(ident, str) or not ident.strip() or ident in seen_segment_ids:
                raise ValueError('IDs dos segmentos precisam ser strings não vazias e únicas no trabalho.')
            seen_segment_ids.add(ident)
    if job.get('article') is not None and not isinstance(job['article'], dict):
        raise ValueError('article precisa ser um objeto ou null.')
    if job.get('editorial') is not None and not isinstance(job['editorial'], dict):
        raise ValueError('editorial precisa ser um objeto ou null.')


def capture_case(case, inspection, database, app_sha256, authorized_video_ids=None):
    """Permissions constrain this research capture, never the app's export state."""
    if not inspection['authorized'] or not inspection['video_id']:
        return None, ['Coleta do caso pendente; dados do artigo não foram lidos.']
    financial_execution = None
    if case.get('job_json'):
        raw_snapshot = Path(case['job_json']).read_bytes()
        expected_hash = (case.get('transcript_snapshot') or {}).get('sha256')
        if expected_hash and hashlib.sha256(raw_snapshot).hexdigest() != expected_hash:
            raise ValueError('Snapshot JSON diverge do hash registrado; conteúdo não foi capturado.')
        job = json.loads(raw_snapshot.decode('utf-8'))
        if not isinstance(job, dict) or case.get('job_id') and case['job_id'] != job.get('id'):
            raise ValueError('Snapshot JSON não corresponde ao job_id declarado.')
    elif database and case.get('job_id'):
        declared = case.get('run') or {}
        ident = declared.get('run_id') or declared.get('execution_id')
        if ident and not (declared.get('run_id') and declared.get('execution_id')
                          and declared['run_id'] != declared['execution_id']):
            from app.cost_observability import read_report, readonly_snapshot
            with readonly_snapshot(database) as (conn, _):
                row = conn.execute('SELECT data FROM jobs WHERE id=?', (case['job_id'],)).fetchone()
                if not row:
                    raise ValueError('Artigo não encontrado no banco indicado.')
                job = json.loads(row[0])
                # Project the same frozen input DB, rather than reading two
                # different moments from a concurrently changing source DB.
                clone_path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
                financial = read_report(clone_path, job_id=case['job_id'], run_id=ident)
                financial_execution = next(iter(financial['executions']), None)
        else:
            job = read_job(database, case['job_id'])
    else:
        return None, ['Banco/job_id ou snapshot JSON da execução ainda não informados.']
    validate_job(job)
    authorized_video_ids = (authorized_video_ids if authorized_video_ids is not None
                            else {inspection['video_id']})
    if any(s.get('video_id') not in authorized_video_ids for s in job.get('sources', [])):
        return None, ['Todas as fontes do artigo precisam de base de uso registrada no manifesto.']
    source = next((s for s in job.get('sources', [])
                   if s.get('video_id') == inspection['video_id']), None)
    if not source:
        return None, ['O artigo informado não contém o vídeo deste caso.']
    issues = []
    if case.get('exclusive_source') and len(job.get('sources', [])) != 1:
        issues.append('Este caso exige artigo baseado somente no vídeo indicado; o snapshot reúne outras fontes.')
    if job.get('article_needs_generation') or (job.get('editorial') or {}).get('stale'):
        issues.append('Fontes/direção mudaram após a geração; este artigo não certifica o baseline das entradas atuais.')
    segments = source.get('segments') or []
    if not segments or any(not str(s.get('text') or '').strip()
                           or not finite_number(s.get('start')) or not finite_number(s.get('end'))
                           or s['end'] < s['start'] for s in segments):
        issues.append('Transcrição com timestamps completos ainda não disponível.')
    by_id = {s.get('id'): s for s in segments}
    for excerpt in case.get('essential_excerpts', []):
        selected = by_id.get(excerpt.get('source_id')) if isinstance(excerpt, dict) else None
        text = excerpt.get('text') if isinstance(excerpt, dict) else None
        if not selected or not isinstance(text, str) or not text.strip() or text not in selected.get('text', ''):
            issues.append('Trecho essencial não corresponde literalmente ao segmento indicado.')
    article = job.get('article') or {}
    if any(not isinstance(article.get(k), str) or not article[k].strip()
           for k in ('title', 'slug', 'markdown')):
        issues.append('Artigo gerado ainda não disponível/completo.')
    run = case.get('run') or {}
    if run.get('app_sha256') != app_sha256:
        issues.append('Versão que gerou o artigo não confirmada contra o fingerprint atual.')
    if not run.get('model') or not run.get('profile'):
        issues.append('Modelo/perfil da execução ainda não registrados.')
    all_usage = job.get('usage') or []
    run_id = run.get('run_id') or run.get('execution_id')
    conflicting_ids = bool(run.get('run_id') and run.get('execution_id')
                           and run['run_id'] != run['execution_id'])
    usage_start, usage_end = run.get('usage_start_index'), run.get('usage_end_index')
    usage_range_valid = (type(usage_start) is int and type(usage_end) is int
                         and 0 <= usage_start <= usage_end <= len(all_usage))
    if conflicting_ids:
        usage, scope = [], 'invalid_execution_id'
        issues.append('Identificadores run_id/execution_id conflitantes; custo da execução não certificado.')
    elif run_id:
        usage = [u for u in all_usage if (u.get('run_id') or u.get('execution_id')) == run_id]
        scope = 'execution_id'
        if not usage:
            issues.append('Uso com a identidade da execução ainda não registrado; histórico não foi usado como custo atual.')
    else:
        usage = all_usage[usage_start:usage_end] if usage_range_valid else all_usage
        scope = 'declared_execution_range' if usage_range_valid else 'article_history'
    if not run_id and not usage_range_valid:
        issues.append('Intervalo de uso da execução não registrado; totais referem-se ao histórico do artigo.')
    costs = [u.get('estimated_usd') for u in usage]
    cost = sum(costs) if costs and all(finite_number(c) for c in costs) else None
    if cost is None:
        issues.append('Custo da execução ausente ou parcialmente registrado; não foi estimado novamente.')
    latency = (financial_execution.get('duration_seconds') if financial_execution
               else run.get('wall_seconds'))
    if not finite_number(latency):
        latency = None
        issues.append('Tempo de parede da execução ainda não medido.')
    calculated = [u.get('calculated_usd') for u in usage]
    calculated_cost = (sum(calculated) if calculated and all(finite_number(c) for c in calculated) else None)
    cached = [u.get('cached_input_tokens') for u in usage
              if type(u.get('cached_input_tokens')) is int and u['cached_input_tokens'] >= 0]
    metrics = {'usage_scope': scope, 'run_id': run_id if not conflicting_ids else None,
               'usage_rows': len(usage), 'known_input_tokens': sum(u['input_tokens'] for u in usage
                if type(u.get('input_tokens')) is int and u['input_tokens'] >= 0)
                if any(type(u.get('input_tokens')) is int and u['input_tokens'] >= 0 for u in usage) else None,
               'known_output_tokens': sum(u['output_tokens'] for u in usage
                if type(u.get('output_tokens')) is int and u['output_tokens'] >= 0)
                if any(type(u.get('output_tokens')) is int and u['output_tokens'] >= 0 for u in usage) else None,
               'rows_without_token_telemetry': sum(not all(type(u.get(k)) is int and u[k] >= 0
                  for k in ('input_tokens', 'output_tokens')) for u in usage),
               'estimated_usage_usd': cost, 'calculated_usage_usd': calculated_cost,
               'current_execution_calculated_usd': None,
               'provider_cached_input_tokens': sum(cached) if cached else None,
               'application_cache_hits': None, 'reserved_usd': None,
               'inconclusive_reserve_usd': None, 'provider_invoice_usd': None,
               'infrastructure_usd': None, 'human_review_cost_usd': None,
               'wall_seconds': latency,
               'notice': 'Uso calculado usa a tarifa registrada; estimativa pode incluir margem de segurança. Reserva, cache de aplicação e fatura são métricas distintas. Ausência permanece null.'}
    if financial_execution:
        execution_costs = financial_execution['costs']
        metrics.update(execution_costs=execution_costs,
                       current_execution_calculated_usd=execution_costs.get('calculated_usd'),
                       application_cache_hits=execution_costs.get('application_cache_hits'),
                       reserved_usd=execution_costs.get('reserved_usd'),
                       inconclusive_reserve_usd=execution_costs.get('inconclusive_reserve_usd'),
                       execution_status=financial_execution.get('status'),
                       measured_pipeline_version=financial_execution.get('pipeline_version'))
        if execution_costs.get('inconclusive_reserve_usd'):
            issues.append('Execução possui reserva incerta; o gasto faturado não foi confirmado.')
        if execution_costs.get('unmeasured_events'):
            issues.append('Execução inclui serviços sem custo mensurado; total completo não confirmado.')
    snapshot = {k: job[k] for k in JOB_FIELDS if k in job}
    snapshot['sources'] = [{k: s[k] for k in SOURCE_FIELDS if k in s} for s in job.get('sources', [])]
    snapshot['baseline_metrics'] = metrics
    snapshot['baseline_run_declaration'] = run
    return snapshot, issues


def execute(manifest, database, output, root=ROOT):
    report = inspect_manifest(manifest)
    code = fingerprint(root)
    run_dir = output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / 'fingerprint.json', code)
    write_json(run_dir / 'manifest.json', manifest)
    authorized_ids = {c['video_id'] for c in report['cases'] if c['authorized'] and c['video_id']}
    for case, inspection in zip(manifest['cases'], report['cases']):
        try:
            snapshot, issues = capture_case(case, inspection, database, code['app_sha256'], authorized_ids)
        except (ValueError, OSError, sqlite3.Error) as exc:
            snapshot, issues = None, [str(exc)]
        inspection['issues'].extend(issues)
        inspection['snapshot_available'] = snapshot is not None
        inspection['article_available'] = bool(snapshot and snapshot.get('article'))
        inspection['source_available'] = bool(snapshot and snapshot.get('sources'))
        case_dir = run_dir / 'cases' / case['id']
        case_dir.mkdir(parents=True)
        if snapshot:
            write_json(case_dir / 'snapshot.json', snapshot)
            inspection['snapshot_sha256'] = hashlib.sha256((case_dir / 'snapshot.json').read_bytes()).hexdigest()
            inspection['metrics'] = snapshot['baseline_metrics']
        write_json(case_dir / 'human-review.json', {
            'case_id': case['id'], 'reviewer': None, 'blind_label': None,
            'dimensions': {k: {'weight_percent': v, 'score_0_to_5': None,
                              'justification': None, 'source_ids': [], 'article_passages': []}
                           for k, v in RUBRIC.items()},
            'critical_errors': [], 'essential_excerpts': case.get('essential_excerpts', []),
            'qualifiers': case.get('qualifiers', []), 'examples': case.get('examples', []),
            'checks': {
                'source_claims_match_original_video': None,
                'essential_methods_examples_conditions_preserved': None,
                'no_invented_personal_experiences_or_authorship': None,
                'complementary_knowledge_distinct_attributed_verified': None,
                'complete_answer_without_word_count_filler': None,
                'natural_cohesion_clear_referents_objective_language': None,
            },
            'notes': None, 'completed': False, 'export_blocking': False})
    if not report['issues'] and all(not c['issues'] for c in report['cases'] if c['required']):
        report['status'] = 'ready_for_human_evaluation'
    report.update(app_sha256=code['app_sha256'], paid_calls=0, provider_calls=0,
                  database_mode='isolated_read_only_snapshot' if database else 'not_requested',
                  human_evaluation_completed=False,
                  candidate_configuration=candidate_configuration(root, database),
                  demonstrated_improvement=False,
                  tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write_json(run_dir / 'report.json', report)
    return run_dir, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'docs/evolucao-seo-premium/amostra.json')
    parser.add_argument('--database', type=Path, help='SQLite existente; DB/WAL copiados sem abrir ou alterar o original.')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/premium-baseline')
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        folder, report = execute(manifest, args.database, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({'status': report['status'], 'report': str(folder / 'report.json'),
                      'unique_videos': report['unique_videos'],
                      'authorized_cases': report['authorized_cases'], 'paid_calls': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
