# Agentic Finance Knowledge Graph

A multi-agent system for extracting, storing, and querying financial metrics from company filings using a knowledge graph.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   Frontend                                       │
│                              (Next.js :3000)                                     │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │ REST + SSE
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   Backend                                        │
│                              (FastAPI :8000)                                     │
└───────────────┬─────────────────────┴─────────────────────┬─────────────────────┘
                │                                           │
                ▼                                           ▼
┌───────────────────────────┐                 ┌───────────────────────────┐
│          Redis            │                 │        ArangoDB           │
│      (Queue + Pub/Sub)    │                 │     (Graph Database)      │
│          :6379            │                 │          :8529            │
└─────────────┬─────────────┘                 └───────────────────────────┘
              │                                             ▲
              ▼                                             │
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   Worker                                         │
│                        (OpenCode Multi-Agent Runner)                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                           Agent Orchestration                            │    │
│  │  ┌──────────┐  ┌─────────────────┐  ┌──────────┐  ┌──────────────────┐  │    │
│  │  │  Router  │─▶│ Knowledge Graph │  │ Citation │  │ Metric Extractor │  │    │
│  │  └──────────┘  └─────────────────┘  └──────────┘  └──────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                      │                                           │
│                      ┌───────────────┴───────────────┐                          │
│                      ▼                               ▼                          │
│            ┌──────────────────┐            ┌──────────────────┐                 │
│            │                  │            │  MCP Servers     │                 │
│            │   (Vector DB)    │            │  (PDF, Excel,    │                 │
│            │      :6333       │            │   Citations)     │                 │
│            └──────────────────┘            └──────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Setup

### 1. Environment Variables

Create a `.env` file:

```env
ARANGO_ROOT_PASSWORD=password
GOOGLE_API_KEY=your_google_api_key
COHERE_API_KEY=your_cohere_api_key
AZURE_DI_ENDPOINT=your_azure_document_intelligence_endpoint
AZURE_DI_KEY=your_azure_document_intelligence_key
```

### 2. Run

```bash
docker compose up
```

| Service   | URL                    |
|-----------|------------------------|
| Frontend  | http://localhost:3000  |
| Backend   | http://localhost:8000  |
| ArangoDB  | http://localhost:8529  |
| Qdrant    | http://localhost:6333  |
