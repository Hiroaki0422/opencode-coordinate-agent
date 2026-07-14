"""Deterministic post-tool response verification and rendering."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from personal_agent.core.types import ActionRequest
from personal_agent.models import ConversationTurn, Coordinator
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
        history: Sequence[ConversationTurn] = (),
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
                history=history,
            )
        if action.tool_name == "local_execution":
            return self._verify_local_execution(decision_message, action, result)
        if action.tool_name == "opencode":
            return self._verify_opencode(decision_message, result)
        return VerificationResult(
            success=True,
            response=f"{decision_message}\n\nTool result verified.",
        )

    def _verify_local_execution(
        self,
        decision_message: str,
        action: ActionRequest,
        result: ToolExecutionResult,
    ) -> VerificationResult:
        if action.operation == "create_workspace":
            path = result.data.get("path")
            if not isinstance(path, str) or path not in result.external_ids:
                return VerificationResult(
                    success=False,
                    response="Workspace creation returned no verifiable path.",
                )
            return VerificationResult(
                success=True,
                response=f"{decision_message}\n\nCreated Git workspace: {path}.",
            )
        if action.operation in {"list_files", "read_file", "run_command"}:
            stdout = str(result.data.get("stdout", ""))
            suffix = "\nOutput was truncated." if result.data.get("output_truncated") else ""
            return VerificationResult(
                success=True,
                response=f"{decision_message}\n\nSandbox output:\n{stdout}{suffix}",
            )
        return VerificationResult(
            success=True,
            response=f"{decision_message}\n\nLocal operation `{action.operation}` succeeded.",
        )

    def _verify_opencode(
        self,
        decision_message: str,
        result: ToolExecutionResult,
    ) -> VerificationResult:
        changed_files = result.data.get("changed_files")
        tests = result.data.get("tests")
        report = result.data.get("report")
        verified = result.data.get("requested_change_verified")
        if not isinstance(changed_files, list) or not changed_files or verified is not True:
            return VerificationResult(
                success=False,
                response="OpenCode returned no verified requested file changes.",
            )
        if not isinstance(tests, list) or any(
            not isinstance(test, dict) or test.get("exit_code") != 0 for test in tests
        ):
            return VerificationResult(
                success=False,
                response="OpenCode changes were produced, but requested tests did not pass.",
            )
        files = ", ".join(str(path) for path in changed_files)
        return VerificationResult(
            success=True,
            response=(
                f"{decision_message}\n\nOpenCode report:\n{report or 'Task completed.'}\n\n"
                f"Changed files: {files}. Requested tests passed."
            ),
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
        history: Sequence[ConversationTurn],
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
        grounded = await coordinator.compose(
            user_input,
            evidence_payload,
            history=history,
        )
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
