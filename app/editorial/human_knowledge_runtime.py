"""Optional shadow diagnostics never participate in editorial delivery decisions."""
import logging

from .. import cost_observability
from . import human_knowledge


logger = logging.getLogger(__name__)


def observe(job, *, phase='runtime'):
    """Persist a local sidecar without changing a job, request, budget or article.

    Ordinary diagnostic failures are visible in telemetry and logs, but cannot
    discard a paid delivery. Real cancellation propagates. Zero here measures
    additional API work, never CPU/storage/infrastructure or a provider invoice.
    """
    try:
        if human_knowledge.mode() == 'off':
            return None
        with cost_observability.external_attempt(
            job, 'human_knowledge_shadow', provider='application', origin='local_processing',
            metadata={'phase': phase, 'incremental_ai_requests': 0,
                      'incremental_input_tokens': 0, 'incremental_output_tokens': 0,
                      'cost_basis': 'no_provider_operation_in_shadow',
                      'article_modified': False, 'export_blocking': False},
        ) as event:
            event.update(calculated_usd=0.0, infrastructure_usd=None)
            artifact = human_knowledge.persist_shadow(job)
            event['state'] = 'completed'
            if artifact is not None:
                event['metadata']['artifact_version'] = artifact['version']
            return artifact
    except Exception as exc:
        # Source text, validation dumps and provider secrets do not enter logs.
        logger.warning('Human Knowledge shadow unavailable: %s', type(exc).__name__)
        return None
