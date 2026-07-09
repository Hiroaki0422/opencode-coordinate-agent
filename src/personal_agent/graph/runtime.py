"""Lifecycle for a graph compiled with a durable SQLite checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from personal_agent.graph.workflow import CompiledAgentGraph, build_agent_graph
from personal_agent.models import Coordinator
from personal_agent.policy import PolicyService


@asynccontextmanager
async def open_agent_graph(
    *,
    checkpoint_path: Path,
    coordinator: Coordinator,
    policy: PolicyService,
) -> AsyncIterator[CompiledAgentGraph]:
    """Open the checkpoint database and yield a compiled graph."""

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        await checkpointer.setup()
        yield build_agent_graph(
            coordinator=coordinator,
            policy=policy,
            checkpointer=checkpointer,
        )
