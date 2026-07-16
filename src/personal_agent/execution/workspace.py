"""Workspace containment and repository creation services."""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath

from personal_agent.execution.docker import DockerSandbox, SandboxExecutionError

_WORKSPACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class WorkspaceService:
    """Resolve files and create repositories beneath one configured root."""

    def __init__(
        self,
        workspace_root: Path,
        sandbox: DockerSandbox,
        repository_paths: list[Path] | None = None,
    ) -> None:
        self._root = workspace_root.expanduser()
        self._sandbox = sandbox
        self._repository_paths = [path.expanduser() for path in repository_paths or []]

    def resolve_workspace(self, resource: str) -> Path:
        root = self._root.resolve()
        candidate = Path(resource).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise SandboxExecutionError("workspace does not exist") from error
        named_repositories = {
            path.resolve() for path in self._repository_paths if path.exists()
        }
        allowed = resolved.is_relative_to(root) or resolved in named_repositories
        if not resolved.is_dir() or not allowed:
            raise SandboxExecutionError("workspace is outside the configured workspace root")
        return resolved

    def list_workspaces(self) -> tuple[Path, ...]:
        root = self._root.resolve()
        discovered: set[Path] = set()
        if root.is_dir():
            discovered.update(
                path.resolve()
                for path in root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
        discovered.update(
            path.resolve()
            for path in self._repository_paths
            if path.is_dir() and not path.is_symlink()
        )
        return tuple(sorted(discovered, key=str))

    def container_path(self, workspace: Path, relative_path: str, *, writing: bool) -> str:
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
            raise SandboxExecutionError("file path must be relative to the workspace")
        host_path = workspace.joinpath(*path.parts)
        boundary = host_path.parent if writing else host_path
        try:
            resolved_boundary = boundary.resolve(strict=True)
        except FileNotFoundError as error:
            raise SandboxExecutionError("file path does not exist") from error
        if not resolved_boundary.is_relative_to(workspace.resolve()):
            raise SandboxExecutionError("file path escapes the workspace")
        return str(PurePosixPath("/workspace", *path.parts))

    async def create_repository(self, name: str) -> Path:
        if _WORKSPACE_NAME.fullmatch(name) is None or name in {".", ".."}:
            raise SandboxExecutionError("workspace name is invalid")
        root = self._root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / name
        try:
            target.mkdir()
        except FileExistsError as error:
            raise SandboxExecutionError(f"workspace {name!r} already exists") from error
        try:
            result = await self._sandbox.run(
                workspace=target,
                command=["git", "init", "--initial-branch=main", "."],
                writable=True,
            )
            if result.exit_code != 0:
                raise SandboxExecutionError("git repository initialization failed")
        except BaseException:
            shutil.rmtree(target)
            raise
        return target.resolve()
