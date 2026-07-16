"""Audited registry and execution boundary for external tools."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from personal_agent.core.types import ActionRequest
from personal_agent.persistence import Database
from personal_agent.tools.contracts import ToolAdapter, ToolExecutionResult


class ToolGateway:
    """Resolve adapters by name and audit each execution attempt."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._adapters: dict[str, ToolAdapter] = {}

    def register(self, adapter: ToolAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"tool adapter {adapter.name!r} is already registered")
        self._adapters[adapter.name] = adapter

    def has_tool(self, name: str) -> bool:
        return name in self._adapters

    async def execute(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        action: ActionRequest,
    ) -> ToolExecutionResult:
        adapter = self._adapters.get(action.tool_name)
        if adapter is None:
            result = ToolExecutionResult(
                tool_name=action.tool_name,
                operation=action.operation,
                success=False,
                error=f"tool adapter {action.tool_name!r} is not registered",
            )
            result_digest = hashlib.sha256(
                json.dumps(result.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
            await self._persist_result(
                session_id=session_id,
                run_id=run_id,
                action=action,
                result=result,
                result_digest=result_digest,
            )
            return result

        await self._audit(
            event_type="tool.started",
            session_id=session_id,
            run_id=run_id,
            action=action,
            payload={"summary": action.summary},
        )
        try:
            result = await adapter.execute(action)
        except Exception as error:  # adapters convert expected provider errors themselves
            result = ToolExecutionResult(
                tool_name=action.tool_name,
                operation=action.operation,
                success=False,
                error=f"unexpected {type(error).__name__}",
            )

        result_digest = hashlib.sha256(
            json.dumps(result.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        await self._persist_result(
            session_id=session_id,
            run_id=run_id,
            action=action,
            result=result,
            result_digest=result_digest,
        )
        return result

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()

    async def _audit(
        self,
        *,
        event_type: str,
        session_id: UUID,
        run_id: UUID,
        action: ActionRequest,
        payload: dict[str, object],
    ) -> None:
        async with self._database.unit_of_work() as unit_of_work:
            await unit_of_work.audit.append(
                event_type=event_type,
                actor="tool_gateway",
                session_id=session_id,
                run_id=run_id,
                payload={
                    "action_id": str(action.action_id),
                    "tool_name": action.tool_name,
                    "operation": action.operation,
                    "resource": action.resource,
                    **payload,
                },
            )
            await unit_of_work.commit()

    async def _persist_result(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        action: ActionRequest,
        result: ToolExecutionResult,
        result_digest: str,
    ) -> None:
        payload = self._receipt_payload(result)
        async with self._database.unit_of_work() as unit_of_work:
            audit_event = await unit_of_work.audit.append(
                event_type="tool.completed" if result.success else "tool.failed",
                actor="tool_gateway",
                session_id=session_id,
                run_id=run_id,
                payload={
                    "action_id": str(action.action_id),
                    "tool_name": action.tool_name,
                    "operation": action.operation,
                    "resource": action.resource,
                    "success": result.success,
                    "external_ids": result.external_ids,
                    "evidence_ids": [item.identifier for item in result.evidence],
                    "result_digest": result_digest,
                    "adapter": result.audit_data,
                    "error": result.error,
                },
            )
            await unit_of_work.operation_receipts.create(
                session_id=session_id,
                run_id=run_id,
                action=action,
                audit_event_id=audit_event.id,
                success=result.success,
                outcome=self._outcome(result),
                payload=payload,
            )
            workspace = self._result_workspace(result)
            if workspace is not None:
                await unit_of_work.session_workspaces.set(session_id, workspace)
            await unit_of_work.commit()

    @staticmethod
    def _outcome(result: ToolExecutionResult) -> str:
        if result.success:
            return "succeeded"
        if result.data.get("effect_observed") is True:
            return "partial"
        return "failed"

    @staticmethod
    def _result_workspace(result: ToolExecutionResult) -> str | None:
        value = result.data.get("workspace") or result.data.get("path")
        if not isinstance(value, str):
            return None
        if result.success or result.data.get("effect_observed") is True:
            return value
        return None

    @staticmethod
    def _receipt_payload(result: ToolExecutionResult) -> dict[str, object]:
        payload: dict[str, object] = {
            "error": result.error,
            "external_ids": result.external_ids[:100],
        }
        if result.tool_name != "opencode":
            if isinstance(result.data.get("workspace"), str):
                payload["workspace"] = result.data["workspace"]
            if isinstance(result.data.get("path"), str):
                payload["workspace"] = result.data["path"]
            if isinstance(result.data.get("exit_code"), int):
                payload["exit_code"] = result.data["exit_code"]
            return payload
        for key in (
            "workspace",
            "model",
            "changed_files",
            "diff_summary",
            "diff",
            "report",
            "worker_events",
            "stdout_tail",
            "stderr_tail",
            "baseline_dirty",
            "expected_files",
            "missing_expected_files",
            "effect_observed",
            "requested_change_verified",
            "tests_passed",
            "verification_reason",
            "changes_retained",
        ):
            if key in result.data:
                payload[key] = result.data[key]
        tests = result.data.get("tests")
        if isinstance(tests, list):
            payload["tests"] = [
                {
                    key: test[key]
                    for key in (
                        "command",
                        "exit_code",
                        "stdout_digest",
                        "stderr_digest",
                        "output_truncated",
                    )
                    if isinstance(test, dict) and key in test
                }
                for test in tests[:10]
                if isinstance(test, dict)
            ]
        commands = result.audit_data.get("commands")
        if isinstance(commands, list):
            payload["command_evidence"] = commands[:25]
        return payload
