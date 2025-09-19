# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Dépendances système (poppler/tesseract si nécessaire pour OCR)
RUN apt-get update && apt-get install -y \
    build-essential poppler-utils tesseract-ocr libmagic1 imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Copie tout le repo
COPY . /app

# Upgrade pip
RUN python -m pip install --upgrade pip

# Installe requirements si présents, sinon installe le package local
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN if [ -f pyproject.toml ] || [ -f setup.py ]; then pip install --no-cache-dir .; fi

# Installer fastapi + uvicorn si pas déjà dans requirements
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
