"""Deterministic post-tool response verification and rendering."""

from __future__ import annotations

from pydantic import BaseModel

from personal_agent.core.types import ActionRequest
from personal_agent.models import Coordinator
from personal_agent.tools.contracts import ToolExecutionResult
from personal_agent.tools.research import ResearchSource


class VerificationResult(BaseModel):
    success: bool
    response: str


class ResponseVerifier:
    """Prevent unsupported success claims and require evidence appropriate to each tool."""

    async def verify(
        self,
        *,
        user_input: str,
        decision_message: str,
        action: ActionRequest,
        result: ToolExecutionResult,
        coordinator: Coordinator,
    ) -> VerificationResult:
        if not result.success:
            return VerificationResult(
                success=False,
                response=(
                    f"{decision_message}\n\n"
                    f"The {action.tool_name} action failed: {result.error or 'unknown error'}."
                ),
            )
        if action.tool_name == "todoist":
            return self._verify_todoist(decision_message, action, result)
        if action.tool_name == "web_research":
            return await self._verify_research(
                user_input=user_input,
                result=result,
                coordinator=coordinator,
            )
        return VerificationResult(
            success=True,
            response=f"{decision_message}\n\nTool result verified.",
        )

    def _verify_todoist(
        self,
        decision_message: str,
        action: ActionRequest,
        result: ToolExecutionResult,
    ) -> VerificationResult:
        mutating = {"create_task", "update_task", "complete_task"}
        if action.operation in mutating and not result.external_ids:
            return VerificationResult(
                success=False,
                response="Todoist did not return an identifier, so success could not be verified.",
            )
        identifiers = ", ".join(result.external_ids) or "none"
        return VerificationResult(
            success=True,
            response=(
                f"{decision_message}\n\n"
                f"Todoist operation `{action.operation}` succeeded. Record IDs: {identifiers}."
            ),
        )

    async def _verify_research(
        self,
        *,
        user_input: str,
        result: ToolExecutionResult,
        coordinator: Coordinator,
    ) -> VerificationResult:
        sources = [
            evidence
            for evidence in result.evidence
            if evidence.kind == "web_source" and evidence.url
        ]
        if not sources:
            return VerificationResult(
                success=False,
                response="Research returned no source URLs, so the answer was not generated.",
            )
        source_records: dict[str, ResearchSource] = {}
        raw_sources = result.data.get("sources", [])
        if isinstance(raw_sources, list):
            for raw_source in raw_sources:
                try:
                    source = ResearchSource.model_validate(raw_source)
                except ValueError:
                    continue
                source_records[source.source_id] = source
        evidence_payload = []
        for evidence in sources:
            source_record = source_records.get(evidence.identifier)
            if source_record is not None and source_record.url == evidence.url:
                evidence_payload.append(source_record.model_dump(mode="json"))
            else:
                evidence_payload.append(evidence.model_dump(mode="json"))
        grounded = await coordinator.compose(user_input, evidence_payload)
        allowed_ids = {source.identifier for source in sources}
        cited_ids = set(grounded.citations)
        if not cited_ids or not cited_ids.issubset(allowed_ids):
            return VerificationResult(
                success=False,
                response="Research synthesis did not provide valid source citations.",
            )
        cited_sources = [source for source in sources if source.identifier in cited_ids]
        source_lines = "\n".join(
            f"[{source.identifier}] {source.title or source.url}: {source.url}"
            for source in cited_sources
        )
        return VerificationResult(
            success=True,
            response=(
                f"Synthesis:\n{grounded.answer}\n\n"
                f"Retrieved sources:\n{source_lines}"
            ),
        )
