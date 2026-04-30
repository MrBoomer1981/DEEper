# 🤖 Telegram Research Agent

A production-ready Telegram bot that performs deep AI-powered internet research, analyzes PDFs, and maintains a searchable knowledge base — deployable to Railway with Docker.

---

## ✨ Features

| Feature | Details |
|---|---|
| **Deep Research** | 15 search queries, up to 30 pages scraped per session |
| **Structured Reports** | Title, Summary, Key Findings, Risks, Insights, Conclusion, Sources |
| **PDF Analysis** | Extract, chunk, summarize PDFs up to 20 MB |
| **Knowledge Base** | SQLite + FAISS semantic vector search |
| **Page Cache** | Avoid re-scraping the same URLs |
| **Multi-language** | Reports match the language of your query |
| **Auto-cleanup** | Oldest research auto-deleted when limit (20) is reached |
| **Full Logging** | Separate logs for agent, research, and errors |

---

## 🛠 Tech Stack

- **Python 3.11+**
- **aiogram 3** — Telegram bot framework
- **Groq API** — LLM inference (llama-3.3-70b + llama-3.1-8b + nomic-embed-text)
- **Tavily** — Web search
- **aiohttp + BeautifulSoup** — Async web scraping
- **PyMuPDF** — PDF text extraction
- **FAISS** — Vector similarity search
- **SQLite** — Persistent storage
- **loguru** — Structured logging
- **Docker + Railway** — Deployment

---

## 📁 Project Structure

```
telegram-research-agent/
├── bot/
│   ├── main.py                  # Entry point
│   ├── config.py                # Configuration & env vars
│   ├── handlers/
│   │   ├── research_handler.py  # /research command
│   │   ├── pdf_handler.py       # PDF uploads + /pdf
│   │   ├── search_handler.py    # /search command
│   │   └── history_handler.py   # /history, /view, /delete
│   ├── services/
│   │   ├── research_agent.py    # Main research pipeline
│   │   ├── planner.py           # LLM-powered query planner
│   │   ├── web_search.py        # Tavily search wrapper
│   │   ├── web_scraper.py       # Async scraper + cache
│   │   ├── pdf_processor.py     # PDF extraction + summary
│   │   ├── embeddings.py        # Embedding generation + FAISS
│   │   ├── knowledge_base.py    # SQLite knowledge store
│   │   └── cache_manager.py     # URL page cache
│   └── utils/
│       ├── logger.py            # Logging setup
│       └── text_utils.py        # Tokenization, chunking, cleaning
├── database/                    # SQLite + FAISS files (auto-created)
├── logs/                        # Log files (auto-created)
├── docs/
│   ├── architecture.md
│   ├── deployment.md
│   └── api.md
├── Dockerfile
├── railway.json
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone <your-repo-url>
cd telegram-research-agent
cp .env.example .env
```

### 2. Configure environment

Edit `.env`:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key
```

**Getting API keys:**
- **Telegram token** — Talk to [@BotFather](https://t.me/BotFather) on Telegram
- **Groq API key** — [console.groq.com](https://console.groq.com) (free tier available)
- **Tavily API key** — [tavily.com](https://tavily.com) (free tier: 1000 searches/month)

### 3. Run locally with Docker

```bash
docker build -t research-agent .
docker run --env-file .env research-agent
```

### 4. Run locally with Python

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m bot.main
```

---

## 🤖 Bot Commands

| Command | Description |
|---|---|
| `/research <topic>` | Start deep research (2-5 min) |
| `/pdf` | Instructions for PDF analysis |
| `/search <query>` | Semantic search in knowledge base |
| `/history` | List all saved research |
| `/view <id>` | View full research report |
| `/delete <id>` | Delete a research entry |
| `/help` | Show help message |

**Example usage:**
```
/research artificial intelligence in healthcare 2024
/research квантовые вычисления применения
/search machine learning
/view 3
/delete 3
```

---

## ☁️ Railway Deployment

### Method 1: Deploy via GitHub

1. Push code to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repository
4. Add environment variables in Railway dashboard:
   - `TELEGRAM_TOKEN`
   - `GROQ_API_KEY`
   - `TAVILY_API_KEY`
5. Railway auto-detects `Dockerfile` and deploys

### Method 2: Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Then set env vars:
```bash
railway variables set TELEGRAM_TOKEN=xxx
railway variables set GROQ_API_KEY=xxx
railway variables set TAVILY_API_KEY=xxx
```

> **Note:** Railway's free tier provides 5 GB of memory and 100 GB of egress per month. The bot runs continuously with polling (no webhook needed).

---

## ⚙️ Configuration

All settings can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | required | Telegram bot token |
| `GROQ_API_KEY` | required | Groq API key |
| `TAVILY_API_KEY` | required | Tavily search key |
| `MAX_PAGES` | `30` | Max pages scraped per research |
| `CHUNK_SIZE` | `800` | Tokens per text chunk |
| `CHUNK_OVERLAP` | `150` | Overlapping tokens between chunks |
| `MAX_RESEARCHES` | `20` | Max stored research entries |
| `MAX_PDF_SIZE_MB` | `20` | Max PDF file size |
| `DB_PATH` | `database/db.sqlite` | SQLite database path |
| `FAISS_INDEX_PATH` | `database/faiss.index` | FAISS index path |

---

## 📊 Research Pipeline

```
User Query
    │
    ▼
[Planner] Generate research plan + 15 search queries
    │
    ▼
[WebSearch] Tavily API → collect URLs (max 30)
    │
    ▼
[WebScraper] Async scrape pages (with cache + retry)
    │
    ▼
[TextUtils] Chunk text (800 tokens, 150 overlap)
    │
    ▼
[ResearchAgent] Analyze chunks with llama-3.1-8b
    │
    ▼
[ResearchAgent] Generate final report with llama-3.3-70b
    │
    ▼
[KnowledgeBase] Save summary + embed with nomic-embed-text
    │
    ▼
[FAISS] Index for semantic search
    │
    ▼
Structured Report → Telegram
```

---

## 📝 Report Format

Each research report contains:

1. **Title** — Concise research title
2. **Summary** — 2-3 sentence executive summary
3. **Key Findings** — 5-8 bullet points
4. **Risks and Considerations** — Limitations and caveats
5. **Scientific and Technical Insights** — Deep technical analysis
6. **Conclusion** — Final synthesis
7. **Sources** — Numbered list of URLs

Reports are generated in the **same language** as the user's query.

---

## 🪵 Logging

Log files are created in the `logs/` directory:

| File | Contents |
|---|---|
| `logs/agent.log` | General bot activity (DEBUG+) |
| `logs/research.log` | Research pipeline steps |
| `logs/errors.log` | Errors with full tracebacks (30 day retention) |

Logs rotate at 10 MB and compress to `.zip`.

---

## 🔒 Limits & Safety

- **Max 30 pages** scraped per research session
- **Max 20 researches** stored (oldest auto-deleted)
- **Max 20 MB** PDF files
- **Scraping timeout**: 15 seconds per page with 3 retries
- Non-text files (images, zip, video) are automatically skipped

---

## 📄 License

MIT License — free to use and modify.
