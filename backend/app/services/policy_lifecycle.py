"""Measured promotion, rejection, and rollback for immutable policy candidates."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..contracts import PolicyCandidateDecisionRequest, PolicyCandidateDecisionValue, PolicyCandidateRollbackRequest
from ..db import SessionLocal
from ..models import (
    AuditEvent,
    EvaluationRunRecord,
    ModelRegistrationRecord,
    PolicyCandidateDecisionRecord,
    PolicyTrainingRunRecord,
)
from ..util import new_id
from . import command_store, control_catalog


MIN_PROMOTION_EVALUATIONS = max(1, int(os.environ.get("ROBOTWORLD_POLICY_PROMOTION_MIN_EVALUATIONS", "3")))


class PolicyLifecycleError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_path(value: str | None) -> str | None:
    if not value:
        return None
    return os.path.normcase(str(Path(value).resolve(strict=False)))


def decision_view(row: PolicyCandidateDecisionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "revision": row.revision,
        "trainingRunId": row.training_run_id,
        "candidateModelId": row.candidate_model_id,
        "previousModelId": row.previous_model_id,
        "lifecycleState": row.lifecycle_state,
        "evaluationIds": list(row.evaluation_ids or []),
        "evidence": dict(row.evidence or {}),
        "reason": row.reason,
        "commandId": row.command_id,
        "error": row.error,
        "createdBy": row.created_by,
        "promotedAt": row.promoted_at,
        "rolledBackAt": row.rolled_back_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _evaluation_summary(row: EvaluationRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "success": row.success,
        "failureCode": row.failure_code,
        "policy": row.policy,
        "seed": row.seed,
        "robotId": row.robot_id,
        "worldTemplateId": row.world_template_id,
        "traceId": row.trace_id,
    }


async def decide(
    request: PolicyCandidateDecisionRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="policy.candidate.decide",
        target_type="training_run",
        target_id=request.training_run_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    decision_id = new_id("policydecision")
    try:
        async with SessionLocal() as session:
            training = await session.get(PolicyTrainingRunRecord, request.training_run_id)
            candidate = await session.get(ModelRegistrationRecord, request.candidate_model_id)
            previous = await session.get(ModelRegistrationRecord, request.previous_model_id)
            existing = (
                await session.execute(
                    select(PolicyCandidateDecisionRecord).where(
                        PolicyCandidateDecisionRecord.training_run_id == request.training_run_id
                    )
                )
            ).scalar_one_or_none()
            if training is None or candidate is None or previous is None:
                raise KeyError("training run or model registration")
            if existing is not None:
                raise PolicyLifecycleError(
                    f"Training run already has policy decision {existing.id}/{existing.lifecycle_state}."
                )
            if training.lifecycle_state != "SUCCEEDED" or not training.candidate_checkpoint_sha256:
                raise PolicyLifecycleError("Only a successfully materialized immutable candidate can be decided.")
            if training.base_model_id != previous.id:
                raise PolicyLifecycleError("previousModelId does not match the training run base model.")
            if _normalized_path(training.candidate_checkpoint_path) != _normalized_path(candidate.local_path):
                raise PolicyLifecycleError("Candidate model path does not match the training-run checkpoint.")
            evaluations: list[EvaluationRunRecord] = []
            for evaluation_id in request.evaluation_ids:
                evaluation = await session.get(EvaluationRunRecord, evaluation_id)
                if evaluation is None:
                    raise KeyError(evaluation_id)
                if f":{candidate.id}:" not in evaluation.policy:
                    raise PolicyLifecycleError(
                        f"Evaluation {evaluation.id} did not execute candidate {candidate.id}."
                    )
                if evaluation.status not in {"SUCCEEDED", "FAILED", "CANCELLED", "CRASHED"}:
                    raise PolicyLifecycleError(f"Evaluation {evaluation.id} is not terminal.")
                evaluations.append(evaluation)
            summaries = [_evaluation_summary(item) for item in evaluations]
            failures = [item for item in evaluations if item.success is not True]
            if request.decision == PolicyCandidateDecisionValue.REJECT:
                if not failures:
                    raise PolicyLifecycleError("Rejection requires at least one measured failed candidate evaluation.")
                lifecycle = "REJECTED"
            else:
                unique_seeds = {item.seed for item in evaluations}
                if len(evaluations) < MIN_PROMOTION_EVALUATIONS or len(unique_seeds) < MIN_PROMOTION_EVALUATIONS:
                    raise PolicyLifecycleError(
                        f"Promotion requires at least {MIN_PROMOTION_EVALUATIONS} successful evaluations with distinct seeds."
                    )
                if failures:
                    raise PolicyLifecycleError("Promotion is blocked because candidate evaluation evidence contains a failure.")
                lifecycle = "ACTIVATING"
            evidence = {
                "gateRevision": "robotworld.policy-promotion-gates.v1",
                "minimumPromotionEvaluations": MIN_PROMOTION_EVALUATIONS,
                "candidateCheckpointSha256": training.candidate_checkpoint_sha256,
                "evaluations": summaries,
                "passed": request.decision == PolicyCandidateDecisionValue.REJECT or not failures,
            }
            row = PolicyCandidateDecisionRecord(
                id=decision_id,
                revision=1,
                training_run_id=training.id,
                candidate_model_id=candidate.id,
                previous_model_id=previous.id,
                lifecycle_state=lifecycle,
                evaluation_ids=list(request.evaluation_ids),
                evidence=evidence,
                reason=request.reason,
                command_id=command.id,
                created_by=actor,
            )
            session.add(row)
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="policy_candidate",
                    entity_id=row.id,
                    action="policy.candidate.reject" if lifecycle == "REJECTED" else "policy.candidate.activate.start",
                    from_state="CANDIDATE",
                    to_state=lifecycle,
                    detail=evidence,
                    actor=actor,
                )
            )
            training.validation = {
                **dict(training.validation or {}),
                "policyDecision": {"id": row.id, "state": lifecycle, "evaluationIds": list(request.evaluation_ids)},
            }
            await session.commit()
            initial_view = decision_view(row)

        if request.decision == PolicyCandidateDecisionValue.PROMOTE:
            await _activate_candidate(decision_id, actor=actor)
            async with SessionLocal() as session:
                promoted = await session.get(PolicyCandidateDecisionRecord, decision_id)
                if promoted is None:
                    raise PolicyLifecycleError("Policy decision disappeared during activation.")
                initial_view = decision_view(promoted)
        output = {"policyDecision": initial_view}
        await command_store.finish_command(command.id, output=output)
        command.output = command_store.json_safe(output)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    except Exception as exc:
        async with SessionLocal() as session:
            row = await session.get(PolicyCandidateDecisionRecord, decision_id)
            if row is not None and row.lifecycle_state == "ACTIVATING":
                row.lifecycle_state = "ACTIVATION_FAILED"
                row.error = str(exc)
                await session.commit()
        await command_store.finish_command(command.id, error=str(exc))
        raise


async def _activate_candidate(decision_id: str, *, actor: str) -> None:
    async with SessionLocal() as session:
        decision = await session.get(PolicyCandidateDecisionRecord, decision_id)
        if decision is None or decision.lifecycle_state != "ACTIVATING":
            raise PolicyLifecycleError("Policy candidate is not awaiting activation.")
        candidate = await session.get(ModelRegistrationRecord, decision.candidate_model_id)
        previous = await session.get(ModelRegistrationRecord, decision.previous_model_id)
        if candidate is None or previous is None:
            raise KeyError("candidate or previous model")
        previous_loaded = previous.lifecycle_state == "LOADED"
        candidate_state = candidate.lifecycle_state
    try:
        if previous_loaded:
            await control_catalog.unload_model(
                previous.id, idempotency_key=f"{decision_id}:unload-previous", actor=actor
            )
        if candidate_state == "AVAILABLE":
            await control_catalog.load_model(
                candidate.id, idempotency_key=f"{decision_id}:load-candidate", actor=actor
            )
        elif candidate_state != "LOADED":
            raise PolicyLifecycleError(f"Candidate model must be AVAILABLE or LOADED, not {candidate_state}.")
    except Exception:
        async with SessionLocal() as session:
            candidate_now = await session.get(ModelRegistrationRecord, decision.candidate_model_id)
            previous_now = await session.get(ModelRegistrationRecord, decision.previous_model_id)
        if candidate_now is not None and candidate_now.lifecycle_state == "LOADED":
            await control_catalog.unload_model(
                candidate_now.id, idempotency_key=f"{decision_id}:recovery-unload-candidate", actor="system"
            )
        if previous_loaded and previous_now is not None and previous_now.lifecycle_state == "AVAILABLE":
            await control_catalog.load_model(
                previous_now.id, idempotency_key=f"{decision_id}:recovery-load-previous", actor="system"
            )
        raise
    async with SessionLocal() as session:
        decision = await session.get(PolicyCandidateDecisionRecord, decision_id)
        if decision is None:
            raise KeyError(decision_id)
        decision.lifecycle_state = "PROMOTED"
        decision.promoted_at = _now()
        session.add(
            AuditEvent(
                command_id=decision.command_id,
                entity_type="policy_candidate",
                entity_id=decision.id,
                action="policy.candidate.promote",
                from_state="ACTIVATING",
                to_state="PROMOTED",
                detail={"activeModelId": decision.candidate_model_id, "rollbackModelId": decision.previous_model_id},
                actor=actor,
            )
        )
        await session.commit()


async def rollback(
    request: PolicyCandidateRollbackRequest,
    *,
    idempotency_key: str | None,
    actor: str = "user",
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    command, reused = await command_store.start_command(
        kind="policy.candidate.rollback",
        target_type="policy_candidate",
        target_id=request.decision_id,
        payload=payload,
        idempotency_key=idempotency_key,
        actor=actor,
    )
    if reused:
        return command_store.command_view(command, reused=True)
    try:
        async with SessionLocal() as session:
            decision = await session.get(PolicyCandidateDecisionRecord, request.decision_id)
            if decision is None:
                raise KeyError(request.decision_id)
            if decision.lifecycle_state != "PROMOTED":
                raise PolicyLifecycleError(f"Only a PROMOTED policy can roll back, not {decision.lifecycle_state}.")
            candidate = await session.get(ModelRegistrationRecord, decision.candidate_model_id)
            previous = await session.get(ModelRegistrationRecord, decision.previous_model_id)
            if candidate is None or previous is None:
                raise KeyError("candidate or previous model")
            candidate_state = candidate.lifecycle_state
            previous_state = previous.lifecycle_state
        if candidate_state == "LOADED":
            await control_catalog.unload_model(
                candidate.id, idempotency_key=f"{decision.id}:rollback-unload-candidate", actor=actor
            )
        if previous_state == "AVAILABLE":
            await control_catalog.load_model(
                previous.id, idempotency_key=f"{decision.id}:rollback-load-previous", actor=actor
            )
        elif previous_state != "LOADED":
            raise PolicyLifecycleError(f"Rollback model must be AVAILABLE or LOADED, not {previous_state}.")
        async with SessionLocal() as session:
            decision = await session.get(PolicyCandidateDecisionRecord, request.decision_id)
            if decision is None:
                raise KeyError(request.decision_id)
            decision.lifecycle_state = "ROLLED_BACK"
            decision.rolled_back_at = _now()
            decision.reason = f"{decision.reason}\nRollback: {request.reason}"
            session.add(
                AuditEvent(
                    command_id=command.id,
                    entity_type="policy_candidate",
                    entity_id=decision.id,
                    action="policy.candidate.rollback",
                    from_state="PROMOTED",
                    to_state="ROLLED_BACK",
                    detail={"restoredModelId": decision.previous_model_id, "reason": request.reason},
                    actor=actor,
                )
            )
            await session.commit()
            view = decision_view(decision)
        output = {"policyDecision": view}
        await command_store.finish_command(command.id, output=output)
        command.output = command_store.json_safe(output)
        command.status = "SUCCEEDED"
        return command_store.command_view(command)
    except Exception as exc:
        await command_store.finish_command(command.id, error=str(exc))
        raise


async def list_decisions(limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(PolicyCandidateDecisionRecord)
                .order_by(PolicyCandidateDecisionRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).scalars().all()
    return [decision_view(row) for row in rows]
