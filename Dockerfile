FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required by PyMuPDF, lxml, and FAISS
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libmupdf-dev \
    libxml2-dev \
    libxslt-dev \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Create required directories
RUN mkdir -p logs database

# Set Python path so imports resolve correctly
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check — just verify the module is importable
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import bot.config" || exit 1

# Run the bot
CMD ["python", "-m", "bot.main"]
