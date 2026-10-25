# --- Stage 1: frontend build (React + Vite) ---
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend + compiled UI ---
FROM python:3.11-slim AS runtime
WORKDIR /app

# git is required at runtime: gitpython calls the real binary to clone and read repositories.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
RUN pip install --no-cache-dir -e .

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

CMD ["beacon", "serve", "--host", "0.0.0.0"]
