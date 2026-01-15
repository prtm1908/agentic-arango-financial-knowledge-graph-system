import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import (
    QueryRequest, JobResponse, JobStatus,
    ChatCreate, ChatUpdate, ChatResponse, ChatDetailResponse, ChatListResponse, ChatQueryRequest, ChatMessage
)
from job_queue import queue
from events import event_publisher
from arangodb import (
    list_companies, list_filings_for_company, ensure_schema, seed_data,
    create_chat, get_chat_metadata, get_chat_content, add_message_to_chat,
    list_chats as db_list_chats, count_chats, update_chat_metadata, delete_chat
)
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await queue.connect()
    await event_publisher.connect()
    await run_in_threadpool(ensure_schema)
    if settings.arango_seed_data:
        await run_in_threadpool(seed_data)
    yield
    # Shutdown
    await queue.disconnect()
    await event_publisher.disconnect()


app = FastAPI(
    title="Financial Knowledge Graph API",
    description="API for querying and extracting financial data",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/api/query", response_model=JobResponse)
async def submit_query(request: QueryRequest):
    """Submit a query for processing."""
    job_id = await queue.enqueue_job(request.query)
    # Publish initial "queued" event so late subscribers can still see it
    await event_publisher.publish(job_id, {"type": "status", "message": "Job queued, waiting for worker..."})
    return JobResponse(
        job_id=job_id,
        status="queued",
        message="Query submitted successfully"
    )


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a job."""
    job = await queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/events/{job_id}")
async def stream_events(job_id: str):
    """Stream events for a job via SSE."""
    job = await queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # Send immediate "connected" event so frontend knows stream is ready
        yield {
            "event": "connected",
            "data": json.dumps({"type": "connected", "job_id": job_id})
        }
        async for event in event_publisher.subscribe(job_id):
            yield {
                "event": event.get("type", "message"),
                "data": json.dumps(event)
            }

    return EventSourceResponse(event_generator(), ping=5)


@app.get("/api/companies")
async def list_companies():
    """List all companies in the knowledge graph."""
    companies = await run_in_threadpool(list_companies)
    return {"companies": companies}


@app.get("/api/filings/{company_id}")
async def list_filings(company_id: str):
    """List all filings for a company."""
    filings = await run_in_threadpool(list_filings_for_company, company_id)
    return {"filings": filings, "company_id": company_id}


# ============================================================================
# Chat Endpoints
# ============================================================================

@app.post("/api/chats", response_model=ChatResponse)
async def create_new_chat(request: ChatCreate):
    """Create a new chat session."""
    chat = await run_in_threadpool(create_chat, request.title, request.initial_message)
    return ChatResponse(
        chat_id=chat["_key"],
        title=chat["title"],
        created_at=chat["created_at"],
        updated_at=chat["updated_at"],
        message_count=chat["message_count"],
        last_message_preview=chat.get("last_message_preview"),
        agents_used=chat.get("agents_used", [])
    )


@app.get("/api/chats", response_model=ChatListResponse)
async def list_all_chats(skip: int = 0, limit: int = 20):
    """List all chats, sorted by updated_at descending."""
    chats = await run_in_threadpool(db_list_chats, skip, limit)
    total = await run_in_threadpool(count_chats)

    chat_responses = [
        ChatResponse(
            chat_id=chat["_key"],
            title=chat["title"],
            created_at=chat["created_at"],
            updated_at=chat["updated_at"],
            message_count=chat["message_count"],
            last_message_preview=chat.get("last_message_preview"),
            agents_used=chat.get("agents_used", [])
        )
        for chat in chats
    ]

    return ChatListResponse(chats=chat_responses, total=total)


@app.get("/api/chats/{chat_id}", response_model=ChatDetailResponse)
async def get_chat(chat_id: str):
    """Get full chat details including all messages."""
    metadata = await run_in_threadpool(get_chat_metadata, chat_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Chat not found")

    content = await run_in_threadpool(get_chat_content, chat_id)
    if not content:
        raise HTTPException(status_code=404, detail="Chat content not found")

    messages = [
        ChatMessage(
            id=msg["id"],
            role=msg["role"],
            content=msg["content"],
            timestamp=msg["timestamp"],
            metadata=msg.get("metadata")
        )
        for msg in content.get("messages", [])
    ]

    return ChatDetailResponse(
        chat_id=metadata["_key"],
        title=metadata["title"],
        created_at=metadata["created_at"],
        updated_at=metadata["updated_at"],
        message_count=metadata["message_count"],
        last_message_preview=metadata.get("last_message_preview"),
        agents_used=metadata.get("agents_used", []),
        messages=messages,
        settings=content.get("settings", {})
    )


@app.put("/api/chats/{chat_id}", response_model=ChatResponse)
async def update_chat(chat_id: str, request: ChatUpdate):
    """Update chat metadata (title, etc.)."""
    updates = {}
    if request.title is not None:
        updates["title"] = request.title

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    updated = await run_in_threadpool(update_chat_metadata, chat_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Chat not found")

    return ChatResponse(
        chat_id=updated["_key"],
        title=updated["title"],
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
        message_count=updated["message_count"],
        last_message_preview=updated.get("last_message_preview"),
        agents_used=updated.get("agents_used", [])
    )


@app.delete("/api/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str):
    """Delete a chat and its JSON file."""
    success = await run_in_threadpool(delete_chat, chat_id)
    if not success:
        raise HTTPException(status_code=404, detail="Chat not found")
    return {"status": "deleted", "chat_id": chat_id}


@app.post("/api/chats/{chat_id}/query", response_model=JobResponse)
async def submit_chat_query(chat_id: str, request: ChatQueryRequest):
    """Submit a query with chat context."""
    # Verify chat exists
    metadata = await run_in_threadpool(get_chat_metadata, chat_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Add user message to chat
    user_message = {
        "role": "user",
        "content": request.query
    }
    await run_in_threadpool(add_message_to_chat, chat_id, user_message)

    # Enqueue job with chat_id for context
    job_id = await queue.enqueue_job(request.query, chat_id=chat_id)

    # Publish initial "queued" event
    await event_publisher.publish(job_id, {"type": "status", "message": "Job queued, waiting for worker..."})

    return JobResponse(
        job_id=job_id,
        status="queued",
        message="Query submitted successfully"
    )

