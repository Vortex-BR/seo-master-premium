"""Restrict editorial references to the exact inventory supplied for each task."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, create_model

from .contracts import (ClaimDisposition, ComparisonRow, DraftArticle, DraftSection, EditorialPlan, PassageAudit,
                        PlanStructure, ResearchResolution, SectionPlan, SourceRelation, TopicComparison,
                        TopicGroup, TopicPlan, TopicRouting, VideoContext)


def scalar(model, field, identifiers):
    ids = tuple(dict.fromkeys(identifiers))
    if not ids:
        raise ValueError('Uma referência obrigatória não tem IDs disponíveis nesta etapa.')
    return Literal[ids], deepcopy(model.model_fields[field])


def references(model, field, identifiers):
    ids = tuple(dict.fromkeys(identifiers))
    if not ids:
        # Empty inventories must never become unrestricted strings.
        return list[str], Field(max_length=0)
    return list[Literal[ids]], deepcopy(model.model_fields[field])


def nested(model, field, child):
    return list[child], deepcopy(model.model_fields[field])


def scope(original, wire, payload):
    if original not in (VideoContext, TopicRouting, TopicComparison, TopicPlan,
                         PlanStructure, EditorialPlan, DraftArticle, DraftSection, PassageAudit, ResearchResolution):
        return wire
    payload = payload or {}
    items = payload.get('items', [])
    item_ids = [item['id'] for item in items]
    fields = {}
    if original is VideoContext:
        allowed = [item['id'] for item in payload.get('video_index', items)]
        relation = create_model('TaskSourceRelation', __base__=SourceRelation,
                                item_ids=references(SourceRelation, 'item_ids', allowed))
        fields['relations'] = nested(wire, 'relations', relation)
    elif original is TopicRouting:
        group = create_model('TaskTopicGroup', __base__=TopicGroup,
                             item_ids=references(TopicGroup, 'item_ids', item_ids))
        fields['topics'] = nested(wire, 'topics', group)
    elif original is TopicComparison:
        allowed = [item['id'] for item in payload.get('topic_index', items)]
        row = create_model('TaskComparisonRow', __base__=ComparisonRow,
                           item_ids=references(ComparisonRow, 'item_ids', allowed))
        fields['rows'] = nested(wire, 'rows', row)
    elif original in (TopicPlan, EditorialPlan):
        supported = [item['id'] for item in items if item['check']['status'] == 'supported']
        section = create_model('TaskSectionPlan', __base__=SectionPlan,
                               item_ids=references(SectionPlan, 'item_ids', supported))
        disposition = create_model('TaskClaimDisposition', __base__=ClaimDisposition,
                                   item_id=scalar(ClaimDisposition, 'item_id', item_ids))
        fields['sections'] = nested(wire, 'sections', section)
        fields['dispositions'] = nested(wire, 'dispositions', disposition)
    elif original is PlanStructure:
        allowed = [item['item_id'] for item in payload.get('dispositions', []) if item['status'] == 'used']
        section = create_model('TaskSectionPlan', __base__=SectionPlan,
                               item_ids=references(SectionPlan, 'item_ids', allowed))
        fields['sections'] = nested(wire, 'sections', section)
    elif original in (DraftSection, DraftArticle):
        allowed = payload.get('section', {}).get('item_ids', item_ids)
        fields['used_item_ids'] = references(wire, 'used_item_ids', allowed)
    elif original is PassageAudit:
        passages = [part['id'] for part in payload.get('passages', [])]
        if passages:
            # Preserve the previously selected evidence contract of each assessment.
            assessment = wire.model_fields['assessments'].annotation.__args__[0]
            assessed = create_model('TaskPassageAssessment', __base__=assessment,
                passage_id=scalar(assessment, 'passage_id', passages),
                used_item_ids=references(assessment, 'used_item_ids', item_ids))
            fields['assessments'] = nested(wire, 'assessments', assessed)
    elif original is ResearchResolution:
        issues = [issue['id'] for issue in payload.get('issues', [])]
        if issues:
            answer = wire.model_fields['answers'].annotation.__args__[0]
            assessed = create_model('TaskResearchAnswer', __base__=answer,
                issue_id=scalar(answer, 'issue_id', issues))
            fields['answers'] = nested(wire, 'answers', assessed)
    return create_model('Task' + wire.__name__, __base__=wire, **fields) if fields else wire
