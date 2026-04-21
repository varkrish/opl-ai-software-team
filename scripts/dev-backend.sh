#!/bin/sh
# Dev entrypoint for the backend container — starts the ASGI app with Uvicorn.

# Copy config to writable location and fix permissions
# (bind-mounts from macOS don't support chmod)
cp /app/config.yaml /tmp/config.yaml
chmod 600 /tmp/config.yaml
export CONFIG_FILE_PATH=/tmp/config.yaml

# Mount Flask fallback for routes not yet ported to FastAPI
export MOUNT_FLASK_FALLBACK=1

export PYTHONPATH="/app:/app/agent/src:/app/agent:${PYTHONPATH:-}"

exec uvicorn crew_studio.asgi_app:app \
    --host 0.0.0.0 \
    --port 8080 \
    --reload \
    --reload-dir /app/crew_studio \
    --reload-dir /app/agent/src \
    --log-level info
