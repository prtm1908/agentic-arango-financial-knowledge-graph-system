import json
import uuid
from datetime import datetime
from typing import Optional, Any

import redis.asyncio as redis

from config import settings


class RedisQueue:
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"

    def __init__(self):
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    async def disconnect(self):
        if self.redis:
            await self.redis.close()

    async def enqueue_job(self, query: str, chat_id: str = None) -> str:
        """Add a new job to the queue and return job_id."""
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        job_data = {
            "job_id": job_id,
            "query": query,
            "chat_id": chat_id,  # Optional chat context for memory
            "status": "queued",
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

        # Store job data
        await self.redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job_data))

        # Add to queue
        await self.redis.rpush(self.QUEUE_NAME, job_id)

        return job_id

    async def get_job(self, job_id: str) -> Optional[dict]:
        """Get job status and data."""
        data = await self.redis.get(f"{self.JOB_PREFIX}{job_id}")
        if data:
            return json.loads(data)
        return None

    async def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None
    ):
        """Update job status."""
        job = await self.get_job(job_id)
        if job:
            if status:
                job["status"] = status
            if result is not None:
                job["result"] = result
            if error is not None:
                job["error"] = error
            job["updated_at"] = datetime.utcnow().isoformat()
            await self.redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job))


queue = RedisQueue()
