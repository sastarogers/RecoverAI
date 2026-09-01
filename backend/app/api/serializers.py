"""Response shaping.

Money crosses the wire twice: `*_minor` (integer paise, authoritative) and a
preformatted display string. The frontend never does currency arithmetic.
"""

from __future__ import annotations

from typing import Any

from app.core.money import format_compact_inr, format_inr, to_major


def money(minor: int | None) -> dict:
    value = int(minor or 0)
    return {
        "minor": value,
        "major": to_major(value),
        "display": format_inr(value),
        "compact": format_compact_inr(value),
    }


def opportunity_row(opp, customer=None) -> dict:
    """A row in the opportunities table (§54)."""
    return {
        "id": str(opp.id),
        "opportunity_ref": opp.opportunity_ref,
        "scenario": opp.scenario,
        "source": opp.source,
        "customer_ref": customer.customer_ref if customer else None,
        "customer_name": customer.name if customer else None,
        "customer_segment": customer.segment if customer else None,
        "amount_at_risk": money(opp.amount_at_risk_minor),
        "recovered_amount": money(opp.recovered_amount_minor),
        "projected_retention": money(opp.projected_retention_minor),
        "failure_category": opp.failure_category,
        "failure_code": opp.failure_code,
        "reason_code": opp.reason_code,
        "status": opp.status,
        "attempt_count": opp.attempt_count,
        "notification_count": opp.notification_count,
        "detected_at": _iso(opp.detected_at),
        "recovered_at": _iso(opp.recovered_at),
        "closed_at": _iso(opp.closed_at),
        "currency": opp.currency,
    }


def ai_decision(decision) -> dict:
    return {
        "id": str(decision.id),
        "sequence": decision.sequence,
        "attempt_number": decision.attempt_number,
        "action": decision.action,
        "recovery_probability": float(decision.recovery_probability),
        "confidence": float(decision.confidence),
        "reason": decision.reason,
        "risk_level": decision.risk_level,
        "decision_source": decision.decision_source,
        "model": decision.model,
        "latency_ms": decision.latency_ms,
        "validation_errors": decision.validation_errors,
        "created_at": _iso(decision.created_at),
    }


def policy_decision(policy) -> dict:
    return {
        "id": str(policy.id),
        "ai_decision_id": str(policy.ai_decision_id),
        "verdict": policy.verdict,
        "requested_action": policy.requested_action,
        "effective_action": policy.effective_action,
        "blocked_by_rule": policy.blocked_by_rule,
        "reason": policy.reason,
        "rules_evaluated": policy.rules_evaluated,
        "created_at": _iso(policy.created_at),
    }


def attempt(att, outcome=None) -> dict:
    return {
        "id": str(att.id),
        "attempt_ref": att.attempt_ref,
        "attempt_number": att.attempt_number,
        "action": att.action,
        # Explicit links so the UI joins on identity rather than array position.
        "ai_decision_id": str(att.ai_decision_id) if att.ai_decision_id else None,
        "policy_decision_id": str(att.policy_decision_id) if att.policy_decision_id else None,
        "executor": att.executor,
        "execution_status": att.execution_status,
        "external_ref": att.external_ref,
        "executed_at": _iso(att.executed_at),
        "error": att.error,
        "outcome": (
            {
                "outcome": outcome.outcome,
                "realized_amount": money(outcome.realized_amount_minor),
                "evidence_type": outcome.evidence_type,
                "evidence_ref": outcome.evidence_ref,
                "observed_at": _iso(outcome.observed_at),
            }
            if outcome
            else None
        ),
    }


def ledger_entry(entry) -> dict:
    return {
        "id": str(entry.id),
        "entry_type": entry.entry_type,
        "scenario": entry.scenario,
        "original_amount": money(entry.original_amount_minor),
        "recovered_amount": money(entry.recovered_amount_minor),
        "action": entry.action,
        "attempt_number": entry.attempt_number,
        "source": entry.source,
        "settlement_key": entry.settlement_key,
        "settled_at": _iso(entry.settled_at),
    }


def audit_entry(entry) -> dict:
    return {
        "id": str(entry.id),
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "actor": entry.actor,
        "action": entry.action,
        "detail": entry.detail,
        "occurred_at": _iso(entry.occurred_at),
    }


def totals(t) -> dict:
    """Headline KPIs, keeping §39's three money concepts visibly distinct."""
    return {
        "revenue_at_risk": money(t.revenue_at_risk_minor),
        "recovered_revenue": money(t.recovered_revenue_minor),
        "recovery_rate": round(t.recovery_rate, 4),
        "opportunities": t.opportunities,
        "opportunities_recovered": t.opportunities_recovered,
        # Deliberately separate from recovered_revenue — an AI artifact, not money.
        "expected_recovery_value": money(t.expected_recovery_value_minor),
        "projected_retention_value": money(t.projected_retention_minor),
    }


def scenario_row(row: dict[str, Any]) -> dict:
    return {
        "scenario": row["scenario"],
        "revenue_at_risk": money(row["revenue_at_risk_minor"]),
        "recovered_revenue": money(row["recovered_revenue_minor"]),
        "recovery_rate": row["recovery_rate"],
        "opportunities": row["opportunities"],
        "opportunities_recovered": row["opportunities_recovered"],
        "projected_retention": money(row.get("projected_retention_minor", 0)),
    }


def baseline_row(row: dict[str, Any]) -> dict:
    return {
        **row,
        "recovered_revenue": money(row["recovered_revenue_minor"]),
        "revenue_at_risk": money(row["revenue_at_risk_minor"]),
        "recovery_efficiency": money(int(row.get("recovery_efficiency_minor", 0))),
    }


def simulation_run(run) -> dict:
    return {
        "id": str(run.id),
        "run_ref": run.run_ref,
        "seed": run.seed,
        "label": run.label,
        "status": run.status,
        "config": run.config,
        "progress": run.progress,
        "results": run.results,
        "ai_mode": run.ai_mode,
        "ai_model": run.ai_model,
        "engine_version": run.engine_version,
        "data_version": run.data_version,
        "policy_snapshot": run.policy_snapshot,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "duration_ms": run.duration_ms,
        "error": run.error,
        "created_at": _iso(run.created_at),
    }


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
