"""Offline tests for the Todoist API v1 provider and tool adapter."""

from typing import Any

import httpx

from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.tools.todoist import TodoistTaskProvider, TodoistTool


async def test_todoist_provider_paginates_and_sends_idempotency_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/v1/tasks":
            if request.url.params.get("cursor") == "next-page":
                return httpx.Response(
                    200,
                    json={
                        "results": [{"id": "task-2", "content": "Second"}],
                        "next_cursor": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "task-1", "content": "First"}],
                    "next_cursor": "next-page",
                },
            )
        if request.method == "POST" and request.url.path == "/api/v1/tasks":
            return httpx.Response(200, json={"id": "task-3", "content": "Created"})
        if request.method == "POST" and request.url.path.endswith("/close"):
            return httpx.Response(200, json=None)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://api.todoist.test/api/v1",
        transport=httpx.MockTransport(handler),
    )
    provider = TodoistTaskProvider(api_token="todoist-secret", client=client)

    tasks = await provider.list_tasks()
    tool = TodoistTool(provider)
    created = await tool.execute(
        ActionRequest(
            tool_name="todoist",
            operation="create_task",
            resource="inbox",
            risk_level=RiskLevel.WRITE,
            summary="Create a task",
            arguments={"content": "Created"},
        )
    )
    completed = await tool.execute(
        ActionRequest(
            tool_name="todoist",
            operation="complete_task",
            resource="task-3",
            risk_level=RiskLevel.WRITE,
            summary="Complete a task",
            arguments={"task_id": "task-3"},
        )
    )
    await client.aclose()

    assert [task.id for task in tasks] == ["task-1", "task-2"]
    assert created.success is True
    assert created.external_ids == ["task-3"]
    assert completed.external_ids == ["task-3"]
    assert all(request.headers["authorization"] == "Bearer todoist-secret" for request in requests)
    mutation_requests = [request for request in requests if request.method == "POST"]
    assert all(request.headers.get("x-request-id") for request in mutation_requests)


async def test_todoist_tool_rejects_read_risk_for_mutations() -> None:
    class UnexpectedProvider:
        async def list_tasks(self, *, project_id: str | None = None) -> list[Any]:
            raise AssertionError("provider should not be called")

        async def list_projects(self) -> list[Any]:
            raise AssertionError("provider should not be called")

        async def find_project(self, query: str) -> Any:
            raise AssertionError("provider should not be called")

        async def create_task(self, task: Any) -> Any:
            raise AssertionError("provider should not be called")

        async def update_task(self, task_id: str, update: Any) -> Any:
            raise AssertionError("provider should not be called")

        async def complete_task(self, task_id: str) -> str:
            raise AssertionError("provider should not be called")

        async def aclose(self) -> None:
            return None

    result = await TodoistTool(UnexpectedProvider()).execute(
        ActionRequest(
            tool_name="todoist",
            operation="create_task",
            resource="inbox",
            risk_level=RiskLevel.READ,
            summary="Create task",
            arguments={"content": "Unsafe"},
        )
    )

    assert result.success is False
    assert "cannot use read risk" in (result.error or "")
