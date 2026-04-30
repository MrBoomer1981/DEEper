# Architecture Overview

## System Design

The Telegram Research Agent follows a layered, modular architecture:

```
┌─────────────────────────────────────┐
│           Telegram Client            │
└─────────────────┬───────────────────┘
                  │ aiogram polling
┌─────────────────▼───────────────────┐
│           Bot Layer (main.py)        │
│   ┌─────────┐  ┌──────────────────┐  │
│   │ Handlers│  │ Service Injection│  │
│   └────┬────┘  └──────────────────┘  │
└────────┼────────────────────────────┘
         │
┌────────▼────────────────────────────┐
│          Services Layer              │
│                                      │
│  ResearchAgent ──► Planner           │
│       │       ──► WebSearch          │
│       │       ──► WebScraper         │
│       │       ──► KnowledgeBase      │
│       │                              │
│  PDFProcessor                        │
│  EmbeddingService ──► FAISS          │
│  CacheManager     ──► SQLite         │
└─────────────────────────────────────┘
         │
┌────────▼────────────────────────────┐
│       External APIs                  │
│  Groq API  │  Tavily API             │
└─────────────────────────────────────┘
```

## Component Responsibilities

### Bot Layer
- `main.py`: Initialises all services, registers routers, starts polling
- `config.py`: Typed configuration loaded from environment variables
- Handlers: Parse user messages, call services, format responses

### Services Layer
- `ResearchAgent`: Orchestrates the full research pipeline
- `ResearchPlanner`: Generates search queries via LLM
- `WebSearch`: Queries Tavily API for URLs and snippets
- `WebScraper`: Async scraping with retry, timeout, cache integration
- `CacheManager`: SQLite-backed URL content cache
- `EmbeddingService`: nomic-embed-text embeddings + FAISS index management
- `KnowledgeBase`: SQLite CRUD for research entries + semantic search
- `PDFProcessor`: PyMuPDF extraction, chunking, LLM summarization

### Data Flow
1. User sends `/research <topic>` to Telegram
2. `research_handler` calls `ResearchAgent.research(topic)`
3. `ResearchPlanner` generates 15 search queries
4. `WebSearch` queries Tavily → returns up to 30 URLs
5. `WebScraper` fetches pages (with cache checks)
6. Text is chunked into 800-token pieces
7. `llama-3.1-8b` analyzes chunks → extracts findings
8. `llama-3.3-70b` synthesizes findings → structured report
9. `nomic-embed-text` embeds the summary → stored in FAISS
10. SQLite stores the full report, summary, and sources
11. Report is sent back to Telegram in chunks (4000 char limit)

## Async Architecture
All I/O operations are async:
- `aiohttp` for web scraping
- `asyncio.Semaphore` for concurrency control
- `loop.run_in_executor` for synchronous library calls (Groq, FAISS)
- No blocking calls on the event loop

## Persistence
```
database/
├── db.sqlite        # Research entries + URL cache
├── faiss.index      # FAISS vector index
└── faiss.index.meta # FAISS ID mapping
```

## Concurrency Limits
| Operation | Concurrency |
|---|---|
| Web scraping | 5 simultaneous pages |
| Tavily searches | 3 simultaneous queries |
| Chunk analysis | 4 simultaneous LLM calls |
