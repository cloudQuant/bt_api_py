"""SDK-neutral position-evidence selection and currentness workflow.

The caller supplies error translation and redaction; source authority
remains with the selected provider port and its lease validation.
"""

from __future__ import annotations

from datetime import datetime, timezone


def read_order_position_evidence(api, route, instrument, session, quantity_unit, *,
                                 sanitize_exception, missing_dependency_error_type):
    """Read and revalidate only SDK-issued neutral position evidence."""
    try:
        from bt_api_py.facade_protocol import (
            OrderPositionEvidence,
            OrderPositionEvidencePort,
            OrderPositionEvidenceSelector,
            OrderPositionEvidenceUnavailable,
            OrderPositionQuantities,
        )
    except ImportError as exc:
        raise missing_dependency_error_type(
            "SDK order-position evidence contracts are required"
        ) from exc

    if not isinstance(api, OrderPositionEvidenceSelector):
        return None, None, "position_evidence_capability_unavailable"
    try:
        evidence_port = api.order_position_evidence(route)
    except OrderPositionEvidenceUnavailable as exc:
        return None, None, exc.code.lower()
    except Exception as exc:
        sanitize_exception(exc)
        return None, None, "position_evidence_route_unavailable"
    if not isinstance(evidence_port, OrderPositionEvidencePort):
        return None, None, "position_evidence_capability_unavailable"

    try:
        evidence = evidence_port.snapshot(instrument, timeout=5.0)
    except OrderPositionEvidenceUnavailable as exc:
        return evidence_port, None, exc.code.lower()
    except Exception as exc:
        sanitize_exception(exc)
        return evidence_port, None, "position_evidence_provider_unavailable"
    if type(evidence) is not OrderPositionEvidence:
        return evidence_port, None, "position_evidence_projection_invalid"
    try:
        OrderPositionEvidence.__post_init__(evidence)
        if type(evidence.quantities) is not OrderPositionQuantities:
            return evidence_port, None, "position_evidence_projection_invalid"
        OrderPositionQuantities.__post_init__(evidence.quantities)
    except Exception:
        return evidence_port, None, "position_evidence_projection_invalid"

    quantities = evidence.quantities
    if quantities.instrument is not instrument:
        return evidence_port, None, "position_evidence_instrument_mismatch"
    if evidence.session_generation != session.generation:
        return evidence_port, None, "position_evidence_session_mismatch"
    if (
        type(quantity_unit) is not str
        or not quantity_unit
        or quantity_unit != quantity_unit.strip()
        or quantities.qty_unit != quantity_unit
    ):
        return evidence_port, None, "position_evidence_quantity_unit_mismatch"

    try:
        verified = evidence_port.validate(evidence, now=datetime.now(timezone.utc))
    except Exception as exc:
        sanitize_exception(exc)
        return evidence_port, None, "position_evidence_lease_unavailable"
    if type(verified) is not bool or verified is not True:
        return evidence_port, None, "position_evidence_lease_not_current"
    return evidence_port, evidence, None


__all__ = ("read_order_position_evidence",)
