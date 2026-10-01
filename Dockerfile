FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Default command runs the API; the worker service overrides this in compose.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"]
