# Dockerfile

FROM python:3.11-slim

WORKDIR /app

# Install libpq-dev for psycopg build if needed, plus deps for PDF parsing
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m nltk.downloader punkt punkt_tab

COPY ./frontend ./frontend
COPY ./app ./app
COPY worker.py .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

