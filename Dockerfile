# ReqManTool Server Edition - Multi-arch Docker Image
# Build: docker buildx build --platform linux/amd64,linux/arm64 -t reqmantool/server:latest .
# Run: docker run -d -p 8899:8899 -v reqman-data:/data -v ./config.json:/config.json -v ./server.env:/server.env reqmantool/server:latest

# ---- Build stage ----
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy source files
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY LICENSE ./LICENSE

# Build frontend
RUN cd /build/frontend && npm ci && npm run build

# Copy built frontend to backend static directory
RUN mkdir -p /build/backend/reqman/static && \
    cp -R /build/frontend/dist/* /build/backend/reqman/static/ && \
    cp /build/LICENSE /build/backend/LICENSE && \
    echo "$(git -C /build rev-parse --short HEAD 2>/dev/null || echo dev) ($(date +%Y-%m-%d))" > /build/backend/reqman/BUILD

# Install Python package in build directory
RUN cd /build/backend && pip install --no-cache-dir --prefix=/install .

# ---- Runtime stage ----
FROM python:3.12-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} reqman && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash reqman

# Create data directory
RUN mkdir -p /data && chown reqman:reqman /data

# Copy installed package from builder
COPY --from=builder /install /usr/local

# Set working directory
WORKDIR /app

# Switch to non-root user
USER reqman

# Environment variables with defaults
ENV REQMAN_DATA_DIR=/data \
    REQMAN_HOST=0.0.0.0 \
    REQMAN_PORT=8899 \
    REQMAN_MODE=server \
    REQMAN_CONFIG_FILE=/config.json \
    PYTHONUNBUFFERED=1

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8899/edition || exit 1

# Expose port
EXPOSE 8899

# Entry point
ENTRYPOINT ["reqman", "serve"]