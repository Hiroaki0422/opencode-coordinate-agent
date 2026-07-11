"""Todoist API v1 task provider and audited tool adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.tools.contracts import ToolEvidence, ToolExecutionResult


class TaskProviderError(RuntimeError):
    """Sanitized Todoist provider failure."""


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    content: str
    description: str = ""
    project_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    priority: int = 1
    due: dict[str, Any] | None = None
    checked: bool = False


class ProjectRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str


class TaskCreate(BaseModel):
    content: str = Field(min_length=1)
    description: str | None = None
    project_id: str | None = None
    due_string: str | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    labels: list[str] | None = None


class TaskUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    description: str | None = None
    due_string: str | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    labels: list[str] | None = None


class TaskProvider(Protocol):
    async def list_tasks(self, *, project_id: str | None = None) -> list[TaskRecord]: ...

    async def list_projects(self) -> list[ProjectRecord]: ...

    async def find_project(self, query: str) -> ProjectRecord: ...

    async def create_task(self, task: TaskCreate) -> TaskRecord: ...

    async def update_task(self, task_id: str, update: TaskUpdate) -> TaskRecord: ...

    async def complete_task(self, task_id: str) -> str: ...

    async def aclose(self) -> None: ...


class TodoistTaskProvider:
    """Small typed client for Todoist's cursor-paginated API v1."""

    def __init__(
        self,
        *,
        api_token: str,
        base_url: str = "https://api.todoist.com/api/v1",
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._authorization = f"Bearer {api_token}"
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def list_tasks(self, *, project_id: str | None = None) -> list[TaskRecord]:
        params = {"project_id": project_id} if project_id else {}
        records = await self._get_all_pages("/tasks", params=params)
        return [TaskRecord.model_validate(item) for item in records]

    async def list_projects(self) -> list[ProjectRecord]:
        records = await self._get_all_pages("/projects")
        return [ProjectRecord.model_validate(item) for item in records]

    async def find_project(self, query: str) -> ProjectRecord:
        projects = await self.list_projects()
        normalized = query.casefold()
        exact = [
            project
            for project in projects
            if project.id == query or project.name.casefold() == normalized
        ]
        if len(exact) == 1:
            return exact[0]
        partial = [project for project in projects if normalized in project.name.casefold()]
        if len(partial) == 1:
            return partial[0]
        if not exact and not partial:
            raise TaskProviderError(f"Todoist project {query!r} was not found")
        raise TaskProviderError(f"Todoist project query {query!r} is ambiguous")

    async def create_task(self, task: TaskCreate) -> TaskRecord:
        response = await self._request(
            "POST",
            "/tasks",
            json=task.model_dump(exclude_none=True),
            mutation=True,
        )
        return TaskRecord.model_validate(response.json())

    async def update_task(self, task_id: str, update: TaskUpdate) -> TaskRecord:
        response = await self._request(
            "POST",
            f"/tasks/{task_id}",
            json=update.model_dump(exclude_none=True, exclude_unset=True),
            mutation=True,
        )
        return TaskRecord.model_validate(response.json())

    async def complete_task(self, task_id: str) -> str:
        await self._request("POST", f"/tasks/{task_id}/close", mutation=True)
        return task_id

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        all_records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        cursor: str | None = None
        while True:
            page_params = {**(params or {}), "limit": "200"}
            if cursor:
                page_params["cursor"] = cursor
            response = await self._request("GET", path, params=page_params)
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TaskProviderError("Todoist returned an invalid paginated response")
            for item in payload["results"]:
                if not isinstance(item, dict):
                    continue
                identifier = str(item.get("id", ""))
                if identifier and identifier not in seen_ids:
                    all_records.append(item)
                    seen_ids.add(identifier)
            cursor_value = payload.get("next_cursor")
            cursor = str(cursor_value) if cursor_value else None
            if cursor is None:
                return all_records

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        mutation: bool = False,
    ) -> httpx.Response:
        headers = {"Authorization": self._authorization}
        if mutation:
            headers["X-Request-Id"] = str(uuid4())
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            raise TaskProviderError(
                f"Todoist request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise TaskProviderError("Todoist request failed") from error


class TodoistTool:
    name = "todoist"

    def __init__(self, provider: TaskProvider) -> None:
        self._provider = provider

    async def execute(self, action: ActionRequest) -> ToolExecutionResult:
        try:
            return await self._execute(action)
        except (TaskProviderError, ValueError) as error:
            return ToolExecutionResult(
                tool_name=self.name,
                operation=action.operation,
                success=False,
                error=str(error),
            )

    async def aclose(self) -> None:
        await self._provider.aclose()

    async def _execute(self, action: ActionRequest) -> ToolExecutionResult:
        read_operations = {"list_tasks", "list_projects", "find_project"}
        write_operations = {"create_task", "update_task", "complete_task"}
        if action.operation in read_operations and action.risk_level is not RiskLevel.READ:
            raise ValueError("Todoist read operations must use read risk")
        if action.operation in write_operations and action.risk_level is RiskLevel.READ:
            raise ValueError("Todoist write operations cannot use read risk")

        if action.operation == "list_tasks":
            tasks = await self._provider.list_tasks(
                project_id=action.arguments.get("project_id")
            )
            return self._records_result(action, tasks)
        if action.operation == "list_projects":
            projects = await self._provider.list_projects()
            return self._records_result(action, projects)
        if action.operation == "find_project":
            query = str(action.arguments.get("query") or action.resource)
            project = await self._provider.find_project(query)
            return self._records_result(action, [project])
        if action.operation == "create_task":
            task = await self._provider.create_task(TaskCreate.model_validate(action.arguments))
            return self._records_result(action, [task])
        if action.operation == "update_task":
            task_id = str(action.arguments.get("task_id") or action.resource)
            update_data = {
                key: value
                for key, value in action.arguments.items()
                if key != "task_id"
            }
            task = await self._provider.update_task(
                task_id,
                TaskUpdate.model_validate(update_data),
            )
            return self._records_result(action, [task])
        if action.operation == "complete_task":
            task_id = str(action.arguments.get("task_id") or action.resource)
            completed_id = await self._provider.complete_task(task_id)
            return ToolExecutionResult(
                tool_name=self.name,
                operation=action.operation,
                success=True,
                data={"completed": True, "task_id": completed_id},
                external_ids=[completed_id],
                evidence=[
                    ToolEvidence(kind="todoist_task", identifier=completed_id)
                ],
            )
        raise ValueError(f"unsupported Todoist operation {action.operation!r}")

    def _records_result(
        self,
        action: ActionRequest,
        records: Sequence[BaseModel],
    ) -> ToolExecutionResult:
        serialized = [record.model_dump(mode="json") for record in records]
        identifiers = [str(item["id"]) for item in serialized if item.get("id")]
        return ToolExecutionResult(
            tool_name=self.name,
            operation=action.operation,
            success=True,
            data={"results": serialized},
            external_ids=identifiers,
            evidence=[
                ToolEvidence(
                    kind="todoist_record",
                    identifier=identifier,
                    title=str(item.get("content") or item.get("name") or identifier),
                )
                for identifier, item in zip(identifiers, serialized, strict=True)
            ],
        )
