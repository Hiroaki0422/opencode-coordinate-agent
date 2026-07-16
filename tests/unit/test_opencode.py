"""Tests for sandboxed OpenCode delegation and coding evidence."""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from personal_agent.core.config import OpenCodeSettings
from personal_agent.core.types import ActionRequest, RiskLevel
from personal_agent.execution import DockerSandbox, OpenCodeTool, WorkspaceService
from personal_agent.execution.docker import SandboxResult


def sandbox_result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    network_enabled: bool = False,
) -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        stdout_digest=f"stdout-{len(stdout)}",
        stderr_digest=f"stderr-{len(stderr)}",
        output_truncated=False,
        network_enabled=network_enabled,
    )


class QueueSandbox:
    def __init__(
        self,
        results: list[SandboxResult],
        mutations: dict[int, Callable[[], None]] | None = None,
    ) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []
        self.mutations = mutations or {}

    async def run(self, **arguments: object) -> SandboxResult:
        self.calls.append(arguments)
        mutation = self.mutations.get(len(self.calls) - 1)
        if mutation is not None:
            mutation()
        return self.results.pop(0)

    async def health_check(self) -> None:
        return None


def repository(tmp_path: Path, *, outside_root: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "workspaces"
    root.mkdir()
    repo = (tmp_path / "named-repo") if outside_root else (root / "repo")
    repo.mkdir()
    (repo / ".git").mkdir()
    return root, repo


def action(repo: Path, **arguments: object) -> ActionRequest:
    payload: dict[str, object] = {
        "task": "Update the greeting",
        "acceptance_criteria": ["Greeting is friendly"],
        "expected_files": ["app.py"],
        "test_commands": [["pytest", "-q"]],
        **arguments,
    }
    return ActionRequest(
        tool_name="opencode",
        operation="code_task",
        resource=str(repo),
        risk_level=RiskLevel.RISKY,
        summary="Update greeting",
        arguments=payload,
    )


def build_tool(root: Path, repo: Path, sandbox: QueueSandbox) -> OpenCodeTool:
    return OpenCodeTool(
        settings=OpenCodeSettings(enabled=True),
        api_key="deepseek-secret",
        sandbox=cast(DockerSandbox, sandbox),
        workspaces=WorkspaceService(
            root,
            cast(DockerSandbox, sandbox),
            [repo] if not repo.is_relative_to(root) else [],
        ),
    )


async def test_opencode_captures_changes_tests_and_report(tmp_path: Path) -> None:
    root, repo = repository(tmp_path, outside_root=True)
    sandbox = QueueSandbox(
        [
            sandbox_result(stdout=""),
            sandbox_result(exit_code=128),
            sandbox_result(
                stdout='{"type":"text","content":"Implemented greeting"}\n',
                network_enabled=True,
            ),
            sandbox_result(stdout=" M app.py\n"),
            sandbox_result(stdout="diff --git a/app.py b/app.py\n"),
            sandbox_result(stdout=" app.py | 2 +-\n"),
            sandbox_result(stdout="1 passed\n"),
        ],
        mutations={2: lambda: (repo / "app.py").write_text("hello\n")},
    )
    tool = build_tool(root, repo, sandbox)

    result = await tool.execute(action(repo))

    assert result.success is True
    assert result.external_ids == ["app.py"]
    assert result.data["requested_change_verified"] is True
    assert result.data["report"] == "Implemented greeting"
    assert result.data["tests"][0]["exit_code"] == 0
    assert len(result.audit_data["commands"]) == 7
    assert result.audit_data["commands"][2]["command"][-1] == "[TASK_CONTRACT]"
    opencode_call = sandbox.calls[2]
    assert opencode_call["network_enabled"] is True
    environment = cast(dict[str, str], opencode_call["environment"])
    assert environment["DEEPSEEK_API_KEY"] == "deepseek-secret"
    config = environment["OPENCODE_CONFIG_CONTENT"]
    assert '"bash":"deny"' in config
    assert '"external_directory":"deny"' in config
    assert "deepseek-secret" not in cast(list[str], opencode_call["command"])


async def test_opencode_reports_failed_tests_without_success_claim(tmp_path: Path) -> None:
    root, repo = repository(tmp_path)
    sandbox = QueueSandbox(
        [
            sandbox_result(),
            sandbox_result(exit_code=128),
            sandbox_result(stdout='{"content":"Changed file"}\n', network_enabled=True),
            sandbox_result(stdout=" M app.py\n"),
            sandbox_result(stdout="diff"),
            sandbox_result(stdout="app.py | 1 +"),
            sandbox_result(exit_code=1, stderr="failed"),
        ],
        mutations={2: lambda: (repo / "app.py").write_text("hello\n")},
    )

    result = await build_tool(root, repo, sandbox).execute(action(repo))

    assert result.success is False
    assert result.error == "one or more requested test commands failed"
    assert result.data["tests"][0]["exit_code"] == 1


async def test_opencode_rejects_unapproved_repository_and_non_risky_action(
    tmp_path: Path,
) -> None:
    root, repo = repository(tmp_path, outside_root=True)
    sandbox = QueueSandbox([])
    tool = OpenCodeTool(
        settings=OpenCodeSettings(enabled=True),
        api_key="secret",
        sandbox=cast(DockerSandbox, sandbox),
        workspaces=WorkspaceService(root, cast(DockerSandbox, sandbox)),
    )

    outside_result = await tool.execute(action(repo))
    write_action = action(repo).model_copy(update={"risk_level": RiskLevel.WRITE})
    risky_result = await build_tool(root, repo, sandbox).execute(write_action)

    assert outside_result.success is False
    assert "outside" in (outside_result.error or "")
    assert risky_result.success is False
    assert "risky" in (risky_result.error or "")
    assert sandbox.calls == []


@pytest.mark.parametrize(
    "command",
    [
        ["pip", "install", "requests"],
        ["git", "push"],
        ["git", "checkout", "feature"],
        ["rm", "-rf", "src"],
    ],
)
async def test_opencode_rejects_risky_test_commands(
    tmp_path: Path,
    command: list[str],
) -> None:
    root, repo = repository(tmp_path)
    sandbox = QueueSandbox([])

    result = await build_tool(root, repo, sandbox).execute(
        action(repo, test_commands=[command])
    )

    assert result.success is False
    assert "not allowed" in (result.error or "")
    assert sandbox.calls == []


async def test_opencode_fails_when_expected_file_was_not_changed(tmp_path: Path) -> None:
    root, repo = repository(tmp_path)
    sandbox = QueueSandbox(
        [
            sandbox_result(),
            sandbox_result(exit_code=128),
            sandbox_result(stdout='{"content":"Changed docs"}\n', network_enabled=True),
            sandbox_result(stdout=" M README.md\n"),
            sandbox_result(stdout="diff"),
            sandbox_result(stdout="README.md | 1 +"),
            sandbox_result(stdout="1 passed"),
        ],
        mutations={2: lambda: (repo / "README.md").write_text("docs\n")},
    )

    result = await build_tool(root, repo, sandbox).execute(action(repo))

    assert result.success is False
    assert result.error == "requested file changes could not be verified"
    assert result.data["effect_observed"] is True
    assert result.data["changed_files"] == ["README.md"]
    assert result.data["missing_expected_files"] == ["app.py"]
    assert result.data["verification_reason"] == "expected_files_missing"
    assert result.data["changes_retained"] is True


async def test_opencode_detects_content_edit_to_existing_untracked_file(
    tmp_path: Path,
) -> None:
    root, repo = repository(tmp_path)
    (repo / "app.py").write_text("before\n")
    sandbox = QueueSandbox(
        [
            sandbox_result(stdout="?? app.py\n"),
            sandbox_result(exit_code=128),
            sandbox_result(stdout='{"content":"Updated file"}\n', network_enabled=True),
            sandbox_result(stdout="?? app.py\n"),
            sandbox_result(exit_code=128),
            sandbox_result(exit_code=128),
            sandbox_result(stdout="1 passed"),
        ],
        mutations={2: lambda: (repo / "app.py").write_text("after\n")},
    )

    result = await build_tool(root, repo, sandbox).execute(action(repo))

    assert result.success is True
    assert result.data["changed_files"] == ["app.py"]
    assert result.data["verification_reason"] == "verified"


async def test_opencode_rejects_workspace_symlink_escape(tmp_path: Path) -> None:
    root, repo = repository(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (repo / "escape.txt").symlink_to(outside)

    result = await build_tool(root, repo, QueueSandbox([])).execute(action(repo))

    assert result.success is False
    assert "symlink escape" in (result.error or "")


async def test_opencode_enforces_manifest_file_count_limit(tmp_path: Path) -> None:
    root, repo = repository(tmp_path)
    (repo / "one.txt").write_text("one")
    (repo / "two.txt").write_text("two")
    sandbox = QueueSandbox([])
    tool = OpenCodeTool(
        settings=OpenCodeSettings(enabled=True, max_manifest_files=1),
        api_key="secret",
        sandbox=cast(DockerSandbox, sandbox),
        workspaces=WorkspaceService(root, cast(DockerSandbox, sandbox)),
    )

    result = await tool.execute(action(repo))

    assert result.success is False
    assert "file-count limit" in (result.error or "")
    assert sandbox.calls == []


def test_opencode_redacts_credentials_from_receipt_evidence(tmp_path: Path) -> None:
    root, repo = repository(tmp_path)
    tool = build_tool(root, repo, QueueSandbox([]))

    redacted = tool._bounded_redacted(  # noqa: SLF001
        "authorization: Bearer abc123 token=secret-value deepseek-secret"
    )

    assert "abc123" not in redacted
    assert "secret-value" not in redacted
    assert "deepseek-secret" not in redacted
