"""Require one explicit delivery per owned item without inventing missing output."""
from collections import defaultdict
from copy import deepcopy
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, Field, create_model

from .contracts import PassageAudit, ResearchResolution, TopicComparison, TopicPlan, TopicRouting


def required_object(name, identifiers, child):
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('O lote contém IDs de entrega duplicados.')
    return create_model(name, __base__=BaseModel, **{ident: (child, ...) for ident in identifiers})


def without_identifier(name, model, excluded):
    return create_model(name, __base__=BaseModel, **{
        key: (field.annotation, deepcopy(field)) for key, field in model.model_fields.items() if key != excluded})


def prepare(original, wire, payload):
    payload = payload or {}
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
        required = required_object('Required' + field.title(), ids, content)
        selected = create_model('Covered' + wire.__name__, __base__=wire, **{field: (required, ...)})
        return selected, {'field': field, 'identifier': identifier, 'ids': ids}
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
    if field == 'topics':
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
