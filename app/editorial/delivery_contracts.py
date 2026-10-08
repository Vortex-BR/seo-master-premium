"""Require one explicit delivery per owned item without inventing missing output."""
from collections import defaultdict
from copy import deepcopy
from typing import Annotated, Literal, Union, get_args

from pydantic import BaseModel, Field, create_model

from .contracts import DraftSection, PassageAudit, PlanIssuePriority, PlanStructure, ResearchResolution, TopicComparison, TopicPlan, TopicRouting
from .reference_contracts import references


def required_object(name, identifiers, child):
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('O lote contém IDs de entrega duplicados.')
    return create_model(name, __base__=BaseModel, **{ident: (child, ...) for ident in identifiers})


def without_identifier(name, model, excluded):
    return create_model(name, __base__=BaseModel, **{
        key: (field.annotation, deepcopy(field)) for key, field in model.model_fields.items() if key != excluded})


def prepare(original, wire, payload, sources=None):
    payload = payload or {}
    if original is DraftSection:
        allowed = tuple((sources if sources is not None else payload.get('_context_sources', {})))
        source_type, _ = references(DraftSection, 'used_item_ids', allowed)
        paragraph = create_model('CitedDraftParagraph', __base__=BaseModel,
            markdown=(str, Field(min_length=1, max_length=8000, pattern=r'^(?:[^\[]|\[[^\[])*\[?$')),
            source_ids=(source_type, Field(max_length=12 if allowed else 0)))
        content = without_identifier('ParagraphDraftSection', wire, 'markdown')
        content = without_identifier('TrackedDraftSection', content, 'used_item_ids')
        ids = tuple(payload.get('section', {}).get('item_ids', []))
        usage = required_object('RequiredDraftUsage', ids, bool)
        selected = create_model('CitedDraftSection', __base__=content,
            paragraphs=(list[paragraph], Field(min_length=1, max_length=80)), usage=(usage, ...))
        return selected, {'field': 'paragraphs', 'ids': ids}
    specs = {
        TopicPlan: ('dispositions', 'item_id', 'items'),
        PassageAudit: ('assessments', 'passage_id', 'passages'),
        ResearchResolution: ('answers', 'issue_id', 'issues'),
    }
    if original in specs:
        field, identifier, input_key = specs[original]
        ids = tuple(item['id'] for item in payload.get(input_key, []))
        if not ids:
            return wire, None
        child = get_args(wire.model_fields[field].annotation)[0]
        content = without_identifier('Owned' + child.__name__, child, identifier)
        if original is PassageAudit:
            # Both states were accepted by the wire schema but rejected by the
            # coordinator: supported without evidence, and unconfirmed claims
            # counting as coverage. Encode the alternatives before generation.
            supported = create_model('SupportedPassageVerdict', __base__=content,
                status=(Literal['supported'], ...),
                evidence=(content.model_fields['evidence'].annotation, Field(min_length=1, max_length=12)))
            unconfirmed = create_model('UnconfirmedPassageVerdict', __base__=content,
                status=(Literal['not_factual', 'unsupported', 'uncertain'], ...),
                used_item_ids=(list[str], Field(max_length=0)))
            content = Union[supported, unconfirmed]
        required = required_object('Required' + field.title(), ids, content)
        selected = create_model('Covered' + wire.__name__, __base__=wire, **{field: (required, ...)})
        return selected, {'field': field, 'identifier': identifier, 'ids': ids}
    if original is PlanStructure:
        ids = tuple(item['item_id'] for item in payload.get('dispositions', []) if item['status'] == 'used')
        if not ids:
            return wire, None
        section_keys = tuple(f's{i}' for i in range(1, 31))
        section = get_args(wire.model_fields['sections'].annotation)[0]
        content = without_identifier('AssignedSectionPlan', section, 'item_ids')
        content = create_model('NamedAssignedSectionPlan', __base__=content,
                               id=(Literal[section_keys], deepcopy(section.model_fields['id'])))
        assignments = required_object('RequiredSectionAssignments', ids,
            Annotated[list[Literal[section_keys]], Field(min_length=1, max_length=30)])
        selected = create_model('CoveredPlanStructure', __base__=wire,
            sections=(list[content], deepcopy(wire.model_fields['sections'])), assignments=(assignments, ...))
        pending_ids = tuple(issue['id'] for issue in payload.get('pending', []) if issue.get('origin') == 'comparison')
        if pending_ids:
            priority = without_identifier('OwnedIssuePriority', PlanIssuePriority, 'issue_id')
            priorities = required_object('RequiredIssuePriorities', pending_ids, priority)
            selected = create_model('PrioritizedPlanStructure', __base__=selected, issue_priorities=(priorities, ...))
        return selected, {'field': 'assignments', 'ids': ids, 'pending_ids': pending_ids}
    if original is TopicRouting:
        ids = tuple(item['id'] for item in payload.get('items', []))
        if not ids:
            return wire, None
        categories = tuple(f't{i}' for i in range(1, 9))
        catalog = tuple(payload.get('catalog', []))
        topic_type = Literal[catalog] if catalog else str
        catalog_model = create_model('RequiredTopicCatalog', __base__=BaseModel,
            **{key: (topic_type | None, Field(description='Família de assuntos; null para posição não utilizada.'))
               for key in categories})
        content = create_model('OwnedTopic', __base__=BaseModel,
            category=(Literal[categories], Field(description='Chave da família editorial em catalog.')))
        required = required_object('RequiredTopics', ids, content)
        selected = create_model('CoveredTopicRouting', __base__=wire, topics=(required, ...), catalog=(catalog_model, ...))
        return selected, {'field': 'topics', 'ids': ids, 'catalog': catalog}
    if original is TopicComparison:
        ids = tuple(item['id'] for item in payload.get('items', []))
        if not ids:
            return wire, None
        row = get_args(wire.model_fields['rows'].annotation)[0]
        related_type = row.model_fields['item_ids'].annotation
        content = create_model('OwnedComparison', __base__=BaseModel,
            **{key: (field.annotation, deepcopy(field)) for key, field in row.model_fields.items() if key != 'item_ids'},
            related_item_ids=(related_type, Field(max_length=7, description=
                'IDs de outras informações relacionadas ao item da chave; vazio se a avaliação é individual.')))
        # The owner is implicit in the required key and always participates in
        # its comparison. All remaining members still use the scoped enum.
        required = required_object('RequiredComparisons', ids,
                                   Annotated[list[content], Field(min_length=1, max_length=8)])
        selected = create_model('CoveredTopicComparison', __base__=wire, rows=(required, ...))
        return selected, {'field': 'rows', 'ids': ids}
    return wire, None


def resolve(result, adapter):
    resolved = deepcopy(result)
    field = adapter['field']
    if field == 'paragraphs':
        resolved['markdown'] = '\n\n'.join(p['markdown'].strip() +
            ((' ' + ' '.join('[[' + ident + ']]' for ident in dict.fromkeys(p['source_ids'])))
             if p['source_ids'] else '') for p in resolved.pop(field))
        resolved['used_item_ids'] = [ident for ident in adapter['ids'] if resolved['usage'][ident]]
        del resolved['usage']
    elif field == 'assignments':
        from ..generation import GenerationResponseError
        sections = resolved['sections']
        index = {section['id']: section for section in sections}
        if len(index) != len(sections):
            raise GenerationResponseError('coverage_mismatch',
                'A IA repetiu identificadores de seções. O plano foi rejeitado.', retryable=True)
        for section in sections:
            section['item_ids'] = []
        for ident in adapter['ids']:
            for section_id in dict.fromkeys(resolved[field][ident]):
                if section_id not in index:
                    raise GenerationResponseError('unknown_reference',
                        'A IA destinou uma informação a uma seção ausente. O plano foi rejeitado.', retryable=True)
                index[section_id]['item_ids'].append(ident)
        del resolved[field]
        if adapter['pending_ids']:
            resolved['issue_priorities'] = [{'issue_id': ident, **resolved['issue_priorities'][ident]}
                                            for ident in adapter['pending_ids']]
    elif field == 'topics':
        from ..generation import GenerationResponseError
        catalog = result['catalog']
        groups = defaultdict(list)
        for ident in adapter['ids']:
            name = catalog[result[field][ident]['category']]
            if not name or not name.strip():
                raise GenerationResponseError('unknown_reference',
                    'A IA selecionou uma categoria editorial não definida. A entrega foi rejeitada.', retryable=True)
            groups[name].append(ident)
        resolved[field] = [{'topic': topic, 'item_ids': ids} for topic, ids in groups.items()]
        # Preserve families unused by this batch so later batches can still
        # select every family established for the complete inventory.
        resolved['catalog'] = list(adapter['catalog']) or list(dict.fromkeys(
            name for name in catalog.values() if name and name.strip()))
    elif field == 'rows':
        resolved[field] = []
        for ident in adapter['ids']:
            for row in result[field][ident]:
                related = row['related_item_ids']
                resolved[field].append({**{k: v for k, v in row.items() if k != 'related_item_ids'},
                                        'item_ids': list(dict.fromkeys([ident, *related]))})
    else:
        resolved[field] = [{adapter['identifier']: ident, **result[field][ident]} for ident in adapter['ids']]
    return resolved
