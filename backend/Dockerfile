FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Tesseract OCR + idioma português + Java para Audiveris + OpenCV deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr \
       tesseract-ocr-por \
       tesseract-ocr-eng \
       default-jre-headless \
       curl \
       unzip \
       libgl1 \
       libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Diretório de cache OCR persistente
RUN mkdir -p /app/data/ocr_cache

EXPOSE 8787

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787"]
