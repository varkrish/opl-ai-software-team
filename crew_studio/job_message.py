"""
JobMessage schema and JobPublisher protocol for future multi-container workers.

This module defines the contract for submitting jobs to an external queue
(Redis, RabbitMQ, etc.) without binding to any specific implementation.
The API layer uses a publisher; in tests a FakePublisher replaces it.

Current deployment: in-process executor via ``asyncio.run_in_executor``.
Future deployment: API publishes a JobMessage; a worker container picks it up.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class JobMode(str, Enum):
    BUILD = "build"
    MIGRATION = "migration"
    REFACTOR = "refactor"


@dataclass(frozen=True)
class JobMessage:
    """Immutable message that describes a job to be executed by a worker.

    Serialisable to JSON for queue transport; the worker container
    deserialises it and hands it to the existing ``run_job_async`` /
    ``run_job_with_backend`` functions.
    """

    job_id: str
    vision: str
    backend: str = "opl-ai-team"
    mode: JobMode = JobMode.BUILD
    workspace_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    github_urls: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobMessage":
        data = dict(data)
        if "mode" in data and isinstance(data["mode"], str):
            data["mode"] = JobMode(data["mode"])
        return cls(**data)


@runtime_checkable
class JobPublisher(Protocol):
    """Protocol that the API layer uses to dispatch jobs.

    Implementations:
      - InProcessPublisher  (current) — runs via asyncio executor
      - RedisPublisher      (future)  — pushes to Redis stream
      - FakePublisher       (tests)   — records published messages
    """

    async def publish(self, message: JobMessage) -> None:
        """Submit a job message for asynchronous execution."""
        ...
