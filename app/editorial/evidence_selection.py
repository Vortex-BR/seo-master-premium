"""Select source spans by ID; source quotations are copied only by the server."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field, create_model

from .contracts import BlockKnowledge, ItemCheck, KnowledgeAudit, KnowledgeItem, PassageAssessment, PassageAudit
from .source_processing import parts


def prepare(schema, sources, originals):
    if schema not in (BlockKnowledge, PassageAudit):
        return schema, {}
    options = {}
    for source_id, source in sources.items():
        # Review contexts can contain disjoint windows. Never turn the marker
        # between windows into an original quote, or include unseen source text.
        spans = source.get('original_spans')
        texts = ([originals[source_id]['text'][start:end] for start, end in spans]
                 if spans is not None else [source['text']])
        for text in texts:
            for _, _, excerpt in parts(text, 700):
                if excerpt.strip():
                    options[f'e{len(options) + 1}'] = {'source_id': source_id, 'excerpt': excerpt}
    if not options:
        return schema, {}
    choice = create_model('EvidenceSelection', __base__=BaseModel,
                          reference=(Literal[tuple(options)], Field(description=
                              'ID de evidence_options que sustenta a afirmação; não escreva a citação.')))
    if schema is BlockKnowledge:
        item = create_model('SelectedKnowledgeItem', __base__=KnowledgeItem,
                            evidence=(list[choice], Field(min_length=1, max_length=8)))
        selected = create_model('SelectedBlockKnowledge', __base__=BlockKnowledge,
                                items=(list[item], Field(max_length=30)))
    else:
        item = create_model('SelectedPassageAssessment', __base__=PassageAssessment,
                            evidence=(list[choice], Field(max_length=12)))
        selected = create_model('SelectedPassageAudit', __base__=PassageAudit,
                                assessments=(list[item], ...))
    return selected, options


def resolve(result, schema, options):
    resolved = deepcopy(result)
    for item in resolved['items' if schema is BlockKnowledge else 'assessments']:
        item['evidence'] = [dict(options[ref['reference']]) for ref in item['evidence']]
    return schema.model_validate(resolved).model_dump()


def prepare_audit(schema, payload):
    """Required keys make coverage explicit even when inputs contain nested IDs."""
    if schema is not KnowledgeAudit:
        return schema, ()
    identifiers = tuple(item['id'] for item in (payload or {}).get('items', []))
    if not identifiers:
        return schema, ()
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('O lote de conferência contém IDs duplicados.')
    assessment = create_model('RequiredItemAssessment', __base__=BaseModel,
        **{key: (ItemCheck.model_fields[key].annotation, deepcopy(ItemCheck.model_fields[key]))
           for key in ('status', 'reason')})
    checks = create_model('RequiredItemChecks', __base__=BaseModel,
                          **{ident: (assessment, ...) for ident in identifiers})
    selected = create_model('RequiredKnowledgeAudit', __base__=KnowledgeAudit, checks=(checks, ...))
    return selected, identifiers


def resolve_audit(result, identifiers):
    return KnowledgeAudit.model_validate({**result, 'checks': [
        {'item_id': ident, **result['checks'][ident]} for ident in identifiers]}).model_dump()
