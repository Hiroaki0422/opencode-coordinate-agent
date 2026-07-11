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
            return ToolExecutionResult(
                tool_name=action.tool_name,
                operation=action.operation,
                success=False,
                error=f"tool adapter {action.tool_name!r} is not registered",
            )

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
        await self._audit(
            event_type="tool.completed" if result.success else "tool.failed",
            session_id=session_id,
            run_id=run_id,
            action=action,
            payload={
                "success": result.success,
                "external_ids": result.external_ids,
                "evidence_ids": [item.identifier for item in result.evidence],
                "result_digest": result_digest,
                "error": result.error,
            },
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
