"""
Central configuration module.
Loads environment variables and provides typed settings.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Telegram
    telegram_token: str

    # API Keys
    groq_api_key: str
    tavily_api_key: str

    # LLM Models
    primary_model: str = "llama-3.3-70b-versatile"
    fast_model: str = "llama-3.1-8b-instant"
    embed_model: str = "nomic-embed-text"

    # Research limits
    search_queries_count: int = 15
    pages_per_query: int = 2
    max_pages: int = 30
    max_researches: int = 20

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 150

    # PDF
    max_pdf_size_mb: int = 20

    # Paths
    db_path: str = "database/db.sqlite"
    faiss_index_path: str = "database/faiss.index"
    logs_dir: str = "logs"

    # Scraping
    scrape_timeout: int = 15
    scrape_retries: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        tavily_key = os.getenv("TAVILY_API_KEY")

        if not token:
            raise ValueError("TELEGRAM_TOKEN is not set in environment")
        if not groq_key:
            raise ValueError("GROQ_API_KEY is not set in environment")
        if not tavily_key:
            raise ValueError("TAVILY_API_KEY is not set in environment")

        return cls(
            telegram_token=token,
            groq_api_key=groq_key,
            tavily_api_key=tavily_key,
            max_pages=int(os.getenv("MAX_PAGES", 30)),
            chunk_size=int(os.getenv("CHUNK_SIZE", 800)),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", 150)),
            max_researches=int(os.getenv("MAX_RESEARCHES", 20)),
            max_pdf_size_mb=int(os.getenv("MAX_PDF_SIZE_MB", 20)),
            db_path=os.getenv("DB_PATH", "database/db.sqlite"),
            faiss_index_path=os.getenv("FAISS_INDEX_PATH", "database/faiss.index"),
        )


# Singleton instance
config = Config.from_env()
