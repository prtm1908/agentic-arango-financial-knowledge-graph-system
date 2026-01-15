import os
from arango import ArangoClient

ARANGO_URL = os.getenv("ARANGO_URL", "http://localhost:8529")
ARANGO_DB = os.getenv("ARANGO_DB", "financial_kg")
ARANGO_USERNAME = os.getenv("ARANGO_USERNAME", "root")
ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "")


COMPANIES = [
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

FILINGS = [
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

EDGES = [
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
    client = ArangoClient(hosts=ARANGO_URL)
    return client.db(ARANGO_DB, username=ARANGO_USERNAME, password=ARANGO_PASSWORD)


def ensure_document(collection, document):
    if not collection.has(document["_key"]):
        collection.insert(document)


def ensure_edge(collection, edge):
    if not collection.has(edge["_key"]):
        collection.insert(edge)


def main():
    db = get_db()

    companies = db.collection("companies")
    filings = db.collection("filings")
    edges = db.collection("company_has_filing")

    for company in COMPANIES:
        ensure_document(companies, company)

    for filing in FILINGS:
        ensure_document(filings, filing)

    for edge in EDGES:
        ensure_edge(edges, edge)

    print("Seed data inserted")


if __name__ == "__main__":
    main()
