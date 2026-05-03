# 🤖 Telegram Research Agent (DEEPer)

Production-ready Telegram bot that performs deep AI-powered internet research, analyzes PDFs, and maintains a searchable knowledge base. Deployable to Railway via Docker.

---

## ✨ Features

| Feature | Details |
|---|---|
| **Two-Stage Search** | Stage 1: broad queries → Stage 2: refined queries based on findings |
| **3 Research Modes** | Simple (~3 min), Deep (~5 min), Study (~7 min) |
| **Jina Reader** | Bypasses paywalls and bot-blockers (Britannica, Medium, Forbes, etc.) |
| **Content Deduplication** | Skips duplicate pages to save tokens and improve quality |
| **Report Verification** | Second LLM pass checks for contradictions and gaps (Deep/Study modes) |
| **Groq Key Rotation** | Auto-switches between API keys on rate limit (supports unlimited keys) |
| **Tavily AI Answers** | Uses Tavily's distilled answers as high-priority context in reports |
| **PDF Analysis** | Extract, chunk, summarize PDFs up to 20 MB |
| **Knowledge Base** | SQLite + FTS5 full-text search |
| **Page Cache** | Avoids re-scraping same URLs across sessions |
| **Always Russian** | All reports generated in Russian regardless of query language |
| **Full Logging** | Separate logs for agent, research, and errors |

---

## 🛠 Tech Stack

- **Python 3.11+**
- **aiogram 3** — Telegram bot framework
- **Groq API** — LLM inference (llama-3.3-70b + llama-3.1-8b)
- **Tavily** — Web search with AI answers
- **Jina Reader** — Paywall bypass for scraping
- **aiohttp + BeautifulSoup** — Async web scraping
- **PyMuPDF** — PDF text extraction
- **SQLite FTS5** — Full-text search (no external dependencies)
- **loguru** — Structured logging
- **Docker + Railway** — Deployment

---

## 📁 Project Structure

```
telegram-research-agent/
├── bot/
│   ├── main.py
│   ├── config.py                # Config + ResearchMode definitions
│   ├── handlers/
│   │   ├── research_handler.py  # /research + mode selection keyboard
│   │   ├── pdf_handler.py
│   │   ├── search_handler.py
│   │   └── history_handler.py
│   ├── services/
│   │   ├── research_agent.py    # Main pipeline (two-stage + verification)
│   │   ├── planner.py           # Stage 1 plan + Stage 2 query refinement
│   │   ├── groq_rotator.py      # Multi-key Groq rotation
│   │   ├── web_search.py        # Tavily search + AI answers
│   │   ├── web_scraper.py       # Async scraper + Jina + dedup
│   │   ├── pdf_processor.py
│   │   ├── knowledge_base.py    # SQLite + FTS5
│   │   ├── embeddings.py        # Stub (FTS5 handles search)
│   │   └── cache_manager.py
│   └── utils/
│       ├── logger.py
│       └── text_utils.py
├── database/
├── logs/
├── docs/
├── Dockerfile
├── railway.json
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Quick Start

```bash
git clone https://github.com/MrBoomer1981/DEEPer.git
cd DEEPer
cp .env.example .env
# Edit .env with your API keys
docker build -t deeper .
docker run --env-file .env deeper
```

---

## ⚙️ Environment Variables

```env
# Required
TELEGRAM_TOKEN=your_telegram_bot_token

# Groq keys — add as many as you have (from different accounts)
GROQ_API_KEY=primary_key
GROQ_API_KEY_2=second_key
GROQ_API_KEY_3=third_key
# GROQ_API_KEY_4=fourth_key  ← add unlimited keys

# Tavily
TAVILY_API_KEY=your_tavily_key

# Optional
MAX_RESEARCHES=20
MAX_PDF_SIZE_MB=20
DB_PATH=database/db.sqlite
```

---

## 🤖 Bot Commands

| Command | Description |
|---|---|
| `/research <topic>` | Start research — shows mode selector |
| `/pdf` | Instructions for PDF analysis |
| `/search <query>` | Full-text search in knowledge base |
| `/history` | List all saved research |
| `/view <id>` | View full research report |
| `/delete <id>` | Delete a research entry |
| `/help` | Show help |

---

## 📊 Research Pipeline

```
User Query
    │
    ▼
[Stage 1] Planner → N/3 broad queries → Tavily search → URLs + AI answers
    │
    ▼
[Stage 2] Planner refines queries based on Stage 1 findings → 2N/3 targeted queries → Tavily
    │
    ▼
[Scraper] Direct fetch → Jina Reader fallback → Deduplication → Cache
    │
    ▼
[Analyzer] Chunk text → Groq llama-3.1-8b (key rotation on 429)
    │
    ▼
[Reporter] Groq llama-3.3-70b → Structured Russian report
    │
    ▼
[Verifier] Second LLM pass → Check contradictions + fill gaps (Deep/Study only)
    │
    ▼
[KnowledgeBase] SQLite + FTS5 index → Telegram
```

---

## ⚙️ Research Modes

| Mode | Queries | Pages | Chunks/page | Verification | Time |
|---|---|---|---|---|---|
| 🟢 Simple | 5 | 10 | 3 | ❌ | ~3 min |
| 🔵 Deep | 15 | 30 | 5 | ✅ | ~5 min |
| 🟣 Study | 20 | 50 | 7 | ✅ | ~7 min |

---

## 🔑 Groq Key Rotation

With 3 keys from different accounts: 3 × 6,000 = **18,000 TPM** effectively.

The rotator switches keys instantly on 429 — no artificial delays.

```
Key 1 → 429 → Key 2 → 429 → Key 3 → ...
```

Add keys in Railway Variables:
```
GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3, GROQ_API_KEY_4 ...
```

---

## ☁️ Railway Deployment

1. Push to GitHub
2. Railway → New Project → Deploy from GitHub
3. Add environment variables
4. Auto-deploys on every `git push`

---

## 🪵 Logging

| File | Contents |
|---|---|
| `logs/agent.log` | General activity |
| `logs/research.log` | Research pipeline steps |
| `logs/errors.log` | Errors with tracebacks (30 day retention) |

---

## 📄 License

MIT
