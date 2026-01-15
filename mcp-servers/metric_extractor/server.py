#!/usr/bin/env python3
"""
Metric Extractor MCP Server

Provides a single deterministic tool for extracting financial metrics from PDFs.
The entire pipeline (embeddings, search, extraction) runs in one call - no agent round-trips.
"""

import os
import sys
import json
import base64
import hashlib
import asyncio
import mimetypes
import time
import uuid
import shutil
import logging
import concurrent.futures
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any
from io import BytesIO

# Configure logging to stderr (stdout is used for MCP protocol)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger("metric_extractor")

import fitz  # PyMuPDF
import cohere
from google import genai
from google.genai import types
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
except Exception:
    redis = None

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
REDIS_URL = os.getenv("REDIS_URL", "")
OPENCODE_JOB_ID = os.getenv("OPENCODE_JOB_ID", "")
OPENCODE_AGENT_NAME = os.getenv("OPENCODE_AGENT_NAME", "")

# PDF processing paths
FILINGS_PATH = os.getenv("FILINGS_PATH", "/data/filings")
DOWNLOAD_PATH = os.getenv("PDF_DOWNLOAD_PATH", os.path.join(FILINGS_PATH, "downloads"))
PAGE_IMAGE_ROOT = os.getenv("PDF_PAGE_IMAGE_ROOT", os.path.join(FILINGS_PATH, "page_images"))
TEMP_PATH = os.getenv("TEMP_PATH", "/tmp/metric_extractor")
RENDER_WORKERS = int(os.getenv("PDF_RENDER_WORKERS", max(2, min(16, (os.cpu_count() or 1) * 2))))

# Embedding config
IMAGE_COLLECTION_NAME = "financial_document_images"
IMAGE_EMBED_MODEL = "embed-v4.0"
IMAGE_EMBED_CONCURRENCY = int(os.getenv("COHERE_IMAGE_EMBED_CONCURRENCY", "100"))
DEFAULT_DPI = 200
SEARCH_TOP_K = 20

MODEL_NAME = "gemini-3-flash-preview"

# Ensure directories exist
os.makedirs(TEMP_PATH, exist_ok=True)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
os.makedirs(PAGE_IMAGE_ROOT, exist_ok=True)

# Initialize clients
gemini_client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else None
cohere_client = cohere.AsyncClientV2(COHERE_API_KEY) if COHERE_API_KEY else None
qdrant_client = AsyncQdrantClient(url=QDRANT_URL)

# Create MCP server
server = Server("metric_extractor")
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


# =============================================================================
# PDF Processing Functions
# =============================================================================

def resolve_pdf_path(pdf_url: str) -> str:
    """Resolve a PDF URL or path to a local file path."""
    if not pdf_url:
        raise ValueError("pdf_url is required")

    parsed = urlparse(pdf_url)
    scheme = parsed.scheme.lower()

    if scheme in ("http", "https"):
        url_hash = hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()[:16]
        local_path = os.path.join(DOWNLOAD_PATH, f"{url_hash}.pdf")

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path

        request = Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
        temp_path = f"{local_path}.tmp"
        with urlopen(request) as response, open(temp_path, "wb") as handle:
            handle.write(response.read())
        os.replace(temp_path, local_path)
        return local_path

    if scheme == "file":
        return parsed.path

    if scheme:
        raise ValueError(f"Unsupported PDF URL scheme: {scheme}")

    local_path = pdf_url
    if not os.path.isabs(local_path):
        local_path = os.path.join(FILINGS_PATH, local_path)
    return local_path


def _render_page_to_png(args: tuple[str, int, int, str]) -> dict[str, Any]:
    """Render a single PDF page to PNG."""
    pdf_path, page_num, dpi, output_dir = args
    doc = fitz.open(pdf_path)
    try:
        if page_num < 1 or page_num > len(doc):
            return {"page_num": page_num, "error": f"Page {page_num} out of range"}
        page = doc[page_num - 1]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        output_path = os.path.join(output_dir, f"page_{page_num:04d}.png")
        pix.save(output_path)
        return {
            "page_num": page_num,
            "width": pix.width,
            "height": pix.height,
            "image_path": output_path
        }
    except Exception as exc:
        return {"page_num": page_num, "error": str(exc)}
    finally:
        doc.close()


def render_all_pages(pdf_path: str, dpi: int = DEFAULT_DPI) -> tuple[str, list[dict]]:
    """Render all PDF pages to images. Returns (output_dir, page_results)."""
    output_dir = os.path.join(PAGE_IMAGE_ROOT, str(uuid.uuid4()))
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    page_count = len(doc)
    doc.close()

    page_args = [(pdf_path, page_num, dpi, output_dir) for page_num in range(1, page_count + 1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=RENDER_WORKERS) as executor:
        results = list(executor.map(_render_page_to_png, page_args))

    return output_dir, results


def create_subset_pdf(pdf_path: str, pages: list[int]) -> tuple[bytes, dict[str, int]]:
    """Create a subset PDF with specified pages. Returns (pdf_bytes, page_mapping)."""
    doc = fitz.open(pdf_path)
    new_doc = fitz.open()

    page_mapping = {}
    subset_page_num = 1
    for page_num in sorted(set(pages)):
        if 1 <= page_num <= len(doc):
            new_doc.insert_pdf(doc, from_page=page_num - 1, to_page=page_num - 1)
            page_mapping[str(subset_page_num)] = page_num
            subset_page_num += 1

    pdf_bytes = new_doc.tobytes()
    new_doc.close()
    doc.close()

    return pdf_bytes, page_mapping


def cleanup_images(folder_path: str):
    """Clean up rendered page images."""
    if os.path.exists(folder_path) and PAGE_IMAGE_ROOT in folder_path:
        shutil.rmtree(folder_path, ignore_errors=True)


# =============================================================================
# Embedding Functions
# =============================================================================

def _image_path_to_data_url(path: str) -> str:
    """Convert image file to data URL."""
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/png"
    with open(path, "rb") as handle:
        raw = handle.read()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


async def _embed_image_with_semaphore(semaphore: asyncio.Semaphore, image_data_url: str) -> list[float]:
    """Embed a single image with concurrency control."""
    if not cohere_client:
        raise ValueError("COHERE_API_KEY not configured")
    async with semaphore:
        response = await cohere_client.embed(
            model=IMAGE_EMBED_MODEL,
            input_type="image",
            texts=[],
            images=[image_data_url],
        )
        return response.embeddings.float_[0]


async def _embed_text_query(text: str) -> list[float]:
    """Embed a text query for search."""
    if not cohere_client:
        raise ValueError("COHERE_API_KEY not configured")
    response = await cohere_client.embed(
        model=IMAGE_EMBED_MODEL,
        input_type="search_query",
        texts=[text],
        images=[]
    )
    return response.embeddings.float_[0]


async def ensure_collection(embedding_dim: int):
    """Ensure the vector collection exists."""
    collections = (await qdrant_client.get_collections()).collections
    exists = any(c.name == IMAGE_COLLECTION_NAME for c in collections)
    if not exists:
        await qdrant_client.create_collection(
            collection_name=IMAGE_COLLECTION_NAME,
            vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE),
        )


async def check_embeddings_exist(document_id: str) -> tuple[bool, int]:
    """Check if embeddings exist for a document. Returns (exists, count)."""
    logger.info(f"Checking embeddings for document_id={document_id}")

    collections = (await qdrant_client.get_collections()).collections
    if not any(c.name == IMAGE_COLLECTION_NAME for c in collections):
        logger.info(f"  Collection '{IMAGE_COLLECTION_NAME}' does not exist yet")
        return False, 0

    results = await qdrant_client.scroll(
        collection_name=IMAGE_COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        ),
        limit=1
    )

    exists = len(results[0]) > 0
    count = 0
    if exists:
        count_result = await qdrant_client.count(
            collection_name=IMAGE_COLLECTION_NAME,
            count_filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            )
        )
        count = count_result.count
        logger.info(f"  Found {count} existing embeddings")
    else:
        logger.info(f"  No existing embeddings found")

    return exists, count


async def create_embeddings(document_id: str, page_results: list[dict]) -> int:
    """Create and store embeddings for document pages. Returns count of embedded pages."""
    logger.info(f"Creating embeddings for {len(page_results)} pages (document_id={document_id})")
    semaphore = asyncio.Semaphore(IMAGE_EMBED_CONCURRENCY)

    async def embed_one(page: dict) -> dict:
        page_num = page.get("page_num")
        image_path = page.get("image_path")
        if not image_path or page.get("error"):
            logger.warning(f"Page {page_num}: skipping - {page.get('error', 'Missing image_path')}")
            return {"page_num": page_num, "error": page.get("error", "Missing image_path")}
        try:
            image_data_url = _image_path_to_data_url(image_path)
            embedding = await _embed_image_with_semaphore(semaphore, image_data_url)
            logger.debug(f"Page {page_num}: embedded successfully")
            return {"page_num": page_num, "embedding": embedding, "image_path": image_path}
        except Exception as exc:
            logger.error(f"Page {page_num}: embedding FAILED - {exc}")
            return {"page_num": page_num, "error": str(exc)}

    results = await asyncio.gather(*(embed_one(page) for page in page_results))
    embeddings = [r for r in results if r.get("embedding")]
    errors = [r for r in results if r.get("error")]

    logger.info(f"Embedding results: {len(embeddings)} succeeded, {len(errors)} failed")
    if errors and len(errors) <= 5:
        for e in errors:
            logger.error(f"  Page {e['page_num']}: {e['error']}")
    elif errors:
        logger.error(f"  First error: Page {errors[0]['page_num']}: {errors[0]['error']}")

    if not embeddings:
        logger.error("No embeddings created! Cannot proceed with search.")
        return 0

    embedding_dim = len(embeddings[0]["embedding"])
    await ensure_collection(embedding_dim)

    points = []
    for item in embeddings:
        page_num = item["page_num"]
        point_id = f"{document_id}_image_page_{page_num}"
        payload = {"document_id": document_id, "page_num": page_num}
        if item.get("image_path"):
            payload["image_path"] = item["image_path"]
        points.append(PointStruct(
            id=hash(point_id) % (2**63),
            vector=item["embedding"],
            payload=payload
        ))

    if points:
        logger.info(f"Upserting {len(points)} points to Qdrant collection '{IMAGE_COLLECTION_NAME}'")
        await qdrant_client.upsert(collection_name=IMAGE_COLLECTION_NAME, points=points)
        logger.info(f"Successfully stored {len(points)} embeddings in Qdrant")

    return len(points)


async def search_pages(document_id: str, query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """Search for relevant pages. Returns list of {page_num, score}."""
    logger.info(f"Searching for '{query}' in document_id={document_id} (top_k={top_k})")

    collections = (await qdrant_client.get_collections()).collections
    collection_names = [c.name for c in collections]
    logger.info(f"Available Qdrant collections: {collection_names}")

    if not any(c.name == IMAGE_COLLECTION_NAME for c in collections):
        logger.error(f"Collection '{IMAGE_COLLECTION_NAME}' not found!")
        return []

    # Check how many points exist for this document_id
    count_result = await qdrant_client.count(
        collection_name=IMAGE_COLLECTION_NAME,
        count_filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        )
    )
    logger.info(f"Found {count_result.count} points in Qdrant for document_id={document_id}")

    query_embedding = await _embed_text_query(query)
    logger.info(f"Query embedded successfully (dim={len(query_embedding)})")

    results = await qdrant_client.query_points(
        collection_name=IMAGE_COLLECTION_NAME,
        query=query_embedding,
        query_filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        ),
        limit=top_k,
        with_payload=True
    )

    scored_points = results.points if hasattr(results, "points") else results
    logger.info(f"Search returned {len(scored_points)} results")

    if scored_points:
        top_3 = scored_points[:3]
        for r in top_3:
            logger.info(f"  Page {r.payload['page_num']}: score={r.score:.4f}")

    return [
        {"page_num": r.payload["page_num"], "score": r.score}
        for r in scored_points
    ]


# =============================================================================
# Gemini Extraction
# =============================================================================

PDF_EXTRACTION_PROMPT = """You are a financial data extraction specialist. Extract the requested metric from the provided PDF.

Metric to extract: {metric_name}

Return a JSON object with the following fields:
- metric_name: The name of the metric
- value: The numeric value as shown in the PDF (including commas/decimals, e.g., "5,833" or "12.45")
- unit: The CURRENCY code only (e.g., "INR", "USD", "EUR", "GBP"). This should ONLY contain the currency, not the scale.
- denomination: The SCALE/MAGNITUDE of the value (e.g., "Crores", "Lakhs", "Millions", "Billions", "Thousands"). This represents how the number should be interpreted.
- source_page_number: The PDF page number (1-indexed) where you found the value. IMPORTANT: This is the actual PDF page number as it would appear in a PDF viewer (1, 2, 3, etc.), NOT the page number printed on the document itself.

IMPORTANT distinctions:
- "unit" = currency (INR, USD, etc.)
- "denomination" = scale (Crores, Millions, Billions, Lakhs, Thousands, etc.)

Example: If the PDF shows "Revenue: ₹5,833 Crores", the result should be:
- value: "5,833"
- unit: "INR"
- denomination: "Crores"

If you cannot find the metric, set value to null, unit to null, denomination to null, and source_page_number to null.

Return ONLY the JSON object, no additional text."""


def extract_with_gemini(pdf_bytes: bytes, metric_name: str, page_mapping: dict[str, int]) -> dict:
    """Extract metric from PDF using Gemini. Returns extraction result."""
    logger.info(f"Extracting '{metric_name}' with Gemini (PDF size: {len(pdf_bytes)} bytes)")

    if not gemini_client:
        logger.error("GOOGLE_API_KEY not configured!")
        return {"error": "GOOGLE_API_KEY not configured"}

    prompt = PDF_EXTRACTION_PROMPT.format(metric_name=metric_name)

    content_parts = [
        prompt,
        types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    ]

    response = gemini_client.models.generate_content(
        model=MODEL_NAME,
        contents=content_parts
    )

    raw_text = response.text or ""
    result_text = raw_text.strip()

    # Parse JSON from response
    if result_text.startswith("```"):
        result_text = result_text.split("```")[1]
        if result_text.startswith("json"):
            result_text = result_text[4:]

    try:
        result = json.loads(result_text)
        logger.info(f"Gemini extraction successful")
    except json.JSONDecodeError:
        logger.error(f"Failed to parse Gemini response: {raw_text[:200]}")
        return {
            "metric_name": metric_name,
            "value": None,
            "error": "Failed to parse extraction result",
            "raw_response": raw_text[:500]
        }

    # Apply page mapping to convert subset page to original page
    subset_page = result.pop("source_page_number", None)
    if subset_page is not None:
        subset_page_str = str(subset_page)
        if page_mapping and subset_page_str in page_mapping:
            result["source_page"] = page_mapping[subset_page_str]
            logger.info(f"  Mapped subset page {subset_page} -> original page {result['source_page']}")
        else:
            result["source_page"] = subset_page
            logger.warning(f"  Could not map subset page {subset_page}, using as-is")

    return result


# =============================================================================
# Main Extraction Pipeline
# =============================================================================

async def extract_metric_pipeline(
    pdf_url: str,
    document_id: str,
    metric_name: str
) -> dict:
    """
    Complete metric extraction pipeline - deterministic, no agent round-trips.

    1. Check if embeddings exist for document
    2. If not, render pages and create embeddings
    3. Search for relevant pages
    4. Create subset PDF from top pages
    5. Extract metric using Gemini
    """
    steps_completed = []
    image_dir = None

    logger.info("=" * 60)
    logger.info(f"METRIC EXTRACTION PIPELINE START")
    logger.info(f"  pdf_url: {pdf_url}")
    logger.info(f"  document_id: {document_id}")
    logger.info(f"  metric_name: {metric_name}")
    logger.info("=" * 60)

    try:
        # Step 1: Resolve PDF path
        logger.info("[Step 1/6] Resolving PDF path...")
        pdf_path = resolve_pdf_path(pdf_url)
        logger.info(f"  PDF resolved to: {pdf_path}")
        steps_completed.append("download_pdf")

        # Step 2: Check if embeddings exist
        logger.info("[Step 2/6] Checking if embeddings exist...")
        exists, count = await check_embeddings_exist(document_id)
        logger.info(f"  Embeddings exist: {exists} (count: {count})")
        steps_completed.append("check_embeddings")

        # Step 3: Create embeddings if needed
        if not exists:
            logger.info("[Step 3/6] Rendering PDF pages to images...")
            image_dir, page_results = render_all_pages(pdf_path, DEFAULT_DPI)
            logger.info(f"  Rendered {len(page_results)} pages to {image_dir}")
            steps_completed.append("render_pages")

            logger.info("[Step 4/6] Creating embeddings...")
            embedded_count = await create_embeddings(document_id, page_results)
            logger.info(f"  Created {embedded_count} embeddings")
            steps_completed.append("create_embeddings")

            if embedded_count == 0:
                logger.error("FAILED: No embeddings were created!")
                return {
                    "metric_name": metric_name,
                    "value": None,
                    "error": "Failed to create embeddings - check Cohere API key and logs",
                    "steps_completed": steps_completed
                }

            # Clean up images after embedding
            if image_dir:
                cleanup_images(image_dir)
                image_dir = None
        else:
            logger.info("[Step 3/6] Skipping render - embeddings exist")
            logger.info("[Step 4/6] Skipping embedding - embeddings exist")

        # Step 5: Search for relevant pages
        logger.info("[Step 5/6] Searching for relevant pages...")
        search_results = await search_pages(document_id, metric_name, SEARCH_TOP_K)
        steps_completed.append("search_pages")

        if not search_results:
            logger.error("FAILED: No relevant pages found in search!")
            return {
                "metric_name": metric_name,
                "value": None,
                "error": "No relevant pages found",
                "steps_completed": steps_completed
            }

        page_numbers = sorted([r["page_num"] for r in search_results])
        logger.info(f"  Found {len(page_numbers)} relevant pages: {page_numbers[:10]}{'...' if len(page_numbers) > 10 else ''}")

        # Step 5: Create subset PDF
        logger.info("[Step 6/6] Creating subset PDF and extracting with Gemini...")
        pdf_bytes, page_mapping = create_subset_pdf(pdf_path, page_numbers)
        logger.info(f"  Created subset PDF with {len(page_mapping)} pages ({len(pdf_bytes)} bytes)")
        steps_completed.append("create_subset")

        # Step 6: Extract metric with Gemini
        result = extract_with_gemini(pdf_bytes, metric_name, page_mapping)
        steps_completed.append("extract_metric")

        # Add metadata
        result["source_pages"] = page_numbers
        result["steps_completed"] = steps_completed
        result["document_id"] = document_id

        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETE - Result:")
        logger.info(f"  metric_name: {result.get('metric_name')}")
        logger.info(f"  value: {result.get('value')}")
        logger.info(f"  unit: {result.get('unit')}")
        logger.info(f"  source_page: {result.get('source_page')}")
        if result.get("error"):
            logger.error(f"  error: {result.get('error')}")
        logger.info("=" * 60)

        return result

    except Exception as exc:
        # Clean up on error
        logger.exception(f"PIPELINE EXCEPTION: {exc}")
        if image_dir:
            cleanup_images(image_dir)
        return {
            "metric_name": metric_name,
            "value": None,
            "error": str(exc),
            "steps_completed": steps_completed
        }


# =============================================================================
# MCP Tool Definition
# =============================================================================

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="extract_metric",
            description="Extract a financial metric from a PDF. Handles the entire pipeline: embeddings, semantic search, and Gemini extraction - all in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or path to the PDF file"
                    },
                    "document_id": {
                        "type": "string",
                        "description": "Unique identifier for the document (used for embedding cache)"
                    },
                    "metric_name": {
                        "type": "string",
                        "description": "The financial metric to extract (e.g., 'revenue from operations', 'total assets')"
                    }
                },
                "required": ["pdf_url", "document_id", "metric_name"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    started_at = time.time()

    # Don't publish huge args, just the essentials
    safe_args = {
        "pdf_url": arguments.get("pdf_url"),
        "document_id": arguments.get("document_id"),
        "metric_name": arguments.get("metric_name")
    }
    _publish_tool_call(name, safe_args)

    if name == "extract_metric":
        pdf_url = arguments["pdf_url"]
        document_id = arguments["document_id"]
        metric_name = arguments["metric_name"]

        result = await extract_metric_pipeline(pdf_url, document_id, metric_name)

        duration_ms = int((time.time() - started_at) * 1000)
        _publish_tool_result(name, result, duration_ms)
        return [TextContent(type="text", text=json.dumps(result))]

    else:
        result = {"error": f"Unknown tool: {name}"}
        duration_ms = int((time.time() - started_at) * 1000)
        _publish_tool_result(name, result, duration_ms)
        return [TextContent(type="text", text=json.dumps(result))]


async def main():
    logger.info("=" * 60)
    logger.info("METRIC EXTRACTOR MCP SERVER STARTING")
    logger.info(f"  Qdrant URL: {QDRANT_URL}")
    logger.info(f"  Cohere API Key: {'configured' if COHERE_API_KEY else 'NOT SET'}")
    logger.info(f"  Google API Key: {'configured' if GOOGLE_API_KEY else 'NOT SET'}")
    logger.info(f"  Collection: {IMAGE_COLLECTION_NAME}")
    logger.info("=" * 60)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
