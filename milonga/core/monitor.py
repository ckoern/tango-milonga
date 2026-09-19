"""Subscription ownership for live attribute values.

One channel exists per attribute no matter how many views want it; the channel
disappears with its last watcher. Where the control system cannot deliver
events the channel falls back to polling and says so, so the UI can mark the
value as pulled rather than pushed.
"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace

from milonga.core.backend.protocol import SubscriptionId, TangoBackend
from milonga.core.enums import DataSource, EventType
from milonga.core.errors import ErrorReport, TangoError
from milonga.core.model import AttributeValue, EventData, MonitorStats
from milonga.core.names import AttributeRef
from milonga.core.store import SystemStore
from milonga.core.tasks import drain_tasks

type MonitorCallback = Callable[[EventData], None]
type ChannelKey = tuple[AttributeRef, EventType]

DEFAULT_FALLBACK_PERIOD = 1.0


@dataclass(slots=True)
class _Channel:
    key: ChannelKey
    callbacks: dict[int, MonitorCallback] = field(default_factory=dict)
    subscription: SubscriptionId | None = None
    poll_task: asyncio.Task[None] | None = None
    source: DataSource = DataSource.EVENTS
    last: AttributeValue | None = None
    error: ErrorReport | None = None


class Watch:
    """A view's claim on one attribute. Releasing it may close the channel."""

    __slots__ = ("_hub", "_key", "_token", "released")

    def __init__(self, hub: "MonitorHub", key: ChannelKey, token: int) -> None:
        self._hub = hub
        self._key = key
        self._token = token
        self.released = False

    @property
    def ref(self) -> AttributeRef:
        return self._key[0]

    @property
    def source(self) -> DataSource:
        channel = self._hub.channel(self._key)
        return channel.source if channel else DataSource.EVENTS

    @property
    def last_value(self) -> AttributeValue | None:
        channel = self._hub.channel(self._key)
        return channel.last if channel else None

    def release(self) -> None:
        """Safe to call from a widget teardown: the close runs on the loop."""
        if self.released:
            return
        self.released = True
        self._hub._schedule_release(self._key, self._token)

    async def aclose(self) -> None:
        if self.released:
            return
        self.released = True
        await self._hub._release(self._key, self._token)


class MonitorHub:
    def __init__(
        self,
        backend: TangoBackend,
        *,
        store: SystemStore | None = None,
        fallback_period: float = DEFAULT_FALLBACK_PERIOD,
    ) -> None:
        self._backend = backend
        self._store = store
        self._fallback_period = fallback_period
        self._channels: dict[ChannelKey, _Channel] = {}
        self._locks: dict[ChannelKey, asyncio.Lock] = {}
        self._tokens = 0
        self._tasks: set[asyncio.Task[None]] = set()

    def channel(self, key: ChannelKey) -> _Channel | None:
        return self._channels.get(key)

    def stats(self) -> MonitorStats:
        channels = list(self._channels.values())
        return MonitorStats(
            channels=len(channels),
            by_events=sum(1 for c in channels if c.source is DataSource.EVENTS),
            by_polling=sum(1 for c in channels if c.source is DataSource.POLLING),
            watchers=sum(len(c.callbacks) for c in channels),
        )

    async def watch(
        self,
        ref: AttributeRef,
        callback: MonitorCallback,
        *,
        event_type: EventType = EventType.CHANGE,
        fallback_period: float | None = None,
    ) -> Watch:
        key = (ref, event_type)
        self._tokens += 1
        token = self._tokens
        async with self._lock(key):
            channel = self._channels.get(key)
            if channel is None:
                channel = _Channel(key)
                self._channels[key] = channel
                await self._open(channel, fallback_period or self._fallback_period)
            channel.callbacks[token] = callback
            if channel.last is not None:
                callback(EventData(ref, event_type, channel.last))
            elif channel.error is not None:
                callback(EventData(ref, event_type, error=channel.error))
        return Watch(self, key, token)

    async def aclose(self) -> None:
        await self.drain()
        for key in list(self._channels):
            channel = self._channels.pop(key)
            await self._close(channel)
        self._locks.clear()

    async def drain(self) -> None:
        """Wait for releases scheduled from synchronous code."""
        await drain_tasks(self._tasks)

    # ------------------------------------------------------------------ internals

    def _lock(self, key: ChannelKey) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _open(self, channel: _Channel, fallback_period: float) -> None:
        ref, event_type = channel.key
        try:
            channel.subscription = await self._backend.subscribe_event(
                ref, event_type, lambda event: self._deliver(channel, event)
            )
            channel.source = DataSource.EVENTS
        except TangoError:
            channel.source = DataSource.POLLING
            channel.poll_task = asyncio.create_task(
                self._poll(channel, fallback_period), name=f"poll:{ref}"
            )
            return
        # Tango pushes the current value on subscription but not every backend
        # does, so the first reading is always fetched explicitly.
        await self._read_once(channel)

    async def _close(self, channel: _Channel) -> None:
        if channel.poll_task is not None:
            channel.poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await channel.poll_task
            channel.poll_task = None
        if channel.subscription is not None:
            with suppress(TangoError):
                await self._backend.unsubscribe_event(channel.subscription)
            channel.subscription = None

    async def _poll(self, channel: _Channel, period: float) -> None:
        while True:
            await self._read_once(channel)
            await asyncio.sleep(period)

    async def _read_once(self, channel: _Channel) -> None:
        ref, event_type = channel.key
        try:
            values = await self._backend.read_attributes(ref.device, [ref.attribute])
        except TangoError as error:
            report = ErrorReport.from_exception(error)
            self._deliver(channel, EventData(ref, event_type, error=report))
            return
        if values:
            self._deliver(channel, EventData(ref, event_type, values[0]))

    def _deliver(self, channel: _Channel, event: EventData) -> None:
        if event.value is not None:
            value = replace(event.value, source=channel.source)
            event = replace(event, value=value)
            channel.last = value
            channel.error = None
            if self._store is not None:
                self._store.put_value(event.ref, value)
        elif event.error is not None:
            channel.error = event.error
        for callback in list(channel.callbacks.values()):
            callback(event)

    def _schedule_release(self, key: ChannelKey, token: int) -> None:
        task = asyncio.create_task(self._release(key, token), name=f"release:{key[0]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _release(self, key: ChannelKey, token: int) -> None:
        async with self._lock(key):
            channel = self._channels.get(key)
            if channel is None:
                return
            channel.callbacks.pop(token, None)
            if channel.callbacks:
                return
            del self._channels[key]
            # Closing inside the lock makes a concurrent watch() wait for the
            # teardown and open a fresh channel instead of reusing a dead one.
            await self._close(channel)
