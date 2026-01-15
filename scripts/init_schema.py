import os
from arango import ArangoClient

ARANGO_URL = os.getenv("ARANGO_URL", "http://localhost:8529")
ARANGO_DB = os.getenv("ARANGO_DB", "financial_kg")
ARANGO_USERNAME = os.getenv("ARANGO_USERNAME", "root")
ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "")

DOCUMENT_COLLECTIONS = [
    "companies",
    "filings",
    "metrics",
    "documents"
]

EDGE_COLLECTIONS = [
    "company_has_filing",
    "filing_has_metric",
    "subsidiary",
    "competitor"
]


def get_db():
    client = ArangoClient(hosts=ARANGO_URL)
    sys_db = client.db("_system", username=ARANGO_USERNAME, password=ARANGO_PASSWORD)

    if not sys_db.has_database(ARANGO_DB):
        sys_db.create_database(ARANGO_DB)

    return client.db(ARANGO_DB, username=ARANGO_USERNAME, password=ARANGO_PASSWORD)


def ensure_collection(db, name: str, edge: bool = False):
    if not db.has_collection(name):
        db.create_collection(name, edge=edge)


def ensure_indexes(db):
    companies = db.collection("companies")
    if companies and not companies.has_index("persistent", fields=["name"]):
        companies.add_persistent_index(fields=["name"], unique=False)
    if companies and not companies.has_index("persistent", fields=["nse_symbol"]):
        companies.add_persistent_index(fields=["nse_symbol"], unique=False)

    filings = db.collection("filings")
    if filings and not filings.has_index("persistent", fields=["nse_symbol"]):
        filings.add_persistent_index(fields=["nse_symbol"], unique=False)
    if filings and not filings.has_index("persistent", fields=["period", "type"]):
        filings.add_persistent_index(fields=["period", "type"], unique=False)


def main():
    db = get_db()

    for collection in DOCUMENT_COLLECTIONS:
        ensure_collection(db, collection, edge=False)

    for collection in EDGE_COLLECTIONS:
        ensure_collection(db, collection, edge=True)

    ensure_indexes(db)
    print("Schema initialized")


if __name__ == "__main__":
    main()
