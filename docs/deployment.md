# Deployment Guide

## Prerequisites

- Docker installed locally (for testing)
- Railway account at [railway.app](https://railway.app)
- API keys for Telegram, Groq, and Tavily

## Local Development

### 1. Environment setup
```bash
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run with Docker
```bash
docker build -t research-agent .
docker run --env-file .env --name research-bot research-agent
```

### 3. Run without Docker
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

### 4. View logs
```bash
tail -f logs/agent.log
tail -f logs/errors.log
```

## Railway Deployment

### Via GitHub (Recommended)

1. Push your code to GitHub
2. Visit [railway.app](https://railway.app)
3. Click **New Project** → **Deploy from GitHub repo**
4. Select your repository
5. Railway detects `Dockerfile` automatically

**Set environment variables** in Railway dashboard:
```
TELEGRAM_TOKEN = <your_token>
GROQ_API_KEY   = <your_key>
TAVILY_API_KEY = <your_key>
```

6. Click **Deploy** — bot is live in ~2 minutes

### Via Railway CLI

```bash
# Install CLI
npm install -g @railway/cli

# Login
railway login

# Create project
railway init

# Set variables
railway variables set TELEGRAM_TOKEN=xxx
railway variables set GROQ_API_KEY=xxx
railway variables set TAVILY_API_KEY=xxx

# Deploy
railway up
```

### Persistent Storage on Railway

By default Railway has ephemeral storage. To persist the database and logs:

1. Go to your Railway project → **Add Volume**
2. Mount at `/app/database` (for SQLite + FAISS)
3. Optionally mount `/app/logs`

Without a volume, the knowledge base resets on redeploy. The bot still works — it just starts fresh.

## Production Checklist

- [ ] All three API keys are set in Railway environment variables
- [ ] Railway volume mounted at `/app/database` for persistence
- [ ] Bot is responding to `/help` in Telegram
- [ ] `logs/errors.log` is empty after startup

## Troubleshooting

### Bot doesn't respond
- Check Railway logs for startup errors
- Verify `TELEGRAM_TOKEN` is correct
- Ensure the bot is not running in another instance (polling conflict)

### Research fails with API errors
- Check `GROQ_API_KEY` — Groq has rate limits on free tier
- Check `TAVILY_API_KEY` — Tavily free tier: 1000 searches/month

### PDF analysis fails
- Confirm PDF is text-based (not scanned images)
- Confirm file is under 20 MB
- Check `logs/errors.log` for PyMuPDF errors

### FAISS errors on restart
- Delete `database/faiss.index` and `database/faiss.index.meta`
- The index rebuilds from SQLite on next startup (re-add embeddings manually or re-research)
