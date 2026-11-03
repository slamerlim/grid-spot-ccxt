"""Bounded asyncio admission with exactly one SQLite-owning worker thread."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from grid.persistence.ledger import Ledger
from grid.persistence.records import Backpressure, Bounds, LedgerError
from grid.persistence.storage import Fault, no_fault


@dataclass
class _Job:
    operation: Callable[[Ledger], Any]
    result: asyncio.Future[Any]


class AsyncWriter:
    """Cancellation detaches a caller; admitted work still finishes exactly once.

    The caller must retain its stable request ID and use lookup after any lost
    acknowledgement. The queue never retries, discards private facts or sends.
    """

    def __init__(self, bounds: Bounds) -> None:
        self._queue: asyncio.Queue[_Job | None] = asyncio.Queue(bounds.queue_size)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grid-ledger")
        self._closing = False
        self._ledger: Ledger
        self._consumer: asyncio.Task[None]
        self._shutdown: asyncio.Task[None] | None = None

    @classmethod
    async def open(
        cls,
        path: Path,
        bounds: Bounds,
        approved_sources: frozenset[str],
        *,
        mock_storage: bool,
        fault: Fault = no_fault,
    ) -> AsyncWriter:
        writer = cls(bounds)
        try:
            initialization = asyncio.get_running_loop().run_in_executor(
                writer._executor,
                partial(
                    Ledger, path, bounds, approved_sources, mock_storage=mock_storage, fault=fault
                ),
            )
            try:
                writer._ledger = await asyncio.shield(initialization)
            except asyncio.CancelledError:
                ledger = await initialization
                await asyncio.get_running_loop().run_in_executor(writer._executor, ledger.close)
                raise
            writer._consumer = asyncio.create_task(writer._consume())
            return writer
        except BaseException:
            writer._executor.shutdown(wait=True, cancel_futures=False)
            raise

    async def submit[T](self, operation: Callable[[Ledger], T]) -> T:
        if self._closing:
            raise LedgerError("writer admission is closed")
        result: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        # Retrieving an eventual exception also covers a detached/canceled caller.
        result.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            self._queue.put_nowait(_Job(operation, result))
        except asyncio.QueueFull as exc:
            raise Backpressure("writer queue is full; input was not admitted") from exc
        return await asyncio.shield(result)

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                try:
                    result = await asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        job.operation,
                        self._ledger,
                    )
                except Exception as exc:
                    job.result.set_exception(exc)
                else:
                    job.result.set_result(result)
            finally:
                self._queue.task_done()

    async def _finish(self) -> None:
        try:
            await self._queue.put(None)
            await self._consumer
            await asyncio.get_running_loop().run_in_executor(self._executor, self._ledger.close)
        finally:
            self._executor.shutdown(wait=True, cancel_futures=False)

    async def close(self) -> None:
        if self._shutdown is None:
            self._closing = True
            self._shutdown = asyncio.create_task(self._finish())
        await asyncio.shield(self._shutdown)
