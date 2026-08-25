# Keep in step with the dev container base interpreter (Miniforge, 3.13).
FROM python:3.13-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed ./seed

ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
