"""
Phase 0 — TDD RED: JobDatabase concurrency tests.

These tests verify that the SQLite-backed JobDatabase handles concurrent
reads and writes without errors or data corruption. They are expected to
FAIL (or be flaky) on the current implementation and should turn GREEN
after WAL mode, busy_timeout, and short-transaction refactoring.

Contract:
  1. Rapid `update_progress` from a simulated worker must not block
     concurrent `get_job` reads beyond a reasonable timeout.
  2. Terminal status (completed/failed) must never be lost by a
     subsequent progress write that races with the status update.
  3. `last_message` JSON must never be corrupted by concurrent appends.
"""

import json
import time
import threading
import uuid
from pathlib import Path

import pytest

from crew_studio.job_database import JobDatabase

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
PROGRESS_WRITES = 200          # number of rapid progress updates to simulate
CONCURRENT_READERS = 10        # parallel reader threads
MAX_READ_LATENCY_SECS = 2.0   # single read must complete within this


@pytest.fixture
def db(tmp_path) -> JobDatabase:
    """Fresh database for each test."""
    return JobDatabase(tmp_path / "test_concurrency.db")


@pytest.fixture
def job_id(db) -> str:
    """A pre-created job ready for update_progress calls."""
    jid = str(uuid.uuid4())
    db.create_job(jid, "concurrency test", f"/tmp/job-{jid}")
    db.mark_started(jid)
    return jid


# ---------------------------------------------------------------------------
# Test 1: Rapid progress writes must not block reads
# ---------------------------------------------------------------------------

class TestProgressWritesDoNotBlockReads:
    """Simulate a worker spamming update_progress while multiple readers
    poll get_job. Readers must not time out or get 'database is locked'."""

    def test_readers_survive_progress_spam(self, db, job_id):
        errors = []
        read_latencies = []
        stop_event = threading.Event()

        def writer():
            for i in range(PROGRESS_WRITES):
                if stop_event.is_set():
                    break
                try:
                    db.update_progress(job_id, "development", min(i, 99), f"step {i}")
                except Exception as e:
                    errors.append(f"writer: {e}")
                time.sleep(0.005)  # ~5ms between writes (realistic for progress)

        def reader():
            while not stop_event.is_set():
                t0 = time.monotonic()
                try:
                    job = db.get_job(job_id)
                    elapsed = time.monotonic() - t0
                    read_latencies.append(elapsed)
                    if job is None:
                        errors.append("reader: get_job returned None for existing job")
                except Exception as e:
                    errors.append(f"reader: {e}")
                time.sleep(0.01)

        writer_thread = threading.Thread(target=writer, daemon=True)
        reader_threads = [
            threading.Thread(target=reader, daemon=True)
            for _ in range(CONCURRENT_READERS)
        ]

        writer_thread.start()
        for rt in reader_threads:
            rt.start()

        writer_thread.join(timeout=30)
        stop_event.set()
        for rt in reader_threads:
            rt.join(timeout=5)

        assert not errors, f"Concurrency errors: {errors}"

        if read_latencies:
            p95 = sorted(read_latencies)[int(len(read_latencies) * 0.95)]
            assert p95 < MAX_READ_LATENCY_SECS, (
                f"p95 read latency during progress spam: {p95:.2f}s "
                f"(threshold: {MAX_READ_LATENCY_SECS}s)"
            )


# ---------------------------------------------------------------------------
# Test 2: Terminal status must not be overwritten by a racing progress write
# ---------------------------------------------------------------------------

class TestTerminalStatusNotOverwritten:
    """If mark_completed is called, a concurrent update_progress that fires
    slightly later must NOT revert the status from 'completed' back to
    'running'."""

    def test_completed_status_survives_late_progress(self, db, job_id):
        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def complete_job():
            try:
                barrier.wait()
                db.mark_completed(job_id, {"output": "done"})
            except Exception as e:
                errors.append(f"completer: {e}")

        def late_progress():
            try:
                barrier.wait()
                time.sleep(0.001)
                db.update_progress(job_id, "development", 95, "late progress")
            except Exception as e:
                errors.append(f"progress: {e}")

        t1 = threading.Thread(target=complete_job, daemon=True)
        t2 = threading.Thread(target=late_progress, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        job = db.get_job(job_id)
        assert job is not None
        assert job["status"] == "completed", (
            f"Expected status 'completed' but got '{job['status']}' — "
            "a late update_progress overwrote the terminal status"
        )
        assert job["progress"] == 100, (
            f"Expected progress 100 but got {job['progress']}"
        )


# ---------------------------------------------------------------------------
# Test 3: last_message JSON must remain valid under concurrent appends
# ---------------------------------------------------------------------------

class TestLastMessageJsonIntegrity:
    """Rapid progress updates that each append to last_message must not
    corrupt the JSON array."""

    def test_last_message_stays_valid_json(self, db, job_id):
        errors = []

        def spam_progress(start, count):
            for i in range(start, start + count):
                try:
                    db.update_progress(job_id, "dev", i % 100, f"msg-{i}")
                except Exception as e:
                    errors.append(f"writer-{start}: {e}")

        threads = [
            threading.Thread(target=spam_progress, args=(i * 50, 50), daemon=True)
            for i in range(4)  # 4 threads x 50 writes = 200 total
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Writer errors: {errors}"

        job = db.get_job(job_id)
        assert job is not None

        raw = job.get("last_message", "[]")
        if isinstance(raw, str):
            messages = json.loads(raw)
        else:
            messages = raw

        assert isinstance(messages, list), (
            f"last_message should be a list, got {type(messages)}: {str(messages)[:200]}"
        )
