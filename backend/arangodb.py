from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from arango import ArangoClient

from config import settings

# Directory for storing chat JSON files (project root/chats)
CHATS_DIR = Path(__file__).parent.parent / "chats"

_client: ArangoClient | None = None
_db = None

DOCUMENT_COLLECTIONS = [
    "companies",
    "filings",
    "metrics",
    "documents",
    "chats"
]

EDGE_COLLECTIONS = [
    "company_has_filing",
    "filing_has_metric",
    "subsidiary",
    "competitor"
]

SEED_COMPANIES = [
    {
        "_key": "reliance",
        "name": "Reliance Industries Limited",
        "nse_symbol": "RELIANCE"
    },
    {
        "_key": "tcs",
        "name": "Tata Consultancy Services",
        "nse_symbol": "TCS"
    },
    {
        "_key": "infosys",
        "name": "Infosys Limited",
        "nse_symbol": "INFY"
    },
    {
        "_key": "hdfc",
        "name": "HDFC Bank",
        "nse_symbol": "HDFCBANK"
    }
]

SEED_FILINGS = [
    {
        "_key": "reliance_fy24_annual",
        "nse_symbol": "RELIANCE",
        "type": "annual",
        "period": "FY24",
        "pdf_url": "/data/filings/reliance_fy24.pdf"
    },
    {
        "_key": "tcs_fy24_annual",
        "nse_symbol": "TCS",
        "type": "annual",
        "period": "FY24",
        "pdf_url": "/data/filings/tcs_fy24.pdf"
    },
    {
        "_key": "infosys_fy24_annual",
        "nse_symbol": "INFY",
        "type": "annual",
        "period": "FY24",
        "pdf_url": "/data/filings/infosys_fy24.pdf"
    },
    {
        "_key": "hdfc_fy24_annual",
        "nse_symbol": "HDFCBANK",
        "type": "annual",
        "period": "FY24",
        "pdf_url": "/data/filings/hdfc_fy24.pdf"
    }
]

SEED_EDGES = [
    {
        "_key": "reliance_has_reliance_fy24_annual",
        "_from": "companies/reliance",
        "_to": "filings/reliance_fy24_annual"
    },
    {
        "_key": "tcs_has_tcs_fy24_annual",
        "_from": "companies/tcs",
        "_to": "filings/tcs_fy24_annual"
    },
    {
        "_key": "infosys_has_infosys_fy24_annual",
        "_from": "companies/infosys",
        "_to": "filings/infosys_fy24_annual"
    },
    {
        "_key": "hdfc_has_hdfc_fy24_annual",
        "_from": "companies/hdfc",
        "_to": "filings/hdfc_fy24_annual"
    }
]


def get_db():
    global _client, _db
    if _db is not None:
        return _db

    _client = ArangoClient(hosts=settings.arango_url)
    sys_db = _client.db(
        "_system",
        username=settings.arango_username,
        password=settings.arango_password
    )

    if not sys_db.has_database(settings.arango_db):
        sys_db.create_database(settings.arango_db)

    _db = _client.db(
        settings.arango_db,
        username=settings.arango_username,
        password=settings.arango_password
    )
    return _db


def ensure_collection(db, name: str, edge: bool = False):
    if not db.has_collection(name):
        db.create_collection(name, edge=edge)


def _has_index(collection, fields: list[str]) -> bool:
    """Check if a persistent index with the given fields already exists."""
    existing_indexes = collection.indexes()
    for idx in existing_indexes:
        if idx.get("type") == "persistent" and idx.get("fields") == fields:
            return True
    return False


def ensure_indexes(db):
    companies = db.collection("companies")
    if companies and not _has_index(companies, ["name"]):
        companies.add_persistent_index(fields=["name"], unique=False)
    if companies and not _has_index(companies, ["nse_symbol"]):
        companies.add_persistent_index(fields=["nse_symbol"], unique=False)

    filings = db.collection("filings")
    if filings and not _has_index(filings, ["nse_symbol"]):
        filings.add_persistent_index(fields=["nse_symbol"], unique=False)
    if filings and not _has_index(filings, ["period", "type"]):
        filings.add_persistent_index(fields=["period", "type"], unique=False)


def ensure_schema():
    db = get_db()
    for collection in DOCUMENT_COLLECTIONS:
        ensure_collection(db, collection, edge=False)
    for collection in EDGE_COLLECTIONS:
        ensure_collection(db, collection, edge=True)
    ensure_indexes(db)


def _ensure_document(collection, document: dict[str, Any]):
    if not collection.has(document["_key"]):
        collection.insert(document)


def _ensure_edge(collection, edge: dict[str, Any]):
    if not collection.has(edge["_key"]):
        collection.insert(edge)


def seed_data():
    db = get_db()
    companies = db.collection("companies")
    filings = db.collection("filings")
    edges = db.collection("company_has_filing")

    for company in SEED_COMPANIES:
        _ensure_document(companies, company)

    for filing in SEED_FILINGS:
        _ensure_document(filings, filing)

    for edge in SEED_EDGES:
        _ensure_edge(edges, edge)


def list_companies() -> list[dict[str, Any]]:
    db = get_db()
    if not db.has_collection("companies"):
        return []
    return list(db.collection("companies").all())


def list_filings_for_company(company_id: str) -> list[dict[str, Any]]:
    db = get_db()
    if not (
        db.has_collection("companies")
        and db.has_collection("company_has_filing")
        and db.has_collection("filings")
    ):
        return []

    query = """
    FOR c IN companies
      FILTER c._key == @company_id
      FOR f IN 1..1 OUTBOUND c company_has_filing
        RETURN f
    """

    cursor = db.aql.execute(query, bind_vars={"company_id": company_id})
    return list(cursor)


# ============================================================================
# Chat Management Functions
# ============================================================================

def _ensure_chats_dir():
    """Ensure the chats directory exists."""
    CHATS_DIR.mkdir(parents=True, exist_ok=True)


def create_chat(title: str | None = None, initial_message: str | None = None) -> dict[str, Any]:
    """Create a new chat and return its metadata."""
    _ensure_chats_dir()
    db = get_db()

    chat_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    # Generate title from initial message if not provided
    if not title and initial_message:
        title = initial_message[:50] + ("..." if len(initial_message) > 50 else "")
    elif not title:
        title = f"Chat {chat_id[:8]}"

    json_path = str(CHATS_DIR / f"{chat_id}.json")

    # Create chat content JSON
    chat_content = {
        "chat_id": chat_id,
        "title": title,
        "created_at": now,
        "messages": [],
        "settings": {}
    }

    # Add initial message if provided
    if initial_message:
        chat_content["messages"].append({
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": initial_message,
            "timestamp": now
        })

    # Save JSON file
    with open(json_path, "w") as f:
        json.dump(chat_content, f, indent=2)

    # Create metadata in ArangoDB
    chat_metadata = {
        "_key": chat_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "json_path": json_path,
        "message_count": len(chat_content["messages"]),
        "last_message_preview": initial_message[:100] if initial_message else "",
        "agents_used": []
    }

    chats_collection = db.collection("chats")
    chats_collection.insert(chat_metadata)

    return chat_metadata


def get_chat_metadata(chat_id: str) -> dict[str, Any] | None:
    """Get chat metadata from ArangoDB."""
    db = get_db()
    if not db.has_collection("chats"):
        return None

    chats = db.collection("chats")
    if chats.has(chat_id):
        return chats.get(chat_id)
    return None


def get_chat_content(chat_id: str) -> dict[str, Any] | None:
    """Load full chat content from JSON file."""
    metadata = get_chat_metadata(chat_id)
    if not metadata:
        return None

    json_path = Path(metadata["json_path"])
    if not json_path.exists():
        return None

    with open(json_path) as f:
        return json.load(f)


def save_chat_content(chat_id: str, content: dict[str, Any]):
    """Save chat content to JSON file and update metadata."""
    _ensure_chats_dir()
    db = get_db()

    metadata = get_chat_metadata(chat_id)
    if not metadata:
        raise ValueError(f"Chat {chat_id} not found")

    json_path = Path(metadata["json_path"])

    # Save JSON file
    with open(json_path, "w") as f:
        json.dump(content, f, indent=2)

    # Update metadata
    now = datetime.utcnow().isoformat() + "Z"
    messages = content.get("messages", [])
    last_message = messages[-1] if messages else None

    # Collect agents used from all messages
    agents_used = set()
    for msg in messages:
        if msg.get("metadata", {}).get("agents_used"):
            agents_used.update(msg["metadata"]["agents_used"])

    update_data = {
        "updated_at": now,
        "message_count": len(messages),
        "last_message_preview": last_message["content"][:100] if last_message else "",
        "agents_used": list(agents_used)
    }

    # Update title if changed
    if content.get("title"):
        update_data["title"] = content["title"]

    chats = db.collection("chats")
    chats.update({"_key": chat_id, **update_data})


def add_message_to_chat(chat_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """Append a message to an existing chat."""
    content = get_chat_content(chat_id)
    if not content:
        raise ValueError(f"Chat {chat_id} not found")

    # Ensure message has required fields
    if "id" not in message:
        message["id"] = str(uuid.uuid4())
    if "timestamp" not in message:
        message["timestamp"] = datetime.utcnow().isoformat() + "Z"

    content["messages"].append(message)
    save_chat_content(chat_id, content)

    return message


def list_chats(skip: int = 0, limit: int = 20) -> list[dict[str, Any]]:
    """List chats with pagination, ordered by updated_at descending."""
    db = get_db()
    if not db.has_collection("chats"):
        return []

    query = """
    FOR chat IN chats
      SORT chat.updated_at DESC
      LIMIT @skip, @limit
      RETURN chat
    """

    cursor = db.aql.execute(query, bind_vars={"skip": skip, "limit": limit})
    return list(cursor)


def count_chats() -> int:
    """Get total number of chats."""
    db = get_db()
    if not db.has_collection("chats"):
        return 0

    query = "RETURN LENGTH(chats)"
    cursor = db.aql.execute(query)
    result = list(cursor)
    return result[0] if result else 0


def update_chat_metadata(chat_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Update chat metadata (title, etc.)."""
    db = get_db()
    if not db.has_collection("chats"):
        return None

    chats = db.collection("chats")
    if not chats.has(chat_id):
        return None

    updates["updated_at"] = datetime.utcnow().isoformat() + "Z"
    updates["_key"] = chat_id

    chats.update(updates)

    # Also update the JSON file if title changed
    if "title" in updates:
        content = get_chat_content(chat_id)
        if content:
            content["title"] = updates["title"]
            json_path = Path(get_chat_metadata(chat_id)["json_path"])
            with open(json_path, "w") as f:
                json.dump(content, f, indent=2)

    return get_chat_metadata(chat_id)


def delete_chat(chat_id: str) -> bool:
    """Delete chat from both ArangoDB and JSON file."""
    db = get_db()
    if not db.has_collection("chats"):
        return False

    metadata = get_chat_metadata(chat_id)
    if not metadata:
        return False

    # Delete JSON file
    json_path = Path(metadata["json_path"])
    if json_path.exists():
        json_path.unlink()

    # Delete from ArangoDB
    chats = db.collection("chats")
    chats.delete(chat_id)

    return True
