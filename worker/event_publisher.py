import json
import time
import redis

from config import config


class EventPublisher:
    CHANNEL_PREFIX = "events:"
    HISTORY_PREFIX = "event_history:"
    HISTORY_TTL = 300  # 5 minutes
    MAX_HISTORY = 100  # Max events to store per job

    def __init__(self):
        self.redis = redis.from_url(config.REDIS_URL, decode_responses=True)

    def publish(self, job_id: str, event: dict):
        """Publish an event for a specific job and store in history."""
        channel = f"{self.CHANNEL_PREFIX}{job_id}"
        history_key = f"{self.HISTORY_PREFIX}{job_id}"
        if "timestamp" not in event:
            event = {**event, "timestamp": time.time_ns()}
        event_json = json.dumps(event)

        # Store in history list (for late subscribers)
        self.redis.rpush(history_key, event_json)
        self.redis.ltrim(history_key, -self.MAX_HISTORY, -1)  # Keep last N events
        self.redis.expire(history_key, self.HISTORY_TTL)

        # Publish to channel (for live subscribers)
        self.redis.publish(channel, event_json)

    def publish_status(self, job_id: str, message: str):
        self.publish(job_id, {"type": "status", "message": message})

    def publish_agent_switch(self, job_id: str, agent: str, reason: str):
        self.publish(job_id, {"type": "agent_switch", "agent": agent, "reason": reason})

    def publish_tool_call(self, job_id: str, tool: str, server: str, args: dict):
        self.publish(job_id, {"type": "tool_call", "tool": tool, "server": server, "args": args})

    def publish_tool_result(self, job_id: str, tool: str, result: any, duration_ms: int):
        self.publish(job_id, {"type": "tool_result", "tool": tool, "result": result, "duration_ms": duration_ms})

    def publish_metric_found(self, job_id: str, metric: dict):
        self.publish(job_id, {"type": "metric_found", "metric": metric})

    def publish_aql_query(self, job_id: str, query: str, bind_vars: dict):
        self.publish(job_id, {"type": "aql_query", "query": query, "bind_vars": bind_vars})

    def publish_complete(self, job_id: str, result: any):
        self.publish(job_id, {"type": "complete", "result": result})

    def publish_error(self, job_id: str, message: str):
        self.publish(job_id, {"type": "error", "message": message})


event_publisher = EventPublisher()
