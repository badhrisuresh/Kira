"""Async event bus for streaming production progress to the frontend."""

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ProductionEvent:
    phase: str                          # script | plan | image_gen | video_gen | concat | voiceover | music | mux | upload | memory
    status: str                         # pending | in_progress | completed | error
    detail: str = ""                    # Human-readable progress text
    progress: float = 0.0              # 0.0–1.0
    preview_url: Optional[str] = None  # URL of generated image/video preview
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self))}\n\n"


class EventBus:
    """Simple pub/sub for production events. Supports multiple SSE listeners."""

    def __init__(self):
        self._listeners: list[asyncio.Queue] = []
        self._history: list[ProductionEvent] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            # Send history to new subscriber so they see current state
            for event in self._history:
                await q.put(event)
            self._listeners.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    async def emit(self, event: ProductionEvent):
        async with self._lock:
            self._history.append(event)
            for q in self._listeners:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # Drop if listener is slow

    async def clear(self):
        async with self._lock:
            self._history.clear()

    @property
    def history(self) -> list[ProductionEvent]:
        return list(self._history)


# Singleton
event_bus = EventBus()
