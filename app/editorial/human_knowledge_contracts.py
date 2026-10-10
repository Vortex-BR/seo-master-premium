"""Versioned shadow contracts, independent from every paid agent schema."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


KnowledgeType = Literal[
    'assertion', 'opinion', 'experience', 'observation', 'example', 'analogy',
    'method', 'step', 'proposed_cause', 'condition', 'exception', 'risk',
    'doubt', 'result', 'unknown',
]
Origin = Literal['video', 'external_verified', 'editorial_inference']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Classification(Contract):
    basis: Literal['legacy_kind', 'legacy_information_type', 'spoken_field',
                   'legacy_field', 'original_reference', 'unknown']
    status: Literal['declared', 'unknown']
    source_field: str | None = None


class LiteralCandidate(Contract):
    type: KnowledgeType
    basis: Literal['literal_cue'] = 'literal_cue'
    status: Literal['candidate'] = 'candidate'
    reference_id: str
    excerpt: str
    offset_start: int
    offset_end: int


class Timing(Contract):
    start: float | int | None
    end: float | int | None
    availability: Literal['interval', 'point', 'partial', 'unavailable']


class Anchor(Contract):
    reference_id: str | None
    status: Literal['resolved', 'missing', 'ambiguous', 'internal_only',
                    'invalid_reference', 'invalid_excerpt', 'ambiguous_excerpt', 'stale']
    source_id: str | None = None
    segment_id: str | None = None
    cue_ids: list[str] = Field(default_factory=list)
    source_hash: str | None = None
    record_hash: str | None = None
    timing: Timing | None = None
    intervals: list[dict[str, Any]] = Field(default_factory=list)
    excerpt: str | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    speaker_label: str | None = None
    speaker_metadata: Any = None
    warnings: list[str] = Field(default_factory=list)


class KnowledgeUnit(Contract):
    id: str
    legacy_item_id: str | None
    parent_unit_id: str | None = None
    type: KnowledgeType
    classification: Classification
    candidates: list[LiteralCandidate] = Field(default_factory=list)
    origin: Origin
    origin_status: Literal['anchored', 'unresolved']
    text: str
    topic: str
    source_field: str
    preserved: dict[str, Any] = Field(default_factory=dict)
    anchors: list[Anchor]
    speaker_labels: list[str]
    speaker_identity: Literal['unknown'] = 'unknown'
    article_writer: Literal['unknown'] = 'unknown'
    truth_status: Literal['not_independently_verified'] = 'not_independently_verified'


class KnowledgeRelation(Contract):
    id: str
    relation: str
    unit_ids: list[str]
    legacy_item_ids: list[str]
    explanation: str
    basis: Literal['existing_relation', 'existing_field']
    status: Literal['declared', 'unresolved']
    source_field: str
    check: dict[str, Any] | None = None


class HumanKnowledgeDossier(Contract):
    schema_version: Literal['human_knowledge.v1'] = 'human_knowledge.v1'
    projection_version: int = Field(ge=1)
    job_id: str
    mode: Literal['shadow'] = 'shadow'
    independent_source: Literal[False] = False
    source_snapshot_hash: str
    source_snapshot_version: str
    dependencies: dict[str, Any]
    apuration_status: Literal['missing', 'partial', 'current', 'stale', 'legacy_unverified']
    sources: list[dict[str, Any]]
    units: list[KnowledgeUnit]
    relations: list[KnowledgeRelation]
    coverage: dict[str, Any]
    gaps: list[str]


class HumanKnowledgeSources(Contract):
    schema_version: Literal['human_knowledge_sources.v1'] = 'human_knowledge_sources.v1'
    sources: list[dict[str, Any]]
