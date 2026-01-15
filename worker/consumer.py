import json
import time
import logging
import signal
import sys
from pathlib import Path

import redis

from config import config
from event_publisher import event_publisher
from opencode_runner import OpenCodeRunner

# Directory for storing chat JSON files (project root/chats)
CHATS_DIR = Path(__file__).parent.parent / "chats"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class QueueConsumer:
    QUEUE_NAME = "job_queue"
    JOB_PREFIX = "job:"

    def __init__(self):
        self.redis = redis.from_url(config.REDIS_URL, decode_responses=True)
        self.running = True

    def get_job(self, job_id: str) -> dict | None:
        """Get job data from Redis."""
        data = self.redis.get(f"{self.JOB_PREFIX}{job_id}")
        if data:
            return json.loads(data)
        return None

    def update_job(self, job_id: str, status: str, result=None, error=None):
        """Update job status in Redis."""
        job = self.get_job(job_id)
        if job:
            job["status"] = status
            if result is not None:
                job["result"] = result
            if error is not None:
                job["error"] = error
            job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.redis.set(f"{self.JOB_PREFIX}{job_id}", json.dumps(job))

    def load_chat_history(self, chat_id: str) -> list[dict]:
        """Load chat history from JSON file for memory/context."""
        if not chat_id:
            return []

        chat_file = CHATS_DIR / f"{chat_id}.json"
        if not chat_file.exists():
            logger.warning(f"Chat file not found: {chat_file}")
            return []

        try:
            with open(chat_file) as f:
                content = json.load(f)
                return content.get("messages", [])
        except Exception as e:
            logger.error(f"Failed to load chat history: {e}")
            return []

    def save_response_to_chat(self, chat_id: str, job_id: str, response: dict, agents_used: list[str] = None, tools_called: list[dict] = None):
        """Save the system response to the chat JSON file."""
        if not chat_id:
            return

        chat_file = CHATS_DIR / f"{chat_id}.json"
        if not chat_file.exists():
            logger.warning(f"Chat file not found for saving response: {chat_file}")
            return

        try:
            with open(chat_file) as f:
                content = json.load(f)

            # Extract the response text
            response_text = ""
            if isinstance(response, dict):
                response_text = response.get("response", response.get("text", json.dumps(response)))
            else:
                response_text = str(response)

            # Load event history for consistent post-stream rendering
            event_history = []
            try:
                history_key = f"{event_publisher.HISTORY_PREFIX}{job_id}"
                raw_events = self.redis.lrange(history_key, 0, -1)
                for raw in raw_events or []:
                    try:
                        event_history.append(json.loads(raw))
                    except Exception:
                        continue
            except Exception:
                event_history = []

            # Create system message with full metadata
            system_message = {
                "id": f"msg_{job_id}",
                "role": "system",
                "content": response_text,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "metadata": {
                    "agents_used": agents_used or [],
                    "tools_called": tools_called or [],
                    "event_history": event_history,
                    "job_id": job_id
                }
            }

            content["messages"].append(system_message)

            with open(chat_file, "w") as f:
                json.dump(content, f, indent=2)

            logger.info(f"Saved response to chat {chat_id} (agents: {agents_used}, tools: {len(tools_called or [])})")

        except Exception as e:
            logger.error(f"Failed to save response to chat: {e}")

    def process_job(self, job_id: str):
        """Process a single job."""
        job = self.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        query = job.get("query", "")
        chat_id = job.get("chat_id")  # Optional chat context
        logger.info(f"Processing job {job_id}: {query[:100]}..." + (f" (chat: {chat_id})" if chat_id else ""))

        # Update status to processing
        self.update_job(job_id, "processing")
        event_publisher.publish_status(job_id, "Processing query...")

        # Load chat history for memory/context
        chat_history = self.load_chat_history(chat_id) if chat_id else []

        try:
            # Run OpenCode with chat history
            runner = OpenCodeRunner(job_id)
            result = runner.run(query, chat_history=chat_history)

            # Extract metadata from result (added by OpenCodeRunner)
            metadata = result.pop("_metadata", {}) if isinstance(result, dict) else {}
            agents_used = metadata.get("agents_used", [])
            tools_called = metadata.get("tools_called", [])

            # Update job with result
            self.update_job(job_id, "completed", result=result)
            event_publisher.publish_complete(job_id, result)

            # Save response to chat if chat_id is provided
            if chat_id:
                self.save_response_to_chat(chat_id, job_id, result, agents_used=agents_used, tools_called=tools_called)

            logger.info(f"Job {job_id} completed successfully (agents: {agents_used})")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Job {job_id} failed: {error_msg}")

            self.update_job(job_id, "failed", error=error_msg)
            event_publisher.publish_error(job_id, error_msg)

    def run(self):
        """Main consumer loop."""
        logger.info("Queue consumer started, waiting for jobs...")

        while self.running:
            try:
                # Blocking pop with 1 second timeout
                result = self.redis.blpop(self.QUEUE_NAME, timeout=1)

                if result:
                    _, job_id = result
                    logger.info(f"Dequeued job: {job_id}")
                    self.process_job(job_id)

            except redis.ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                time.sleep(5)  # Wait before retrying

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(1)

    def stop(self):
        """Stop the consumer gracefully."""
        logger.info("Stopping consumer...")
        self.running = False


def main():
    consumer = QueueConsumer()

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        consumer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    consumer.run()


if __name__ == "__main__":
    main()
