#!/usr/bin/env python3
"""
Vector Store MCP Server

Provides tools for:
- Creating embeddings using Cohere
- Storing and searching vectors in Qdrant
- Managing document embeddings for financial filings
"""

import os
import json
import asyncio
import base64
import mimetypes
import time
from typing import Any

import cohere
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None

# Configuration
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
IMAGE_COLLECTION_NAME = "financial_document_images"
IMAGE_EMBED_MODEL = "embed-v4.0"
IMAGE_EMBED_CONCURRENCY = int(os.getenv("COHERE_IMAGE_EMBED_CONCURRENCY", "100"))
REDIS_URL = os.getenv("REDIS_URL", "")
OPENCODE_JOB_ID = os.getenv("OPENCODE_JOB_ID", "")
OPENCODE_AGENT_NAME = os.getenv("OPENCODE_AGENT_NAME", "")

# Initialize clients
qdrant = AsyncQdrantClient(url=QDRANT_URL)
cohere_async_client = cohere.AsyncClientV2(COHERE_API_KEY) if COHERE_API_KEY else None

# Create MCP server
server = Server("vector_store")
_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL or redis is None:
        return None
    try:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    except Exception:
        _redis_client = None
    return _redis_client


def _publish_event(event: dict):
    if not OPENCODE_JOB_ID:
        return
    client = _get_redis_client()
    if client is None:
        return
    payload = dict(event)
    payload.setdefault("timestamp", int(time.time() * 1000))
    event_json = json.dumps(payload)
    history_key = f"event_history:{OPENCODE_JOB_ID}"
    channel = f"events:{OPENCODE_JOB_ID}"
    try:
        client.rpush(history_key, event_json)
        client.ltrim(history_key, -100, -1)
        client.expire(history_key, 300)
        client.publish(channel, event_json)
    except Exception:
        return


def _publish_tool_call(name: str, args: dict):
    payload = {"type": "tool_call", "tool": name, "server": "mcp", "args": args}
    if OPENCODE_AGENT_NAME:
        payload["agent"] = OPENCODE_AGENT_NAME
    _publish_event(payload)


def _publish_tool_result(name: str, result: dict, duration_ms: int):
    payload = {"type": "tool_result", "tool": name, "result": result, "duration_ms": duration_ms}
    if OPENCODE_AGENT_NAME:
        payload["agent"] = OPENCODE_AGENT_NAME
    _publish_event(payload)


def _tool_response(name: str, payload: dict, started_at: float) -> list[TextContent]:
    duration_ms = int((time.time() - started_at) * 1000)
    _publish_tool_result(name, payload, duration_ms)
    return [TextContent(type="text", text=json.dumps(payload))]


async def ensure_image_collection(embedding_dim: int):
    """Ensure the image vector collection exists with the correct dimension."""
    collections = (await qdrant.get_collections()).collections
    exists = any(c.name == IMAGE_COLLECTION_NAME for c in collections)
    if not exists:
        await qdrant.create_collection(
            collection_name=IMAGE_COLLECTION_NAME,
            vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE),
        )
        return

    info = await qdrant.get_collection(IMAGE_COLLECTION_NAME)
    current_dim = info.config.params.vectors.size
    if current_dim != embedding_dim:
        raise ValueError(
            f"Image embedding dimension mismatch: collection={current_dim}, embedding={embedding_dim}"
        )


def _image_to_data_url(image_base64: str, mime_type: str = "image/png") -> str:
    if image_base64.startswith("data:"):
        return image_base64
    return f"data:{mime_type};base64,{image_base64}"


def _image_path_to_data_url(path: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/png"
    with open(path, "rb") as handle:
        raw = handle.read()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


async def _embed_image_with_semaphore(semaphore: asyncio.Semaphore, image_data_url: str) -> list[float]:
    if not cohere_async_client:
        raise ValueError("COHERE_API_KEY not configured or AsyncClientV2 unavailable")
    async with semaphore:
        response = await cohere_async_client.embed(
            model=IMAGE_EMBED_MODEL,
            input_type="image",
            texts=[],
            images=[image_data_url],
        )
        return response.embeddings[0]


async def _embed_text_query(text: str) -> list[float]:
    if not cohere_async_client:
        raise ValueError("COHERE_API_KEY not configured or AsyncClientV2 unavailable")
    response = await cohere_async_client.embed(
        model=IMAGE_EMBED_MODEL,
        input_type="search_query",
        texts=[text],
        images=[]
    )
    return response.embeddings[0]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="check_embeddings_exist",
            description="Check if embeddings exist for a document",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "The document ID to check"
                    }
                },
                "required": ["document_id"]
            }
        ),
        Tool(
            name="create_page_image_embeddings",
            description="Create and store image embeddings for document pages",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "The document ID"
                    },
                    "pages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "page_num": {"type": "integer"},
                                "image_base64": {"type": "string"},
                                "image_path": {"type": "string"}
                            }
                        },
                        "description": "Array of page objects with page_num and image data"
                    }
                },
                "required": ["document_id", "pages"]
            }
        ),
        Tool(
            name="search_pages",
            description="Search for relevant pages using semantic similarity",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "The document ID to search within"
                    },
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 20
                    }
                },
                "required": ["document_id", "query"]
            }
        ),
        Tool(
            name="delete_document_embeddings",
            description="Delete all embeddings for a document",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "The document ID"
                    }
                },
                "required": ["document_id"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    started_at = time.time()
    _publish_tool_call(name, arguments)

    if name == "check_embeddings_exist":
        document_id = arguments["document_id"]

        collections = (await qdrant.get_collections()).collections
        if not any(c.name == IMAGE_COLLECTION_NAME for c in collections):
            payload = {"exists": False, "document_id": document_id, "page_count": 0}
            return _tool_response(name, payload, started_at)

        # Check if any points exist for this document
        results = await qdrant.scroll(
            collection_name=IMAGE_COLLECTION_NAME,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    )
                ]
            ),
            limit=1
        )

        exists = len(results[0]) > 0
        count = 0
        if exists:
            # Get count
            count_result = await qdrant.count(
                collection_name=IMAGE_COLLECTION_NAME,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document_id)
                        )
                    ]
                )
            )
            count = count_result.count

        payload = {"exists": exists, "document_id": document_id, "page_count": count}
        return _tool_response(name, payload, started_at)

    elif name == "create_page_image_embeddings":
        document_id = arguments["document_id"]
        pages = arguments["pages"]

        if not cohere_async_client:
            payload = {"error": "COHERE_API_KEY not configured or AsyncClientV2 unavailable"}
            return _tool_response(name, payload, started_at)

        semaphore = asyncio.Semaphore(IMAGE_EMBED_CONCURRENCY)

        async def embed_one(page: dict) -> dict:
            page_num = page.get("page_num")
            image_base64 = page.get("image_base64")
            image_path = page.get("image_path")

            if not image_base64 and not image_path:
                return {"page_num": page_num, "error": "Missing image_base64 or image_path"}

            try:
                if image_base64:
                    image_data_url = _image_to_data_url(image_base64)
                else:
                    image_data_url = _image_path_to_data_url(image_path)
                embedding = await _embed_image_with_semaphore(semaphore, image_data_url)
                return {
                    "page_num": page_num,
                    "embedding": embedding,
                    "image_path": image_path
                }
            except Exception as exc:
                return {"page_num": page_num, "error": str(exc)}

        results = await asyncio.gather(*(embed_one(page) for page in pages))

        embeddings = [r for r in results if r.get("embedding")]
        errors = [r for r in results if r.get("error")]

        if not embeddings:
            payload = {
                "success": False,
                "document_id": document_id,
                "pages_embedded": 0,
                "errors": errors
            }
            return _tool_response(name, payload, started_at)

        embedding_dim = len(embeddings[0]["embedding"])
        await ensure_image_collection(embedding_dim)

        points = []
        for item in embeddings:
            page_num = item["page_num"]
            point_id = f"{document_id}_image_page_{page_num}"
            payload = {
                "document_id": document_id,
                "page_num": page_num
            }
            if item.get("image_path"):
                payload["image_path"] = item["image_path"]

            points.append(PointStruct(
                id=hash(point_id) % (2**63),
                vector=item["embedding"],
                payload=payload
            ))

        if points:
            await qdrant.upsert(
                collection_name=IMAGE_COLLECTION_NAME,
                points=points
            )

        payload = {
            "success": True,
            "document_id": document_id,
            "pages_embedded": len(points),
            "errors": errors
        }
        return _tool_response(name, payload, started_at)

    elif name == "search_pages":
        document_id = arguments["document_id"]
        query = arguments["query"]
        top_k = arguments.get("top_k", 10)

        collections = (await qdrant.get_collections()).collections
        if not any(c.name == IMAGE_COLLECTION_NAME for c in collections):
            payload = {"document_id": document_id, "query": query, "pages": []}
            return _tool_response(name, payload, started_at)

        query_embedding = await _embed_text_query(query)

        results = await qdrant.query_points(
            collection_name=IMAGE_COLLECTION_NAME,
            query=query_embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    )
                ]
            ),
            limit=top_k,
            with_payload=True
        )

        scored_points = results.points if hasattr(results, "points") else results
        pages = [
            {
                "page_num": r.payload["page_num"],
                "score": r.score,
                "image_path": r.payload.get("image_path", "")
            }
            for r in scored_points
        ]

        payload = {"document_id": document_id, "query": query, "pages": pages}
        return _tool_response(name, payload, started_at)

    elif name == "delete_document_embeddings":
        document_id = arguments["document_id"]

        if any(c.name == IMAGE_COLLECTION_NAME for c in (await qdrant.get_collections()).collections):
            await qdrant.delete(
                collection_name=IMAGE_COLLECTION_NAME,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document_id)
                        )
                    ]
                )
            )

        payload = {"success": True, "document_id": document_id}
        return _tool_response(name, payload, started_at)

    else:
        payload = {"error": f"Unknown tool: {name}"}
        return _tool_response(name, payload, started_at)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
