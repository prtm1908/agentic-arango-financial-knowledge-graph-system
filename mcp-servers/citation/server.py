#!/usr/bin/env python3
"""
Citation MCP Server

Provides tools for:
- Analyzing PDF pages with Azure Document Intelligence
- Finding value coordinates (bounding boxes) in tables/paragraphs
- Rendering 300 DPI citation images with highlighted regions
"""

import os
import re
import json
import hashlib
import uuid
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import fitz  # PyMuPDF
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None

# Azure Document Intelligence imports
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentContentFormat
from azure.core.credentials import AzureKeyCredential

# Configuration
AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT", "")
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY", "")
FILINGS_PATH = os.getenv("FILINGS_PATH", "/data/filings")
DOWNLOAD_PATH = os.getenv("PDF_DOWNLOAD_PATH", os.path.join(FILINGS_PATH, "downloads"))
CITATION_OUTPUT_PATH = os.getenv("CITATION_OUTPUT_PATH", "/output/citations")
REDIS_URL = os.getenv("REDIS_URL", "")
OPENCODE_JOB_ID = os.getenv("OPENCODE_JOB_ID", "")
OPENCODE_AGENT_NAME = os.getenv("OPENCODE_AGENT_NAME", "")

# Create directories
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
os.makedirs(CITATION_OUTPUT_PATH, exist_ok=True)

# Create MCP server
server = Server("citation")
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


def get_number_variants(val_str: str) -> set:
    """Return normalized numeric variants for matching."""
    variants = {val_str}

    stripped = val_str.strip()
    if stripped in ('-', '\u2013', '\u2014', '\u2010'):
        variants.update({'-', '\u2013', '\u2014', '\u2010'})
        return variants

    cleaned = val_str.strip().lstrip('+')
    negative = cleaned.startswith('-')
    if negative:
        cleaned = cleaned[1:]

    cleaned_plain = cleaned.replace(',', '')

    if not re.match(r"^\d+(?:\.\d+)?$", cleaned_plain):
        return variants

    if negative:
        variants.add(f"-{cleaned_plain}")
        variants.add(f"({cleaned_plain})")
    else:
        variants.add(cleaned_plain)

    return variants


def _to_dict(obj) -> dict:
    """Convert Azure DI result object to dict."""
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="analyze_page_with_di",
            description="Analyze a PDF page with Azure Document Intelligence to extract tables, figures, and text with bounding boxes",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "Page number to analyze (1-indexed)"
                    }
                },
                "required": ["pdf_url", "page_number"]
            }
        ),
        Tool(
            name="find_value_coordinates",
            description="Find the bounding box coordinates for a specific value in the DI analysis result",
            inputSchema={
                "type": "object",
                "properties": {
                    "di_result": {
                        "type": "object",
                        "description": "The Document Intelligence analysis result"
                    },
                    "value": {
                        "type": "string",
                        "description": "The value to find (e.g., '1,234.56')"
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "The page number (1-indexed)"
                    }
                },
                "required": ["di_result", "value", "page_number"]
            }
        ),
        Tool(
            name="render_citation_image",
            description="Render a 300 DPI PNG image of a PDF page with a highlighted bounding box",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "Page number to render (1-indexed)"
                    },
                    "coordinates": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Bounding box coordinates [x0, y0, x1, y1] in PDF points"
                    },
                    "output_folder": {
                        "type": "string",
                        "description": "Folder name for output (will be created under citation output path)"
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Output filename (without extension)"
                    }
                },
                "required": ["pdf_url", "page_number", "coordinates", "output_folder"]
            }
        ),
        Tool(
            name="generate_citation",
            description="Complete citation workflow: analyze page, find value, and render highlighted image",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_url": {
                        "type": "string",
                        "description": "URL or local path to the PDF file"
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "Page number where the metric was found (1-indexed)"
                    },
                    "value": {
                        "type": "string",
                        "description": "The metric value to highlight"
                    },
                    "metric_name": {
                        "type": "string",
                        "description": "Name of the metric (for output naming)"
                    },
                    "company": {
                        "type": "string",
                        "description": "Company name (for output folder naming)"
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period (for output folder naming)"
                    }
                },
                "required": ["pdf_url", "page_number", "value", "metric_name"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    started_at = time.time()
    _publish_tool_call(name, arguments)

    if name == "analyze_page_with_di":
        if not AZURE_DI_ENDPOINT or not AZURE_DI_KEY:
            payload = {"error": "Azure DI credentials not configured"}
            return _tool_response(name, payload, started_at)

        pdf_url = arguments["pdf_url"]
        page_number = arguments["page_number"]

        try:
            # For local files, we need to read and send bytes
            pdf_path = resolve_pdf_path(pdf_url)

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            client = DocumentIntelligenceClient(
                endpoint=AZURE_DI_ENDPOINT,
                credential=AzureKeyCredential(AZURE_DI_KEY)
            )

            poller = client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                output_content_format=DocumentContentFormat.MARKDOWN,
                pages=str(page_number)
            )
            result = poller.result()

            # Extract relevant data
            tables = []
            for table in (result.tables or []):
                table_data = _to_dict(table)
                tables.append(table_data)

            figures = []
            for figure in (result.figures or []):
                figure_data = _to_dict(figure)
                figures.append(figure_data)

            paragraphs = []
            for para in (result.paragraphs or []):
                para_data = _to_dict(para)
                paragraphs.append(para_data)

            pages_meta = []
            for page in (result.pages or []):
                page_data = {
                    "pageNumber": page.page_number,
                    "angle": page.angle or 0,
                    "width": page.width,
                    "height": page.height,
                    "unit": page.unit
                }
                pages_meta.append(page_data)

            words = []
            for page in (result.pages or []):
                for word in (page.words or []):
                    word_data = _to_dict(word)
                    word_data["pageNumber"] = page.page_number
                    words.append(word_data)

            payload = {
                "success": True,
                "page_number": page_number,
                "tables": tables,
                "figures": figures,
                "paragraphs": paragraphs,
                "pages": pages_meta,
                "words": words
            }
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "find_value_coordinates":
        di_result = arguments["di_result"]
        value = arguments["value"]
        page_number = arguments["page_number"]

        try:
            val_variants = get_number_variants(str(value).strip())
            tables = di_result.get("tables", [])
            figures = di_result.get("figures", [])
            paragraphs = di_result.get("paragraphs", [])
            pages_meta = di_result.get("pages", [])
            words = di_result.get("words", [])

            # Get page angle for coordinate adjustment
            angle = 0
            for pm in pages_meta:
                if pm.get("pageNumber") == page_number:
                    angle = pm.get("angle", 0)
                    break

            # Search in tables first
            for t_idx, table in enumerate(tables):
                for cell in table.get("cells", []):
                    txt = str(cell.get("content", "")).strip()
                    txt_normalized = txt.replace(',', '')

                    if txt_normalized in val_variants or txt in val_variants:
                        regions = cell.get("boundingRegions", cell.get("bounding_regions", []))
                        if regions:
                            reg = regions[0]
                            pg = reg.get("pageNumber") or reg.get("page_number")
                            poly = reg.get("polygon", [])

                            if pg == page_number and len(poly) >= 8:
                                # Convert polygon to rectangle coordinates
                                if -100 <= angle <= -80:
                                    norm = [poly[0], poly[5], poly[4], poly[1]]
                                elif 80 <= angle <= 100:
                                    norm = [poly[2], poly[3], poly[6], poly[7]]
                                else:
                                    norm = [poly[0], poly[1], poly[4], poly[5]]

                                # Convert to PDF points (multiply by 72)
                                coords_pts = [c * 72 for c in norm]

                                payload = {
                                    "success": True,
                                    "found": True,
                                    "type": "table",
                                    "table_index": t_idx,
                                    "page_number": page_number,
                                    "coordinates": coords_pts,
                                    "angle": angle,
                                    "matched_text": txt
                                }
                                return _tool_response(name, payload, started_at)

            # Search in paragraphs
            for p_idx, para in enumerate(paragraphs):
                txt = str(para.get("content", "")).strip()
                txt_normalized = txt.replace(',', '')

                for variant in val_variants:
                    if variant in txt_normalized or variant in txt:
                        regions = para.get("boundingRegions", para.get("bounding_regions", []))
                        if regions:
                            reg = regions[0]
                            pg = reg.get("pageNumber") or reg.get("page_number")
                            poly = reg.get("polygon", [])

                            if pg == page_number and len(poly) >= 8:
                                if -100 <= angle <= -80:
                                    norm = [poly[0], poly[5], poly[4], poly[1]]
                                elif 80 <= angle <= 100:
                                    norm = [poly[2], poly[3], poly[6], poly[7]]
                                else:
                                    norm = [poly[0], poly[1], poly[4], poly[5]]

                                coords_pts = [c * 72 for c in norm]

                                payload = {
                                    "success": True,
                                    "found": True,
                                    "type": "paragraph",
                                    "paragraph_index": p_idx,
                                    "page_number": page_number,
                                    "coordinates": coords_pts,
                                    "angle": angle,
                                    "matched_text": txt[:100]
                                }
                                return _tool_response(name, payload, started_at)

            # Search in words as fallback
            for word in words:
                if word.get("pageNumber") != page_number:
                    continue
                txt = str(word.get("content", "")).strip()
                txt_normalized = txt.replace(',', '')

                if txt_normalized in val_variants or txt in val_variants:
                    poly = word.get("polygon", [])
                    if len(poly) >= 8:
                        if -100 <= angle <= -80:
                            norm = [poly[0], poly[5], poly[4], poly[1]]
                        elif 80 <= angle <= 100:
                            norm = [poly[2], poly[3], poly[6], poly[7]]
                        else:
                            norm = [poly[0], poly[1], poly[4], poly[5]]

                        coords_pts = [c * 72 for c in norm]

                        payload = {
                            "success": True,
                            "found": True,
                            "type": "word",
                            "page_number": page_number,
                            "coordinates": coords_pts,
                            "angle": angle,
                            "matched_text": txt
                        }
                        return _tool_response(name, payload, started_at)

            payload = {
                "success": True,
                "found": False,
                "message": f"Could not find value '{value}' on page {page_number}"
            }
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "render_citation_image":
        pdf_url = arguments["pdf_url"]
        page_number = arguments["page_number"]
        coordinates = arguments["coordinates"]
        output_folder = arguments["output_folder"]
        output_filename = arguments.get("output_filename", f"page_{page_number}_citation")

        try:
            pdf_path = resolve_pdf_path(pdf_url)
            doc = fitz.open(pdf_path)

            if page_number < 1 or page_number > len(doc):
                doc.close()
                payload = {"error": f"Page {page_number} out of range (1-{len(doc)})"}
                return _tool_response(name, payload, started_at)

            page = doc[page_number - 1]

            # Draw blue semi-transparent rectangle
            if len(coordinates) >= 4:
                x0, y0, x1, y1 = coordinates[:4]
                pad = 4
                rect = fitz.Rect(
                    max(0, x0 - pad),
                    max(0, y0 - pad),
                    x1 + pad,
                    y1 + pad
                )
                page.draw_rect(
                    rect,
                    color=(0, 0, 1),      # Blue
                    fill=(0, 0, 1),       # Blue fill
                    width=1,
                    stroke_opacity=1,
                    fill_opacity=0.15     # 15% transparent
                )

            # Render at 300 DPI
            scale = 300 / 72
            zoom_mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(alpha=False, matrix=zoom_mat)

            # Create output directory
            output_dir = os.path.join(CITATION_OUTPUT_PATH, output_folder)
            os.makedirs(output_dir, exist_ok=True)

            # Save PNG
            output_path = os.path.join(output_dir, f"{output_filename}.png")
            pix.save(output_path)

            doc.close()

            payload = {
                "success": True,
                "image_path": output_path,
                "width": pix.width,
                "height": pix.height,
                "dpi": 300
            }
            return _tool_response(name, payload, started_at)

        except Exception as e:
            payload = {"error": str(e)}
            return _tool_response(name, payload, started_at)

    elif name == "generate_citation":
        pdf_url = arguments["pdf_url"]
        page_number = arguments["page_number"]
        value = arguments["value"]
        metric_name = arguments["metric_name"]
        company = arguments.get("company", "unknown")
        period = arguments.get("period", "unknown")

        if not AZURE_DI_ENDPOINT or not AZURE_DI_KEY:
            payload = {"error": "Azure DI credentials not configured"}
            return _tool_response(name, payload, started_at)

        try:
            # Step 1: Analyze page with DI
            pdf_path = resolve_pdf_path(pdf_url)

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            client = DocumentIntelligenceClient(
                endpoint=AZURE_DI_ENDPOINT,
                credential=AzureKeyCredential(AZURE_DI_KEY)
            )

            poller = client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                output_content_format=DocumentContentFormat.MARKDOWN,
                pages=str(page_number)
            )
            result = poller.result()

            # Build DI result dict
            di_result = {
                "tables": [_to_dict(t) for t in (result.tables or [])],
                "figures": [_to_dict(f) for f in (result.figures or [])],
                "paragraphs": [_to_dict(p) for p in (result.paragraphs or [])],
                "pages": [{"pageNumber": p.page_number, "angle": p.angle or 0} for p in (result.pages or [])],
                "words": []
            }
            for page in (result.pages or []):
                for word in (page.words or []):
                    w = _to_dict(word)
                    w["pageNumber"] = page.page_number
                    di_result["words"].append(w)

            # Step 2: Find value coordinates
            val_variants = get_number_variants(str(value).strip())
            angle = 0
            for pm in di_result["pages"]:
                if pm.get("pageNumber") == page_number:
                    angle = pm.get("angle", 0)
                    break

            coordinates = None
            matched_type = None

            # Search tables
            for table in di_result["tables"]:
                if coordinates:
                    break
                for cell in table.get("cells", []):
                    txt = str(cell.get("content", "")).strip().replace(',', '')
                    if txt in val_variants or cell.get("content", "").strip() in val_variants:
                        regions = cell.get("boundingRegions", [])
                        if regions:
                            poly = regions[0].get("polygon", [])
                            if len(poly) >= 8:
                                if -100 <= angle <= -80:
                                    norm = [poly[0], poly[5], poly[4], poly[1]]
                                elif 80 <= angle <= 100:
                                    norm = [poly[2], poly[3], poly[6], poly[7]]
                                else:
                                    norm = [poly[0], poly[1], poly[4], poly[5]]
                                coordinates = [c * 72 for c in norm]
                                matched_type = "table"
                                break

            # Search paragraphs if not found
            if not coordinates:
                for para in di_result["paragraphs"]:
                    txt = str(para.get("content", "")).replace(',', '')
                    for v in val_variants:
                        if v in txt:
                            regions = para.get("boundingRegions", [])
                            if regions:
                                poly = regions[0].get("polygon", [])
                                if len(poly) >= 8:
                                    if -100 <= angle <= -80:
                                        norm = [poly[0], poly[5], poly[4], poly[1]]
                                    elif 80 <= angle <= 100:
                                        norm = [poly[2], poly[3], poly[6], poly[7]]
                                    else:
                                        norm = [poly[0], poly[1], poly[4], poly[5]]
                                    coordinates = [c * 72 for c in norm]
                                    matched_type = "paragraph"
                                    break
                    if coordinates:
                        break

            # Search words if not found
            if not coordinates:
                for word in di_result["words"]:
                    if word.get("pageNumber") != page_number:
                        continue
                    txt = str(word.get("content", "")).strip().replace(',', '')
                    if txt in val_variants or word.get("content", "").strip() in val_variants:
                        poly = word.get("polygon", [])
                        if len(poly) >= 8:
                            if -100 <= angle <= -80:
                                norm = [poly[0], poly[5], poly[4], poly[1]]
                            elif 80 <= angle <= 100:
                                norm = [poly[2], poly[3], poly[6], poly[7]]
                            else:
                                norm = [poly[0], poly[1], poly[4], poly[5]]
                            coordinates = [c * 72 for c in norm]
                            matched_type = "word"
                            break

            if not coordinates:
                payload = {
                    "success": False,
                    "error": f"Could not find value '{value}' on page {page_number}"
                }
                return _tool_response(name, payload, started_at)

            # Step 3: Render citation image
            doc = fitz.open(pdf_path)
            page = doc[page_number - 1]

            x0, y0, x1, y1 = coordinates[:4]
            pad = 4
            rect = fitz.Rect(
                max(0, x0 - pad),
                max(0, y0 - pad),
                x1 + pad,
                y1 + pad
            )
            page.draw_rect(
                rect,
                color=(0, 0, 1),
                fill=(0, 0, 1),
                width=1,
                stroke_opacity=1,
                fill_opacity=0.15
            )

            # Render at 300 DPI
            scale = 300 / 72
            zoom_mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(alpha=False, matrix=zoom_mat)

            # Create output folder
            safe_company = "".join(c if c.isalnum() else "_" for c in company)
            safe_period = "".join(c if c.isalnum() else "_" for c in period)
            safe_metric = "".join(c if c.isalnum() else "_" for c in metric_name)
            output_folder = f"{safe_company}_{safe_period}_{safe_metric}"
            output_dir = os.path.join(CITATION_OUTPUT_PATH, output_folder)
            os.makedirs(output_dir, exist_ok=True)

            output_filename = f"page_{page_number}_citation.png"
            output_path = os.path.join(output_dir, output_filename)
            pix.save(output_path)

            doc.close()

            payload = {
                "success": True,
                "image_path": output_path,
                "width": pix.width,
                "height": pix.height,
                "dpi": 300,
                "coordinates": coordinates,
                "matched_type": matched_type,
                "page_number": page_number,
                "metric_name": metric_name,
                "value": value
            }
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
