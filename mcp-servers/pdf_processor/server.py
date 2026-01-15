#!/usr/bin/env python3
"""
PDF Processor MCP Server

Provides tools for:
- Extracting text from PDF pages
- Converting PDF pages to images
- Getting PDF metadata
- Creating PDFs from specific pages
Supports local paths or HTTP(S) URLs for PDF inputs.
"""

import os
import json
import base64
import hashlib
import uuid
import shutil
import concurrent.futures
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from io import BytesIO
from typing import Any

import fitz  # PyMuPDF
from PIL import Image
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None

# Configuration
FILINGS_PATH = os.getenv("FILINGS_PATH", "/data/filings")
DOWNLOAD_PATH = os.getenv("PDF_DOWNLOAD_PATH", os.path.join(FILINGS_PATH, "downloads"))
PAGE_IMAGE_ROOT = os.getenv("PDF_PAGE_IMAGE_ROOT", os.path.join(FILINGS_PATH, "page_images"))
TEMP_PATH = os.getenv("TEMP_PATH", "/tmp/pdf_processor")
RENDER_WORKERS = int(
    os.getenv("PDF_RENDER_WORKERS", max(2, min(16, (os.cpu_count() or 1) * 2)))
)
REDIS_URL = os.getenv("REDIS_URL", "")
OPENCODE_JOB_ID = os.getenv("OPENCODE_JOB_ID", "")
OPENCODE_AGENT_NAME = os.getenv("OPENCODE_AGENT_NAME", "")

# Create temp directory
os.makedirs(TEMP_PATH, exist_ok=True)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
os.makedirs(PAGE_IMAGE_ROOT, exist_ok=True)

# Create MCP server
server = Server("pdf_processor")
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


def resolve_pdf_path(pdf_url: str) -> str:
    """Resolve a PDF URL or path to a local file path."""
    if not pdf_url:
        raise ValueError("pdf_url is required")

    parsed = urlparse(pdf_url)
    scheme = parsed.scheme.lower()

    if scheme in ("http", "https"):
        url_hash = hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()[:16]
        local_path = os.path.join(DOWNLOAD_PATH, f"{url_hash}.pdf")

        # Reuse cached download when possible.
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


def _safe_within_root(path: str, root: str) -> bool:
    root_path = os.path.realpath(root)
    target_path = os.path.realpath(path)
    return target_path == root_path or target_path.startswith(root_path + os.sep)


def _create_page_image_dir() -> str:
    folder = os.path.join(PAGE_IMAGE_ROOT, str(uuid.uuid4()))
    os.makedirs(folder, exist_ok=True)
    return folder


def _render_page_to_png(args: tuple[str, int, int, str]) -> dict[str, Any]:
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


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_pdf_info",
            description="Get metadata and page count of a PDF",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    }
                },
                "required": ["pdf_url"]
            }
        ),
        Tool(
            name="pages_to_images",
            description="Convert specific PDF pages to images (returns base64)",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    },
                    "pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of page numbers (1-indexed)"
                    },
                    "dpi": {
                        "type": "integer",
                        "description": "Resolution in DPI",
                        "default": 150
                    }
                },
                "required": ["pdf_url", "pages"]
            }
        ),
        Tool(
            name="render_all_pages",
            description="Render all PDF pages to 200 DPI images and store in a UUID folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    },
                    "dpi": {
                        "type": "integer",
                        "description": "Resolution in DPI",
                        "default": 200
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Optional output directory (defaults to a UUID folder)"
                    },
                    "include_base64": {
                        "type": "boolean",
                        "description": "Include base64-encoded PNGs in response",
                        "default": False
                    }
                },
                "required": ["pdf_url"]
            }
        ),
        Tool(
            name="create_subset_pdf",
            description="Create a new PDF with only specific pages",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the source PDF file"
                    },
                    "pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of page numbers (1-indexed)"
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Name for the output PDF"
                    },
                    "include_base64": {
                        "type": "boolean",
                        "description": "Include base64-encoded PDF in response",
                        "default": False
                    }
                },
                "required": ["pdf_url", "pages", "output_name"]
            }
        ),
        Tool(
            name="cleanup_page_images",
            description="Delete a rendered page image folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_path": {
                        "type": "string",
                        "description": "Path to the UUID folder to delete"
                    }
                },
                "required": ["folder_path"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    started_at = time.time()
    _publish_tool_call(name, arguments)

    if name == "get_pdf_info":
        pdf_url = arguments.get("pdf_url") or arguments.get("pdf_path")

        try:
            pdf_path = resolve_pdf_path(pdf_url)
            doc = fitz.open(pdf_path)
            info = {
                "path": pdf_path,
                "page_count": len(doc),
                "metadata": doc.metadata,
                "is_encrypted": doc.is_encrypted
            }
            doc.close()

            return _tool_response(name, info, started_at)

        except Exception as e:
            payload = {"error": str(e), "url": pdf_url}
            return _tool_response(name, payload, started_at)

    elif name == "pages_to_images":
        pdf_url = arguments.get("pdf_url") or arguments.get("pdf_path")
        pages = arguments["pages"]
        dpi = arguments.get("dpi", 150)

        try:
            pdf_path = resolve_pdf_path(pdf_url)
            doc = fitz.open(pdf_path)
            result = []
            zoom = dpi / 72  # 72 is the default DPI

            for page_num in pages:
                if 1 <= page_num <= len(doc):
                    page = doc[page_num - 1]
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)

                    # Convert to PIL Image and then to base64
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    buffer = BytesIO()
                    img.save(buffer, format="PNG")
                    b64 = base64.b64encode(buffer.getvalue()).decode()

                    result.append({
                        "page_num": page_num,
                        "width": pix.width,
                        "height": pix.height,
                        "image_base64": b64
                    })
                else:
                    result.append({
                        "page_num": page_num,
                        "error": f"Page {page_num} out of range"
                    })

            doc.close()

            payload = {"images": result}
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "render_all_pages":
        pdf_url = arguments.get("pdf_url") or arguments.get("pdf_path")
        dpi = int(arguments.get("dpi", 200))
        include_base64 = bool(arguments.get("include_base64", False))
        output_dir = arguments.get("output_dir") or _create_page_image_dir()

        if not _safe_within_root(output_dir, PAGE_IMAGE_ROOT):
            payload = {"error": "Output directory must be within the page image root"}
            return _tool_response(name, payload, started_at)

        os.makedirs(output_dir, exist_ok=True)

        try:
            pdf_path = resolve_pdf_path(pdf_url)
            doc = fitz.open(pdf_path)
            page_count = len(doc)
            doc.close()

            page_args = [(pdf_path, page_num, dpi, output_dir) for page_num in range(1, page_count + 1)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=RENDER_WORKERS) as executor:
                results = list(executor.map(_render_page_to_png, page_args))

            if include_base64:
                for item in results:
                    image_path = item.get("image_path")
                    if image_path and os.path.exists(image_path):
                        with open(image_path, "rb") as handle:
                            item["image_base64"] = base64.b64encode(handle.read()).decode()

            payload = {
                "success": True,
                "pdf_url": pdf_url,
                "page_count": page_count,
                "dpi": dpi,
                "output_dir": output_dir,
                "pages": results
            }
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "create_subset_pdf":
        pdf_url = arguments.get("pdf_url") or arguments.get("pdf_path")
        pages = sorted(set(arguments["pages"]))
        output_name = arguments["output_name"]
        include_base64 = bool(arguments.get("include_base64", False))

        try:
            pdf_path = resolve_pdf_path(pdf_url)
            doc = fitz.open(pdf_path)
            new_doc = fitz.open()

            # Build page mapping: subset_page (1-indexed) -> original_page (1-indexed)
            page_mapping = {}
            subset_page_num = 1
            for page_num in pages:
                if 1 <= page_num <= len(doc):
                    new_doc.insert_pdf(doc, from_page=page_num-1, to_page=page_num-1)
                    page_mapping[str(subset_page_num)] = page_num
                    subset_page_num += 1

            output_path = os.path.join(TEMP_PATH, output_name)
            new_doc.save(output_path)
            new_doc.close()
            doc.close()

            output_base64 = None
            if include_base64 and os.path.exists(output_path):
                with open(output_path, "rb") as handle:
                    output_base64 = base64.b64encode(handle.read()).decode()

            payload = {
                "success": True,
                "output_path": output_path,
                "page_count": len(page_mapping),
                "pdf_base64": output_base64,
                "page_mapping": page_mapping
            }
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "cleanup_page_images":
        folder_path = arguments["folder_path"]

        if not _safe_within_root(folder_path, PAGE_IMAGE_ROOT):
            payload = {"error": "Folder path must be within the page image root"}
            return _tool_response(name, payload, started_at)

        try:
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
            payload = {"success": True, "folder_path": folder_path}
            return _tool_response(name, payload, started_at)
        except Exception as e:
            payload = {"error": str(e)}
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
