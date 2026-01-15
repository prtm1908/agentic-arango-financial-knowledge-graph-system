from pydantic import BaseModel
from typing import Optional, Any, Literal
from datetime import datetime
import uuid


class QueryRequest(BaseModel):
    query: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# Event types for SSE
class AgentSwitchEvent(BaseModel):
    type: Literal["agent_switch"] = "agent_switch"
    agent: str
    reason: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    server: str
    args: dict


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str
    result: Any
    duration_ms: int


class MetricFoundEvent(BaseModel):
    type: Literal["metric_found"] = "metric_found"
    metric: dict


class AqlQueryEvent(BaseModel):
    type: Literal["aql_query"] = "aql_query"
    query: str
    bind_vars: dict


class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    message: str


class CompleteEvent(BaseModel):
    type: Literal["complete"] = "complete"
    result: Any


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


EventType = (
    AgentSwitchEvent
    | ToolCallEvent
    | ToolResultEvent
    | MetricFoundEvent
    | AqlQueryEvent
    | StatusEvent
    | CompleteEvent
    | ErrorEvent
)


# ============================================================================
# Chat Models
# ============================================================================

class ToolCallInfo(BaseModel):
    tool: str
    server: str
    args: Optional[dict] = None
    duration_ms: Optional[int] = None


class MessageMetadata(BaseModel):
    agents_used: Optional[list[str]] = None
    tools_called: Optional[list[ToolCallInfo]] = None
    event_history: Optional[list[dict]] = None
    job_id: Optional[str] = None


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "system"]
    content: str
    timestamp: datetime
    metadata: Optional[MessageMetadata] = None


class ChatCreate(BaseModel):
    title: Optional[str] = None
    initial_message: Optional[str] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None


class ChatResponse(BaseModel):
    chat_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    last_message_preview: Optional[str] = None
    agents_used: list[str] = []


class ChatDetailResponse(ChatResponse):
    messages: list[ChatMessage]
    settings: dict = {}


class ChatListResponse(BaseModel):
    chats: list[ChatResponse]
    total: int


class ChatQueryRequest(BaseModel):
    query: str
