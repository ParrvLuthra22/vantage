"""Batched, thread-safe span exporter.

Instrumentation is a guest in someone else's process. That constraint drives
every decision in this file, and the class below is built around three goals:

1. It MUST NOT block the instrumented app. Spans are handed to a bounded
   in-memory queue with `put_nowait`, so `submit()` returns in constant time and
   never waits on the network, on a lock held by the exporter, or on a slow
   backend. The queue is bounded rather than infinite because an unbounded queue
   doesn't remove backpressure, it just converts it into unbounded memory growth
   and eventually an OOM kill of the host app. When the queue is full we drop.
   Losing telemetry is an acceptable failure; taking down the app that produced
   it is not.

2. It MUST NOT crash the instrumented app. All network work happens on a
   background thread, and every exception there is swallowed. A DNS failure, an
   expired API key, a 500 from the backend, a TLS error — none of it may surface
   in the caller's stack. The user asked us to observe their agent, not to
   become a new source of outages in it.

3. It MUST NOT lose spans on clean shutdown. The worker is a daemon thread, so
   it cannot keep a finished process alive; an `atexit` hook signals it and
   waits for a final drain instead. Without that, every span buffered since the
   last flush would vanish on exit — which is exactly the window containing the
   crash or the final answer you most wanted to look at.

Batching is by size AND by time, whichever comes first. Size alone starves
low-traffic apps, where a half-full batch could sit for hours. Time alone gives
no protection against bursts, where a busy agent would build one enormous
request. The pair bounds both the latency of a span reaching the backend and the
size of any single request.
"""

import atexit
import queue
import threading
import time
from typing import Any, Optional

import httpx

from vantage.models import SpanCreate

# The backend rejects batches larger than this (SpanBatch caps `spans` at 500),
# so no single request may exceed it regardless of how large `batch_size` is or
# how much the queue held at drain time.
MAX_REQUEST_SPANS = 500


class VantageClient:
    """Collects spans on the caller's thread and ships them from a worker thread."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.vantage.dev",
        project: str = "default",
        batch_size: int = 50,
        flush_interval_s: float = 5.0,
        queue_size: int = 10_000,
    ) -> None:
        self.api_key = api_key
        # Normalize now so every request builds a clean URL rather than "//traces".
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s

        self._queue: queue.Queue[SpanCreate] = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()

        # daemon=True so a forgotten shutdown can never hang interpreter exit.
        # The atexit hook below is what actually guarantees the final flush.
        self._worker = threading.Thread(
            target=self._run_worker, daemon=True, name="vantage-exporter"
        )
        self._worker.start()

        atexit.register(self.shutdown)

    def submit(self, span: SpanCreate) -> None:
        """Hand a span to the exporter. Never blocks, never raises."""
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            # Drop rather than block: the app's latency is not ours to spend.
            # A production build would increment a dropped-spans counter here so
            # the loss is visible instead of silent.
            pass

    def _run_worker(self) -> None:
        """Drain the queue into batches until stopped, then flush what's left."""
        batch: list[SpanCreate] = []
        last_flush = time.monotonic()

        # One client for the worker's whole lifetime so the TCP + TLS handshake
        # is paid once and every flush reuses the pooled connection.
        with httpx.Client(timeout=10.0) as client:
            while not self._stop_event.is_set():
                try:
                    batch.append(self._queue.get(timeout=0.5))
                except queue.Empty:
                    # Not an error: the timeout is our periodic wake-up, and it
                    # is what lets a time-based flush fire while the app is idle.
                    pass

                due_by_size = len(batch) >= self.batch_size
                due_by_time = bool(batch) and (
                    time.monotonic() - last_flush > self.flush_interval_s
                )
                if due_by_size or due_by_time:
                    self._flush(client, batch)
                    batch = []
                    last_flush = time.monotonic()

            # Final drain: whatever is still queued at shutdown gets one last
            # flush, including spans that never reached `batch`.
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._flush(client, batch)

    def _flush(self, client: httpx.Client, batch: list[SpanCreate]) -> None:
        """POST a batch to the backend. Swallows every failure by design."""
        if not batch:
            return

        # Chunked because a drain at shutdown can hold far more than batch_size
        # — up to the full queue — and one oversized request would be rejected
        # wholesale, losing every span in it.
        for i in range(0, len(batch), MAX_REQUEST_SPANS):
            chunk = batch[i : i + MAX_REQUEST_SPANS]
            try:
                client.post(
                    f"{self.base_url}/traces/spans",
                    json={
                        "project": self.project,
                        "spans": [s.model_dump(mode="json") for s in chunk],
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            except Exception:
                # Instrumentation must never break the app. There is no retry
                # here on purpose: the exporter already dropped this batch's
                # backpressure, and a retry loop on a dead backend would grow
                # the queue behind it.
                pass

    def shutdown(self, timeout: float = 5.0) -> None:
        """Signal the worker to drain and wait, briefly, for it to finish."""
        self._stop_event.set()
        self._worker.join(timeout=timeout)


_default_client: Optional[VantageClient] = None


def init(api_key: str, **kwargs: Any) -> VantageClient:
    """Create the process-wide client and install it as the default."""
    global _default_client
    _default_client = VantageClient(api_key=api_key, **kwargs)
    return _default_client


def get_client() -> Optional[VantageClient]:
    """Return the process-wide client, or None if `init()` was never called."""
    return _default_client
