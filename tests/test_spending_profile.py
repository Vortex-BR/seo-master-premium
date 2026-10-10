from copy import deepcopy

import pytest
from pydantic import ValidationError

from app import db
from app.editorial import source_processing, store
from app.editorial.contracts import VoiceProfile


def test_profile_default_is_one_dollar_and_never_permits_more_than_one():
    assert VoiceProfile().max_spend_usd == 1.00
    assert VoiceProfile(max_spend_usd=1).max_spend_usd == 1
    for amount in (0, -1, 1.01, float('inf'), float('nan')):
        with pytest.raises(ValidationError):
            VoiceProfile(max_spend_usd=amount)


def test_old_forced_eight_call_profile_migrates_without_losing_voice(authed):
    original = VoiceProfile(tone='Uma voz editorial própria.').model_dump()
    original.pop('max_spend_usd')
    original.update(max_calls=8, max_rounds=5)
    db.set_setting('editorial_profile', original)
    profile = authed.get('/api/editorial/profile').json()['profile']
    assert profile['tone'] == original['tone']
    assert profile['max_calls'] == 24 and profile['max_rounds'] == 0
    assert profile['max_spend_usd'] == 1.00
    # Saved historical records are not rewritten by reading the migrated profile.
    assert db.get_setting('editorial_profile')['max_calls'] == 8


def test_explicit_new_safety_limit_is_preserved_and_legacy_large_limits_are_bounded():
    profile = VoiceProfile(max_calls=8, max_spend_usd=0.25).model_dump()
    assert store.bounded_profile(profile)['max_calls'] == 8
    assert store.bounded_profile({**profile, 'max_calls': 120, 'max_spend_usd': 5})['max_spend_usd'] == 1
    assert store.bounded_profile({**profile, 'max_calls': 120})['max_calls'] == 24


def test_profile_api_rejects_excess_budget_and_accepts_conservative_limit(authed):
    before = authed.get('/api/editorial/profile').json()
    body = {'base_version': before['version'], 'profile': {**before['profile'], 'max_spend_usd': 1.01}}
    assert authed.put('/api/editorial/profile', json=body).status_code == 422
    assert authed.get('/api/editorial/profile').json() == before
    body['profile']['max_spend_usd'] = 0.25
    response = authed.put('/api/editorial/profile', json=body)
    assert response.status_code == 200
    assert response.json()['profile']['max_spend_usd'] == 0.25


def test_money_and_safety_limits_do_not_change_editorial_voice():
    profile = {'profile': VoiceProfile().model_dump(), 'version': 'first', 'brand_name': 'Marca'}
    changed = deepcopy(profile)
    changed['profile'].update(max_calls=12, max_spend_usd=0.10)
    changed['version'] = 'second'
    assert store.voice(profile) == store.voice(changed)
    assert 'max_spend_usd' not in store.voice(profile)['profile']


def test_estimate_exposes_lifetime_budget_instead_of_eight_call_ceiling(job, monkeypatch):
    from app import spending

    totals = {'limit_usd': 0.50, 'spent_usd': 0.08, 'reserved_usd': 0, 'remaining_usd': 0.42,
              'accounting_notice': 'Custos contabilizados.'}
    def read_summary(current, *, persist):
        assert persist is False
        return totals

    monkeypatch.setattr(spending, 'summary', read_summary)
    estimate = source_processing.estimate(job, VoiceProfile().model_dump())
    assert estimate['max_calls'] == 24
    assert estimate['spending'] == totals
    assert 'não é o limite financeiro' in estimate['notice']
    job['brief']['research'] = True
    assert source_processing.estimate(job, VoiceProfile().model_dump())['estimated_calls_max'] == 8
