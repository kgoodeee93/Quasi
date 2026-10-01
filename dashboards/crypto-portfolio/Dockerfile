FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY demo_wallets.json .

# Run as non-root. wallets.json and API keys are NOT in the image; Cloud Run
# mounts them from Secret Manager at runtime (see deploy.sh).
RUN useradd --create-home app && chown -R app /app
USER app

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
