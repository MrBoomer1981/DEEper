"""
Central configuration module.
"""
import os
from dataclasses import dataclass, field
from typing import Dict
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ResearchMode:
    name: str
    label: str
    description: str
    search_queries: int
    max_pages: int
    max_chunks_per_page: int


RESEARCH_MODES: Dict[str, ResearchMode] = {
    "simple": ResearchMode(
        name="simple",
        label="🟢 Простой",
        description="5 запросов, 10 страниц — ~3 минуты",
        search_queries=5,
        max_pages=10,
        max_chunks_per_page=3,
    ),
    "deep": ResearchMode(
        name="deep",
        label="🔵 Углублённый",
        description="15 запросов, 30 страниц — ~5 минут",
        search_queries=15,
        max_pages=30,
        max_chunks_per_page=5,
    ),
    "study": ResearchMode(
        name="study",
        label="🟣 Изучение",
        description="20 запросов, 50 страниц — ~7 минут",
        search_queries=20,
        max_pages=50,
        max_chunks_per_page=7,
    ),
}


@dataclass
class Config:
    telegram_token: str
    groq_api_key: str
    tavily_api_key: str

    primary_model: str = "llama-3.3-70b-versatile"
    fast_model: str = "llama-3.1-8b-instant"

    pages_per_query: int = 2
    max_researches: int = 20
    chunk_size: int = 800
    chunk_overlap: int = 150
    max_pdf_size_mb: int = 20

    db_path: str = "database/db.sqlite"
    faiss_index_path: str = "database/faiss.index"
    logs_dir: str = "logs"

    scrape_timeout: int = 15
    scrape_retries: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not token:
            raise ValueError("TELEGRAM_TOKEN is not set")
        if not groq_key:
            raise ValueError("GROQ_API_KEY is not set")
        if not tavily_key:
            raise ValueError("TAVILY_API_KEY is not set")
        return cls(
            telegram_token=token,
            groq_api_key=groq_key,
            tavily_api_key=tavily_key,
            max_researches=int(os.getenv("MAX_RESEARCHES", 20)),
            max_pdf_size_mb=int(os.getenv("MAX_PDF_SIZE_MB", 20)),
            db_path=os.getenv("DB_PATH", "database/db.sqlite"),
            faiss_index_path=os.getenv("FAISS_INDEX_PATH", "database/faiss.index"),
        )


config = Config.from_env()
