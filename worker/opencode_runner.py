import subprocess
import json
import re
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Generator, Optional
from dataclasses import dataclass

from config import config
from event_publisher import event_publisher

logger = logging.getLogger(__name__)


@dataclass
class OpenCodeEvent:
    event_type: str
    data: dict


class OpenCodeRunner:
    """Runs OpenCode CLI and parses its streaming output."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.config_path = config.OPENCODE_CONFIG_PATH
        # Track agents and tools used during execution
        self.agents_used: list[str] = []
        self.tools_called: list[dict] = []
        self._processed_tool_traces: set[str] = set()
        self.current_agent: str | None = None
        self._live_mcp_events = os.getenv("LIVE_MCP_TOOL_EVENTS", "1") == "1"
        self._trace_path: Optional[Path] = None

    def build_prompt(self, query: str, chat_history: list[dict] = None) -> str:
        """Build the prompt for OpenCode with the user's query and optional chat history."""
        history_context = self._format_chat_history(chat_history) if chat_history else ""
        router_instructions = ""
        try:
            router_path = Path(self.config_path) / "agents" / "router.md"
            if router_path.exists():
                router_instructions = router_path.read_text()
        except Exception:
            router_instructions = ""

        if router_instructions:
            router_instructions = router_instructions.strip() + "\n\n"

        return f"""{router_instructions}{history_context}Current Query:
{query}

Return the delegated agent's response to the user."""

    def _format_chat_history(self, messages: list[dict]) -> str:
        """Format chat history for context injection."""
        if not messages:
            return ""

        # Use last 10 messages to avoid context window overflow
        recent_messages = messages[-10:]

        formatted = "## Previous Conversation Context\n\n"
        for msg in recent_messages:
            role = "User" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "..."
            formatted += f"**{role}**: {content}\n\n"

        formatted += "---\n\n"
        return formatted

    def run(self, query: str, chat_history: list[dict] = None) -> dict:
        """Run OpenCode with the given query and stream events."""
        prompt = self.build_prompt(query, chat_history)
        run_started_at = time.time()

        event_publisher.publish_status(self.job_id, "Starting OpenCode processing...")

        try:
            # Run OpenCode CLI with JSON output
            # Config is passed via OPENCODE_CONFIG_DIR environment variable
            base_cmd = [
                "opencode",
                "run",
                "--format", "json",
            ]
            if config.OPENCODE_AGENT:
                base_cmd.extend(["--agent", config.OPENCODE_AGENT])
            base_cmd.append(prompt)
            if shutil.which("stdbuf"):
                cmd = ["stdbuf", "-oL", "-eL", *base_cmd]
            else:
                cmd = base_cmd

            # Set environment for OpenCode config
            env = {
                **dict(os.environ),
                "OPENCODE_CONFIG_DIR": self.config_path,
                "PYTHONUNBUFFERED": "1",
                "OPENCODE_JOB_ID": self.job_id,
                "REDIS_URL": config.REDIS_URL
            }

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )

            final_result = None
            all_output = []

            # Capture raw OpenCode stream for debugging
            trace_dir = Path(os.getenv("OPENCODE_TRACE_DIR", "/output/opencode"))
            try:
                trace_dir.mkdir(parents=True, exist_ok=True)
                self._trace_path = trace_dir / f"{self.job_id}.jsonl"
                trace_handle = self._trace_path.open("w", encoding="utf-8")
            except Exception:
                trace_handle = None

            # Parse streaming output
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                # Log all output for debugging
                logger.info(f"OpenCode output: {line[:500]}")
                all_output.append(line)
                if trace_handle:
                    trace_handle.write(line + "\n")
                    trace_handle.flush()

                try:
                    event = json.loads(line)
                    self._handle_event(event)

                    # Capture result from various possible field names
                    if event.get("type") == "result":
                        final_result = event.get("data") or event.get("content") or event
                    elif event.get("type") == "text":
                        # OpenCode uses "text" type with nested "part.text" for responses
                        part = event.get("part", {})
                        text_content = part.get("text") or event.get("text") or event.get("content")
                        if text_content:
                            final_result = {"response": text_content}
                    elif event.get("type") == "message":
                        content = event.get("content") or event.get("text") or event.get("message")
                        if content:
                            final_result = {"response": content}
                    elif event.get("response"):
                        final_result = {"response": event.get("response")}

                except json.JSONDecodeError:
                    # Non-JSON output, could be the actual response
                    event_publisher.publish_status(self.job_id, line)
                    # If it's substantial text, treat it as the response
                    if len(line) > 50:
                        final_result = {"response": line}

            process.wait()
            if trace_handle:
                trace_handle.close()

            if process.returncode != 0:
                error_output = "\n".join(all_output[-200:]) if all_output else ""
                raise Exception(f"OpenCode failed: {error_output or 'unknown error'}")

            # If no structured result, use the collected output
            if not final_result and all_output:
                final_result = {"response": "\n".join(all_output)}

            result = final_result or {"status": "completed", "output": all_output}
            moved_files = self._relocate_outputs(result, run_started_at)

            # Include tracked metadata in the result
            # Preserve agent order while deduplicating
            seen_agents = set()
            ordered_agents: list[str] = []
            for agent in self.agents_used:
                if agent not in seen_agents:
                    seen_agents.add(agent)
                    ordered_agents.append(agent)

            result["_metadata"] = {
                "agents_used": ordered_agents,
                "tools_called": self.tools_called,
                "moved_files": moved_files or [],
                "opencode_trace": str(self._trace_path) if self._trace_path else None
            }

            return result

        except FileNotFoundError as exc:
            error_message = (
                "OpenCode CLI not installed. Install it in the worker image "
                "or ensure `opencode` is available on PATH."
            )
            event_publisher.publish_error(self.job_id, error_message)
            raise Exception(error_message) from exc

    def _handle_event(self, event: dict):
        """Handle an event from OpenCode output."""
        event_type = event.get("type")

        if event_type == "agent_switch":
            agent_name = event.get("agent", "unknown")
            event_publisher.publish_agent_switch(
                self.job_id,
                agent_name,
                event.get("reason", "")
            )
            # Track agent usage
            if agent_name and agent_name != "unknown":
                self.agents_used.append(agent_name)
                self.current_agent = agent_name

        elif event_type == "tool_use":
            # OpenCode tool_use events have nested part with tool info
            part = event.get("part", {}) or {}
            state = part.get("state", {}) or {}
            tool_name = part.get("tool") or event.get("tool") or "unknown"
            input_data = state.get("input") if isinstance(state.get("input"), dict) else {}

            # Handle internal agent delegation (task tool)
            if tool_name == "task":
                subagent = input_data.get("subagent_type", "")
                if subagent:
                    event_publisher.publish_agent_switch(
                        self.job_id,
                        subagent,
                        input_data.get("description", "Processing request")
                    )
                    # Track agent usage
                    self.agents_used.append(subagent)
                    self.current_agent = subagent

                # Extract tool calls from sub-agent's output if available
                output_text = self._extract_output_text(
                    state.get("output")
                    or state.get("result")
                    or part.get("output")
                    or part.get("result")
                    or event.get("output")
                    or event.get("result")
                )
                if output_text:
                    self._extract_tools_from_output(output_text, subagent)
                return

            # Determine server from tool name
            server = "arangodb" if "arango" in tool_name.lower() else "mcp"
            agent = (
                part.get("agent")
                or event.get("agent")
                or self.current_agent
                or "unknown"
            )

            event_publisher.publish_tool_call(
                self.job_id,
                tool_name,
                server,
                input_data
            )

            # Track tool call
            self.tools_called.append({
                "tool": tool_name,
                "server": server,
                "args": input_data,
                "agent": agent
            })

            # Special handling for AQL queries
            if "execute-aql" in tool_name or "aql" in tool_name.lower():
                event_publisher.publish_aql_query(
                    self.job_id,
                    input_data.get("aql_query", input_data.get("query", "")),
                    input_data.get("bind_vars", {})
                )

            # If completed, also publish result
            if state.get("status") == "completed" and state.get("output"):
                event_publisher.publish_tool_result(
                    self.job_id,
                    tool_name,
                    state.get("output"),
                    0
                )

        elif event_type == "tool_call":
            tool_name = event.get("tool") or "unknown"
            server = event.get("server", "unknown")
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            agent = event.get("agent") or self.current_agent or "unknown"

            event_publisher.publish_tool_call(
                self.job_id,
                tool_name,
                server,
                args
            )

            # Track tool call
            self.tools_called.append({
                "tool": tool_name,
                "server": server,
                "args": args,
                "agent": agent
            })

            # Special handling for AQL queries
            if event.get("tool") == "arango_query":
                args = event.get("args", {})
                event_publisher.publish_aql_query(
                    self.job_id,
                    args.get("query", args.get("aql", "")),
                    args.get("bind_vars", {})
                )

        elif event_type == "tool_result":
            tool_name = event.get("tool") or "unknown"
            result_payload = event.get("result")
            event_publisher.publish_tool_result(
                self.job_id,
                tool_name,
                result_payload,
                event.get("duration_ms", 0)
            )

            if tool_name == "task":
                output_text = self._extract_output_text(
                    result_payload
                    or event.get("output")
                    or event.get("content")
                )
                if output_text:
                    self._extract_tools_from_output(output_text, "task")

            # Check if result contains a metric
            result = result_payload or {}
            if isinstance(result, dict) and "metric_name" in result:
                event_publisher.publish_metric_found(self.job_id, result)

        elif event_type == "status":
            event_publisher.publish_status(self.job_id, event.get("message", ""))

        elif event_type == "error":
            event_publisher.publish_error(self.job_id, event.get("message", "Unknown error"))

        elif event_type == "step_start":
            # Publish step_start so frontend knows agent is actively working
            event_publisher.publish(self.job_id, {"type": "step_start"})

        elif event_type in ("text", "message", "result"):
            if event_type == "text":
                part = event.get("part", {}) or {}
                content = part.get("text") or event.get("text") or event.get("content")
            elif event_type == "message":
                content = event.get("content") or event.get("text") or event.get("message")
            else:
                content = event.get("data") or event.get("content") or event.get("result") or event

            output_text = self._extract_output_text(content)
            if output_text:
                self._extract_tools_from_output(output_text, self.current_agent or "unknown")

        # Ignore step_finish, text - these are noisy and not useful for UI
        # The final result is captured separately and shown in the results panel

    def _extract_tools_from_output(self, output: str, _agent: str):
        """Extract and publish tool usage info from sub-agent output."""
        # Pattern 1: Look for <tool_trace> section (preferred - structured)
        tool_trace_match = re.search(r'<tool_trace>(.*?)</tool_trace>', output, re.DOTALL | re.IGNORECASE)
        if tool_trace_match:
            try:
                raw_trace = tool_trace_match.group(1).strip()
                if raw_trace in self._processed_tool_traces:
                    return
                self._processed_tool_traces.add(raw_trace)
                tool_data = json.loads(raw_trace)
                if isinstance(tool_data, list):
                    for tool in tool_data:
                        tool_name = tool.get("tool", "unknown")
                        args = tool.get("args")
                        if not isinstance(args, dict):
                            args = {
                                key: value
                                for key, value in tool.items()
                                if key not in ("tool", "result", "result_count")
                            }
                        if not isinstance(args, dict):
                            args = {}
                        server = "arangodb" if "arango" in tool_name.lower() else "mcp"
                        skip_publish = self._live_mcp_events and server == "mcp"

                        if not skip_publish:
                            # Publish tool call
                            event_publisher.publish_tool_call(
                                self.job_id,
                                tool_name,
                                server,
                                args
                            )

                        # Track tool call for metadata
                        self.tools_called.append({
                            "tool": tool_name,
                            "server": server,
                            "args": args,
                            "agent": _agent or "unknown"
                        })

                        # If it's an AQL query, also publish that
                        query = args.get("query")
                        if isinstance(query, str) and query:
                            event_publisher.publish_aql_query(
                                self.job_id,
                                query,
                                args.get("bind_vars", {})
                            )
                        # Publish result if available
                        if not skip_publish and (tool.get("result") or tool.get("result_count")):
                            event_publisher.publish_tool_result(
                                self.job_id,
                                tool_name,
                                {"result": tool.get("result"), "count": tool.get("result_count")},
                                0
                            )
                return  # Found tool_trace, don't need fallback patterns
            except (json.JSONDecodeError, AttributeError):
                pass  # Fall through to other patterns

    def _extract_output_text(self, output) -> str:
        if output is None:
            return ""
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            for key in ("output", "content", "text", "response", "message", "result", "data"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(output)
        return json.dumps(output)

    def _relocate_outputs(self, result: dict, run_started_at: float) -> list[dict]:
        """Mirror newly created output files into the mounted /output directory."""
        output_root = os.getenv("OUTPUT_ROOT", "/output")
        exports_dir = os.getenv("OUTPUT_PATH", os.path.join(output_root, "exports"))
        citations_dir = os.getenv("CITATION_OUTPUT_PATH", os.path.join(output_root, "citations"))
        scan_dirs_env = os.getenv("OPENCODE_OUTPUT_SCAN_DIRS", "/app")
        scan_dirs = [d for d in scan_dirs_env.split(os.pathsep) if d]

        image_exts = {".png", ".jpg", ".jpeg"}
        table_exts = {".xlsx", ".csv", ".tsv"}
        allowed_exts = image_exts | table_exts

        moved = []
        moved_map = {}

        for scan_dir in scan_dirs:
            scan_path = Path(scan_dir)
            if not scan_path.exists():
                continue

            for path in scan_path.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in allowed_exts:
                    continue
                try:
                    if path.stat().st_mtime < run_started_at - 5:
                        continue
                except FileNotFoundError:
                    continue

                # Skip files already under the output root
                try:
                    if Path(output_root) in path.parents:
                        continue
                except Exception:
                    pass

                if path.suffix.lower() in image_exts:
                    dest_dir = Path(citations_dir)
                else:
                    dest_dir = Path(exports_dir)

                dest_path = self._copy_to_output(path, dest_dir)
                if dest_path:
                    moved.append({"from": str(path), "to": str(dest_path)})
                    moved_map[str(path)] = str(dest_path)

        if moved_map:
            self._rewrite_result_paths(result, moved_map)

        return moved

    def _copy_to_output(self, src: Path, dest_dir: Path) -> Optional[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / src.name
        if dest_path.exists():
            try:
                if dest_path.stat().st_size == src.stat().st_size:
                    return dest_path
            except FileNotFoundError:
                pass
            stem = src.stem
            suffix = src.suffix
            counter = 1
            while True:
                candidate = dest_dir / f"{stem}_{counter}{suffix}"
                if not candidate.exists():
                    dest_path = candidate
                    break
                counter += 1

        try:
            shutil.copy2(src, dest_path)
        except Exception as exc:
            logger.warning(f"Failed to copy output file {src} to {dest_path}: {exc}")
            return None

        return dest_path

    def _rewrite_result_paths(self, result: dict, moved_map: dict[str, str]) -> None:
        if not isinstance(result, dict):
            return
        for key in ("response", "text", "content", "message"):
            value = result.get(key)
            if isinstance(value, str):
                result[key] = self._rewrite_paths_in_text(value, moved_map)

    @staticmethod
    def _rewrite_paths_in_text(text: str, moved_map: dict[str, str]) -> str:
        for src, dest in moved_map.items():
            text = text.replace(src, dest)
        return text
