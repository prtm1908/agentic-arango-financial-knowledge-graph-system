import json
import asyncio
import time
from typing import AsyncGenerator

import redis.asyncio as redis

from config import settings


class EventPublisher:
    CHANNEL_PREFIX = "events:"
    HISTORY_PREFIX = "event_history:"
    HISTORY_TTL = 300  # 5 minutes
    MAX_HISTORY = 100  # Max events to store per job

    def __init__(self):
        self.redis: redis.Redis | None = None

    async def connect(self):
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def disconnect(self):
        if self.redis:
            await self.redis.close()

    async def publish(self, job_id: str, event: dict):
        """Publish an event for a specific job and store in history."""
        channel = f"{self.CHANNEL_PREFIX}{job_id}"
        history_key = f"{self.HISTORY_PREFIX}{job_id}"
        if "timestamp" not in event:
            event = {**event, "timestamp": time.time_ns()}
        event_json = json.dumps(event)

        # Store in history list (for late subscribers)
        await self.redis.rpush(history_key, event_json)
        await self.redis.ltrim(history_key, -self.MAX_HISTORY, -1)  # Keep last N events
        await self.redis.expire(history_key, self.HISTORY_TTL)

        # Publish to channel (for live subscribers)
        await self.redis.publish(channel, event_json)

    async def subscribe(self, job_id: str) -> AsyncGenerator[dict, None]:
        """Subscribe to events for a specific job, replaying any missed events first."""
        channel = f"{self.CHANNEL_PREFIX}{job_id}"
        history_key = f"{self.HISTORY_PREFIX}{job_id}"

        pubsub = self.redis.pubsub()

        # Subscribe FIRST, then get history (so we don't miss events between history fetch and subscribe)
        await pubsub.subscribe(channel)

        # Get and yield any historical events
        history = await self.redis.lrange(history_key, 0, -1)
        seen_ids = set()
        for event_json in history:
            data = json.loads(event_json)
            # Track by type+timestamp to avoid duplicates
            event_id = f"{data.get('type')}:{data.get('timestamp', '')}"
            seen_ids.add(event_id)
            yield data
            # If history already contains complete/error, we're done
            if data.get("type") in ("complete", "error"):
                await pubsub.unsubscribe(channel)
                await pubsub.close()
                return

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    # Skip if we already yielded this from history
                    event_id = f"{data.get('type')}:{data.get('timestamp', '')}"
                    if event_id in seen_ids:
                        continue
                    yield data
                    # Stop listening after complete or error
                    if data.get("type") in ("complete", "error"):
                        break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()


event_publisher = EventPublisher()
