# API Reference

## External APIs Used

### Groq API
Used for LLM inference and embeddings.

**Models:**
| Model | Use Case | Speed |
|---|---|---|
| `llama-3.3-70b-versatile` | Research plans, final reports, PDF summaries | Slower, higher quality |
| `llama-3.1-8b-instant` | Chunk analysis, short summaries | Fast, lower cost |
| `nomic-embed-text` | Text embeddings for semantic search | Fast |

**Endpoints used:**
- `POST /v1/chat/completions` — Text generation
- `POST /v1/embeddings` — Vector embeddings

**Rate limits (free tier):**
- 30 requests/minute for chat
- 14,400 requests/day

### Tavily Search API
Used for web search.

**Configuration:**
- `search_depth`: `advanced`
- `max_results`: 2 per query (configurable)
- `include_answer`: True (AI-generated answer included)

**Free tier:** 1,000 searches/month

### Telegram Bot API
Used via aiogram 3.

**Update method:** Long polling (no webhook)
**File download:** Used for PDF uploads (max 20 MB via Bot API)

---

## Internal Service API

### ResearchAgent

```python
await research_agent.research(
    topic: str,
    progress_callback: Optional[Callable[[str], Coroutine]] = None
) -> dict
```

Returns:
```python
{
    "research_id": int,
    "title": str,
    "report": str,       # Full markdown report
    "sources": List[str],
    "summary": str,      # 2-3 sentence summary
}
```

### KnowledgeBase

```python
# Save
research_id = await kb.save_research(title, summary, report, sources)

# Get one
research = kb.get_research(research_id)  # Research | None

# List all
researches = kb.list_researches()  # List[Research]

# Delete
success = await kb.delete_research(research_id)  # bool

# Semantic search
results = await kb.semantic_search(query, top_k=5)
# List[Tuple[Research, float]]  — (research, distance)
```

### PDFProcessor

```python
result = await pdf_processor.process(
    pdf_bytes: bytes,
    filename: str = "document.pdf"
) -> dict
```

Returns:
```python
{
    "filename": str,
    "page_count": int,
    "char_count": int,
    "summary": str,    # Structured markdown summary
}
```

Raises `ValueError` on: oversized file, empty PDF, unreadable PDF.

### EmbeddingService

```python
# Add to index
await embeddings.add_research(research_id: int, text: str)

# Search
results = await embeddings.search(query: str, top_k: int = 5)
# List[Tuple[int, float]]  — (research_id, distance)

# Remove
embeddings.remove_research(research_id: int)
```

### CacheManager

```python
content = cache.get(url: str)   # str | None
cache.set(url: str, content: str)
deleted = cache.clear_old(max_age_days: int = 7)  # int
total = cache.size()  # int
```
