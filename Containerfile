FROM python:3.11-slim

# Build arguments for metadata
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION
ARG IMAGE_NAME

# Labels for metadata
LABEL org.opencontainers.image.title="AI Software Development Crew"
LABEL org.opencontainers.image.description="Automated Software Development with AI Agents"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.source="https://github.com/${IMAGE_NAME}"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="AI Software Dev Crew"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    build-essential \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/
COPY crew_studio/ ./crew_studio/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Create workspace directory
RUN mkdir -p /app/workspace

# Set environment variables
ENV PYTHONPATH=/app
ENV WORKSPACE_PATH=/app/workspace
ENV FLASK_APP=crew_studio.web_app
ENV FLASK_ENV=production

# Support for config file mounting
# Option 1: Docker secrets (recommended)
#   docker secret create crew_config config.yaml
#   docker service create --secret crew_config ...
# Option 2: Volume mount (also secure)
#   docker run -v /secure/path/config.yaml:/app/config.yaml:ro \
#              -e CONFIG_FILE_PATH=/app/config.yaml ...
ENV CONFIG_FILE_PATH=/run/secrets/crew_config

# Expose web UI port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/health/ready || exit 1

# Default command: run web UI
CMD ["python", "-m", "ai_software_dev_crew.main", "web", "--port", "8080"]

