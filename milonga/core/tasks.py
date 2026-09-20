"""Concurrency helpers shared by the services."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_CONCURRENCY = 8


async def gather_limited(
    items: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    limit: int = DEFAULT_CONCURRENCY,
) -> tuple[R, ...]:
    """Run ``worker`` over ``items`` with at most ``limit`` calls in flight."""
    semaphore = asyncio.Semaphore(limit)

    async def guarded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return tuple(await asyncio.gather(*(guarded(item) for item in items)))


async def gather_settled(
    items: Sequence[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    limit: int = DEFAULT_CONCURRENCY,
) -> tuple[tuple[T, R | BaseException], ...]:
    """Like :func:`gather_limited` but pairs each item with its result or error."""
    semaphore = asyncio.Semaphore(limit)

    async def guarded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    results = await asyncio.gather(*(guarded(item) for item in items), return_exceptions=True)
    return tuple(zip(items, results, strict=True))


async def drain_tasks(tasks: "set[asyncio.Task[Any]]") -> None:
    """Wait until ``tasks`` empties.

    Awaiting an already finished task does not yield, so the done-callbacks
    that remove tasks from the set would never run; the explicit ``sleep(0)``
    hands control back to the loop on every pass.
    """
    while tasks:
        pending = [task for task in tasks if not task.done()]
        if pending:
            await asyncio.wait(pending)
        await asyncio.sleep(0)
