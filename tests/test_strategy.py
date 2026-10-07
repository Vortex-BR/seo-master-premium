"""Comprehensive test suite for Organic Intelligence (Strategy Module).

Tests:
- 8 specialist agents roster and role definitions
- Data contracts and validation
- Persistence layer (store: cycles, runs, opportunities)
- Coordinator execution, phase dependencies, and budget enforcement
- Synthesis with mock AI and deterministic fallback
- Checkpoints and resume functionality
- API endpoints: roster, cycles, opportunities, decisions, and produce
- Service restart recovery
"""
import uuid
from copy import deepcopy
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app import db, generation, pipeline
from app.strategy import agents as strategy_agents, coordinator, engine as strategy_engine, store as strategy_store
from app.strategy.contracts import (
    BusinessAnalysis,
    CompetitorAnalysis,
    ContentArchitecture,
    ContentCuration,
    IntentAnalysis,
    Opportunity,
    OpportunityDecision,
    OpportunityProduce,
    PerformanceAnalysis,
    ResultsAnalysis,
    StrategyBudget,
    StrategyEvidence,
    StrategyFinding,
    StrategyPlan,
    StrategyRequest,
    TechnicalHealth,
)


@pytest.fixture
def mock_strategy_ai(monkeypatch):
    """Deterministic mock provider for all 8 strategic agents and coordinator."""
    sample_finding = {
        'severity': 'opportunity',
        'area': 'Conteúdo orgânico',
        'summary': 'Oportunidade para guia de cultivo de manjericão em vasos',
        'evidence': [{'source': 'gsc', 'metric': 'impressões', 'value': '1200', 'period': 'últimos 28 dias', 'detail': 'queries em crescimento'}],
        'suggestion': 'Criar guia prático focado em cuidados e rega',
        'related_pages': ['https://exemplo.com/horta'],
        'related_queries': ['como plantar manjericao', 'manjericao em vaso'],
    }

    mock_outputs = {
        'strategy_business': {
            'summary': 'Negócio voltado para jardinagem urbana e produtos sustentáveis.',
            'business_topics': ['horta em apartamento', 'ervas aromáticas', 'adubação orgânica'],
            'commercial_priorities': ['kits de sementes', 'vasos autoirrigáveis'],
            'restrictions': ['não usar agrotóxicos'],
            'context_gaps': ['preço médio dos kits'],
            'findings': [sample_finding],
        },
        'strategy_performance': {
            'summary': 'Crescimento de 15% em impressões no nicho de ervas.',
            'total_clicks': 450,
            'total_impressions': 8900,
            'period': 'últimos 28 dias',
            'top_queries': [{'query': 'como plantar manjericao', 'clicks': 120, 'impressions': 2300, 'position': 8.2, 'ctr': 0.052}],
            'declining_pages': [],
            'growing_pages': [{'url': 'https://exemplo.com/horta', 'clicks': 200}],
            'findings': [sample_finding],
        },
        'strategy_intent': {
            'summary': 'Intenção predominantemente informativa com transição para comercial.',
            'intents': [{'query': 'como plantar manjericao', 'intent_type': 'informational', 'stage': 'descoberta', 'format': 'artigo com vídeo', 'questions': ['qual o tamanho do vaso?']}],
            'audience_segments': ['iniciantes em jardinagem'],
            'findings': [sample_finding],
        },
        'strategy_competitors': {
            'summary': 'Concorrentes cobrem o básico mas pecam em detalhes práticos de rega.',
            'competitors': [{'domain': 'concorrente.com', 'overlap_queries': ['plantar manjericao'], 'strengths': 'autoridade', 'gaps': 'falta vídeo e passo a passo'}],
            'serp_features': ['featured_snippet', 'video_carousel'],
            'content_gaps': ['drenagem e substrato ideal'],
            'findings': [sample_finding],
        },
        'strategy_architecture': {
            'summary': 'Cluster sugerido: Ervas em Casa com página pilar de horta urbana.',
            'clusters': [{'name': 'Ervas em Casa', 'pillar_page': 'https://exemplo.com/horta', 'supporting_pages': ['https://exemplo.com/manjericao'], 'queries': ['manjericao']}],
            'overlaps': [],
            'internal_link_suggestions': [{'from': 'https://exemplo.com/horta', 'to': 'https://exemplo.com/manjericao', 'anchor': 'cultivo de manjericão'}],
            'findings': [sample_finding],
        },
        'strategy_technical': {
            'summary': 'Saúde técnica estável, sem bloqueios de indexação.',
            'issues': [],
            'indexation_status': 'todas as páginas chave indexadas',
            'findings': [],
        },
        'strategy_curation': {
            'summary': 'Vídeo do YouTube selecionado com explicação detalhada de poda e rega.',
            'selected_videos': [{
                'video_id': 'abcdefghijk',
                'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                'title': 'Como cuidar do manjericão para durar anos',
                'channel': 'Canal da Horta',
                'reason': 'Excelente demonstração prática de poda e colheita correta.',
                'key_contributions': ['técnica de poda por nós', 'frequência de rega'],
                'transcript_available': True,
            }],
            'approved_channels': [{'channel_id': 'UC123', 'name': 'Canal da Horta'}],
            'research_gaps': ['dados sobre solo argiloso vs arenoso'],
            'briefing_notes': 'Enfocar na poda correta para evitar florescimento precoce.',
            'findings': [sample_finding],
        },
        'strategy_results': {
            'summary': 'Artigos anteriores mantêm estabilidade de cliques.',
            'interventions_reviewed': [],
            'insights': ['artigos com vídeo retêm 40% mais tempo na página'],
            'next_actions': ['expandir cluster de ervas'],
            'findings': [],
        },
        'strategy_coordinator': {
            'summary': 'Plano estratégico focado em consolidar autoridade no nicho de ervas aromáticas através de pautas práticas com vídeos curados.',
            'opportunities': [{
                'opportunity_id': 'opp-manjericao-01',
                'action': 'create',
                'main_question': 'Como plantar e cuidar de manjericão em vaso para durar anos?',
                'target_page': '',
                'queries': ['como plantar manjericao', 'manjericao em vaso'],
                'related_products': ['vaso autoirrigável'],
                'selected_videos': [{
                    'video_id': 'abcdefghijk',
                    'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                    'title': 'Como cuidar do manjericão para durar anos',
                }],
                'evidence': [{'source': 'gsc', 'metric': 'impressões', 'value': '1200', 'period': '28d', 'detail': 'demanda identificada'}],
                'justification': 'Alta demanda e oportunidade de conectar com o produto do site.',
                'gaps': [],
                'effort': 'medium',
                'priority_score': 85.0,
                'monitoring_plan': 'Acompanhar impressões e cliques em 28 e 60 dias.',
                'status': 'proposed',
            }],
            'conflicts': [],
            'context_gaps': [],
            'next_cycle_focus': 'Avaliar sementes e adubação orgânica.',
        },
    }

    def fake_structured(job, schema, instruction, stage, extra=None):
        if stage in mock_outputs:
            return deepcopy(mock_outputs[stage])
        return {'summary': 'Padrão mockado', 'findings': []}

    mock = Mock(side_effect=fake_structured)
    monkeypatch.setattr(generation, 'structured', mock)
    monkeypatch.setattr(strategy_engine, 'get_secret', lambda name: 'test-api-key')
    monkeypatch.setattr(coordinator, 'get_secret', lambda name: 'test-api-key')
    return mock


# ===========================================================================
# 1. Roster and Agent Definitions
# ===========================================================================

def test_strategy_roster():
    roster = strategy_agents.roster()
    assert len(roster) == 8
    roles_by_id = {r['id']: r for r in roster}
    expected_ids = ['business', 'performance', 'intent', 'competitors', 'architecture', 'technical', 'curation', 'results']
    for eid in expected_ids:
        assert eid in roles_by_id
        assert roles_by_id[eid]['name']
        assert roles_by_id[eid]['sector']

    # Check dependency ordering
    assert strategy_agents.ROLES['business']['dependencies'] == []
    assert strategy_agents.ROLES['intent']['dependencies'] == ['business', 'performance']
    assert strategy_agents.ROLES['architecture']['dependencies'] == ['business', 'performance', 'intent']
    assert strategy_agents.ROLES['curation']['dependencies'] == ['business', 'intent', 'architecture']


# ===========================================================================
# 2. Data Contracts Validation
# ===========================================================================

def test_strategy_contracts_validation():
    # Valid BusinessAnalysis
    ba = BusinessAnalysis(
        summary='Resumo do negócio',
        business_topics=['horta', 'jardinagem'],
        commercial_priorities=['kits'],
    )
    assert len(ba.business_topics) == 2

    # Valid Opportunity
    opp = Opportunity(
        opportunity_id='opp-1',
        action='create',
        main_question='Como plantar?',
        justification='Demanda comprovada no GSC.',
        priority_score=90.0,
    )
    assert opp.action == 'create'
    assert opp.status == 'proposed'

    # Valid StrategyPlan
    plan = StrategyPlan(
        summary='Resumo executivo do plano',
        opportunities=[opp],
    )
    assert len(plan.opportunities) == 1

    # Budget defaults
    budget = StrategyBudget()
    assert budget.max_agent_calls == 20
    assert budget.max_tokens_estimate == 200000


# ===========================================================================
# 3. Store CRUD operations
# ===========================================================================

def test_strategy_store_crud(client):
    strategy_store.init()

    # Save and get cycle
    cycle_data = {
        'id': 'cycle-test-01',
        'project_id': 'proj-abc',
        'status': 'queued',
        'created_at': db.now(),
        'updated_at': db.now(),
        'focus': 'Foco teste',
        'budget': {'max_agent_calls': 15},
    }
    strategy_store.save_cycle(cycle_data)
    retrieved = strategy_store.get_cycle('cycle-test-01')
    assert retrieved is not None
    assert retrieved['project_id'] == 'proj-abc'
    assert retrieved['status'] == 'queued'

    # List cycles
    cycles = strategy_store.list_cycles('proj-abc')
    assert len(cycles) >= 1
    assert cycles[0]['id'] == 'cycle-test-01'

    # Save and get opportunity
    opp_data = {
        'opportunity_id': 'opp-test-01',
        'action': 'create',
        'main_question': 'Pergunta teste?',
        'queries': ['query 1'],
        'justification': 'Justificativa teste',
        'status': 'proposed',
    }
    strategy_store.save_opportunity(opp_data, cycle_data, 'proj-abc')
    opp = strategy_store.get_opportunity('opp-test-01')
    assert opp is not None
    assert opp['action'] == 'create'
    assert opp['status'] == 'proposed'

    # Filter opportunities by status
    listed = strategy_store.list_opportunities('proj-abc', status='proposed')
    assert len(listed) == 1
    empty = strategy_store.list_opportunities('proj-abc', status='approved')
    assert len(empty) == 0

    # Cycle report
    report = strategy_store.cycle_report('cycle-test-01')
    assert report is not None
    assert report['cycle']['id'] == 'cycle-test-01'
    assert len(report['opportunities']) == 1


# ===========================================================================
# 4. Coordinator Synthesis & Fallback
# ===========================================================================

def test_coordinator_deterministic_plan_fallback():
    cycle = {
        'id': 'cycle-fallback',
        'calls': 20,
        'budget': {'max_agent_calls': 20},
        'events': [],
    }
    results = {
        'business': {
            'findings': [{
                'severity': 'critical',
                'area': 'Nicho',
                'summary': 'Falta definição de público-alvo',
                'suggestion': 'Definir persona no perfil',
                'related_pages': ['https://exemplo.com'],
                'related_queries': ['horta'],
                'evidence': [],
            }],
            'context_gaps': ['catálogo de produtos'],
        },
        'performance': {
            'findings': [{
                'severity': 'opportunity',
                'area': 'Busca',
                'summary': 'Palavra-chave com alta impressão',
                'suggestion': 'Criar artigo dedicado',
                'related_pages': [],
                'related_queries': ['manjericao'],
                'evidence': [],
            }],
        },
    }
    plan = coordinator._deterministic_plan(cycle, results)
    assert 'agregação determinística' in plan['summary']
    assert len(plan['opportunities']) == 2
    assert plan['context_gaps'] == ['catálogo de produtos']


# ===========================================================================
# 5. Full Strategy Cycle Execution
# ===========================================================================

def test_strategy_cycle_full_execution(client, mock_strategy_ai):
    strategy_store.init()

    # Start a cycle directly via engine
    cycle = strategy_engine.start(project_id='test-project', focus='Ervas medicinais')
    cycle_id = cycle['id']

    # Execute synchronous run
    strategy_engine.run(cycle_id)

    # Check cycle final state
    updated = strategy_store.get_cycle(cycle_id)
    assert updated['status'] == 'ready'
    assert updated['error'] is None
    assert len(updated['completed']) == 8  # all 8 agents completed

    # Verify plan and opportunities were saved
    assert updated['plan'] is not None
    assert len(updated['plan']['opportunities']) >= 1

    opps = strategy_store.list_opportunities('test-project')
    assert len(opps) >= 1
    assert opps[0]['opportunity_id'] == 'opp-manjericao-01'

    # Check runs in store
    runs = strategy_store.cycle_runs(cycle_id)
    # 8 agent runs + 1 coordinator run
    assert len(runs) == 9
    assert all(r['status'] == 'completed' for r in runs)


# ===========================================================================
# 6. Budget Enforcement
# ===========================================================================

def test_strategy_budget_enforcement(client, mock_strategy_ai):
    strategy_store.init()
    # Cycle with budget of only 1 call
    cycle = strategy_engine.start(project_id='test-project', budget={'max_agent_calls': 1})
    cycle_id = cycle['id']

    # Execute run — should fail because it needs 8 calls + coordinator
    strategy_engine.run(cycle_id)

    updated = strategy_store.get_cycle(cycle_id)
    assert updated['status'] == 'failed'
    assert 'atingiu o limite de chamadas' in updated['error']


# ===========================================================================
# 7. Checkpoints and Resume
# ===========================================================================

def test_strategy_checkpoint_and_resume(client, mock_strategy_ai):
    strategy_store.init()
    cycle = strategy_engine.start(project_id='test-project')
    cycle_id = cycle['id']

    # Simulate running only 2 agents then failing
    output_1, run_id_1 = coordinator.invoke_agent(cycle, 'business', {})
    output_2, run_id_2 = coordinator.invoke_agent(cycle, 'performance', {})

    cycle['status'] = 'failed'
    cycle['error'] = 'Falha simulada no meio do processo'
    strategy_store.save_cycle(cycle)

    # Reset mock call count to track re-runs
    mock_strategy_ai.reset_mock()

    # Resume cycle synchronously
    with patch.object(strategy_engine.executor, 'submit', side_effect=lambda fn, arg: fn(arg)):
        strategy_engine.resume(cycle_id)

    resumed = strategy_store.get_cycle(cycle_id)
    assert resumed['status'] == 'ready'

    # Check that business and performance were NOT re-called (reused from completed cache)
    called_stages = [call[0][3] for call in mock_strategy_ai.call_args_list]
    assert 'strategy_business' not in called_stages
    assert 'strategy_performance' not in called_stages
    # Later stages were called
    assert 'strategy_intent' in called_stages
    assert 'strategy_curation' in called_stages


# ===========================================================================
# 8. API Endpoints
# ===========================================================================

def test_strategy_api_roster(authed):
    resp = authed.get('/api/strategy/roster')
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 8


def test_strategy_api_cycles_and_opportunities(authed, mock_strategy_ai):
    strategy_store.init()

    # Create cycle via API
    with patch.object(strategy_engine.executor, 'submit', side_effect=lambda fn, arg: fn(arg)):
        resp = authed.post('/api/strategy/cycles', json={
            'project_id': 'proj-api',
            'focus': 'Horta e ervas em vasos',
            'budget': {'max_agent_calls': 15},
        })
        assert resp.status_code == 201
        cycle_id = resp.json()['id']

    # List cycles via API
    resp_list = authed.get('/api/strategy/cycles?project_id=proj-api')
    assert resp_list.status_code == 200
    cycles = resp_list.json()
    assert len(cycles) >= 1
    assert cycles[0]['id'] == cycle_id

    # Get cycle detail/report via API
    resp_detail = authed.get(f'/api/strategy/cycles/{cycle_id}')
    assert resp_detail.status_code == 200
    report = resp_detail.json()
    assert report['cycle']['status'] == 'ready'
    assert len(report['opportunities']) >= 1

    opp_id = report['opportunities'][0]['opportunity_id']

    # Get opportunity detail via API
    resp_opp = authed.get(f'/api/strategy/opportunities/{opp_id}')
    assert resp_opp.status_code == 200
    assert resp_opp.json()['opportunity_id'] == opp_id

    # Make decision: approve opportunity
    resp_decide = authed.post(f'/api/strategy/opportunities/{opp_id}/decision', json={
        'action': 'approve',
        'reason': 'Aprovado pelo editor-chefe para produção imediata.',
    })
    assert resp_decide.status_code == 200
    assert resp_decide.json()['status'] == 'approved'

    # Produce opportunity into an editorial job!
    with patch.object(pipeline.executor, 'submit'):
        resp_produce = authed.post(f'/api/strategy/opportunities/{opp_id}/produce')
        assert resp_produce.status_code == 200
        produce_data = resp_produce.json()
        assert produce_data['ok'] is True
        assert produce_data['job_id']
        job = db.get_job(produce_data['job_id'])
        assert job is not None
        assert job['brief']['topic'] == report['opportunities'][0]['main_question']
        assert job['opportunity_id'] == opp_id

        # Opportunity status is now 'in_progress'
        updated_opp = strategy_store.get_opportunity(opp_id)
        assert updated_opp['status'] == 'in_progress'
        assert updated_opp['job_id'] == produce_data['job_id']


def test_strategy_api_produce_with_custom_urls(authed, mock_strategy_ai):
    strategy_store.init()
    # Create an opportunity without videos
    opp_data = {
        'opportunity_id': 'opp-no-video',
        'action': 'create',
        'main_question': 'Como cultivar alecrim?',
        'queries': ['como cultivar alecrim'],
        'justification': 'Oportunidade sem vídeos pré-selecionados.',
        'selected_videos': [],
        'status': 'approved',
    }
    strategy_store.save_opportunity(opp_data, {'id': 'cycle-test'}, 'default')

    # Without URLs should return 400
    resp = authed.post('/api/strategy/opportunities/opp-no-video/produce')
    assert resp.status_code == 400

    # With explicit URLs should succeed
    with patch.object(pipeline.executor, 'submit'):
        resp_custom = authed.post('/api/strategy/opportunities/opp-no-video/produce', json={
            'urls': ['https://www.youtube.com/watch?v=12345678901'],
        })
        assert resp_custom.status_code == 200
        job_id = resp_custom.json()['job_id']
        job = db.get_job(job_id)
        assert 'https://www.youtube.com/watch?v=12345678901' in job['brief']['urls']


def test_strategy_api_404_cases(authed):
    assert authed.get('/api/strategy/cycles/nonexistent').status_code == 404
    assert authed.get('/api/strategy/opportunities/nonexistent').status_code == 404
    assert authed.post('/api/strategy/opportunities/nonexistent/decision', json={'action': 'approve'}).status_code == 404
    assert authed.post('/api/strategy/opportunities/nonexistent/produce').status_code == 404


# ===========================================================================
# 9. Service Restart Recovery
# ===========================================================================

def test_strategy_recovery_on_restart(client):
    strategy_store.init()
    cycle = {
        'id': 'cycle-interrupted-01',
        'project_id': 'default',
        'status': 'analyzing',
        'created_at': db.now(),
        'updated_at': db.now(),
        'focus': '',
        'budget': {'max_agent_calls': 15},
    }
    strategy_store.save_cycle(cycle)

    strategy_engine.recover()

    recovered = strategy_store.get_cycle('cycle-interrupted-01')
    assert recovered['status'] == 'interrupted'
    assert 'reiniciou' in recovered['error']
