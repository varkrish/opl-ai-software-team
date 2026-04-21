"""
Tests for the JobMessage schema and JobPublisher protocol.

These tests verify:
  1. JobMessage serialisation/deserialisation round-trip.
  2. FakePublisher conforms to the JobPublisher protocol.
  3. The API layer can swap publishers without code changes.
"""

import asyncio
from dataclasses import dataclass, field
from typing import List

import pytest

from crew_studio.job_message import JobMessage, JobMode, JobPublisher


# ---------------------------------------------------------------------------
# FakePublisher — used in tests as a drop-in for any real publisher
# ---------------------------------------------------------------------------

@dataclass
class FakePublisher:
    """In-memory publisher that records every message for assertion."""

    published: List[JobMessage] = field(default_factory=list)

    async def publish(self, message: JobMessage) -> None:
        self.published.append(message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestJobMessageSerialisation:

    def test_round_trip(self):
        msg = JobMessage(
            job_id="abc-123",
            vision="Build a Frappe invoicing app",
            backend="opl-ai-team",
            mode=JobMode.BUILD,
            workspace_path="/app/workspace/job-abc-123",
            metadata={"lang": "python"},
            github_urls=["https://github.com/frappe/frappe"],
        )
        d = msg.to_dict()
        assert d["mode"] == "build"
        assert d["job_id"] == "abc-123"

        restored = JobMessage.from_dict(d)
        assert restored == msg

    def test_defaults(self):
        msg = JobMessage(job_id="x", vision="test")
        assert msg.backend == "opl-ai-team"
        assert msg.mode == JobMode.BUILD
        assert msg.metadata == {}
        assert msg.github_urls == []

    def test_mode_enum_values(self):
        for mode in ("build", "migration", "refactor"):
            msg = JobMessage.from_dict({"job_id": "x", "vision": "v", "mode": mode})
            assert msg.mode == JobMode(mode)

    def test_immutable(self):
        msg = JobMessage(job_id="x", vision="v")
        with pytest.raises(AttributeError):
            msg.job_id = "y"  # type: ignore[misc]


class TestFakePublisher:

    def test_conforms_to_protocol(self):
        pub = FakePublisher()
        assert isinstance(pub, JobPublisher)

    @pytest.mark.asyncio
    async def test_records_messages(self):
        pub = FakePublisher()
        msg = JobMessage(job_id="j1", vision="v1")
        await pub.publish(msg)
        assert len(pub.published) == 1
        assert pub.published[0].job_id == "j1"

    @pytest.mark.asyncio
    async def test_multiple_publishes(self):
        pub = FakePublisher()
        msgs = [JobMessage(job_id=f"j{i}", vision=f"v{i}") for i in range(5)]
        for m in msgs:
            await pub.publish(m)
        assert len(pub.published) == 5
        assert [m.job_id for m in pub.published] == [f"j{i}" for i in range(5)]
