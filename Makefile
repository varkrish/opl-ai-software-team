.PHONY: setup agent-test studio-run container-build container-run help

ROOT_DIR := $(shell pwd)

help:
	@echo "AI Software Development Crew Monorepo"
	@echo "======================================"
	@echo ""
	@echo "Setup & Development:"
	@echo "  setup           - Install agent dependencies"
	@echo "  agent-test      - Run agent framework tests"
	@echo "  studio-run      - Run Crew Studio UI locally"
	@echo ""
	@echo "Container Operations (Podman):"
	@echo "  container-build - Build Podman container image"
	@echo "  container-run   - Run container with Web UI"
	@echo "  container-cli   - Run CLI command in container"
	@echo "  container-stop  - Stop and remove container"
	@echo ""
	@echo "OpenShift Deployment:"
	@echo "  oc-deploy       - Deploy to OpenShift"
	@echo "  oc-logs         - View OpenShift logs"
	@echo "  oc-delete       - Delete from OpenShift"

setup:
	@echo "📦 Setting up Agent Framework..."
	cd agent && pip install -e .

agent-test:
	@echo "🧪 Running Agent Tests..."
	cd agent && pytest

studio-run:
	@echo "🚀 Starting Crew Studio UI..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	export WORKSPACE_PATH=$(ROOT_DIR)/agent/workspace && \
	python3.10 -m src.llamaindex_crew.web.llamaindex_web_app

# Podman Container Operations
container-build:
	@echo "🏗️  Building Podman container image..."
	podman build -t crew-ai-software:latest -f Containerfile .

container-run:
	@echo "🚀 Starting Crew Studio in container..."
	podman run -d -p 8080:8080 --name crew-ai-studio \
		-v ~/.crew-ai:/root/.crew-ai:ro \
		crew-ai-software:latest
	@echo "✅ Crew Studio UI: http://localhost:8080"

container-cli:
	@echo "💻 Run: podman run --rm -v ~/.crew-ai:/root/.crew-ai:ro crew-ai-software:latest python -m src.llamaindex_crew.main \"Your vision\""

container-stop:
	@echo "🛑 Stopping container..."
	podman stop crew-ai-studio || true
	podman rm crew-ai-studio || true

# OpenShift Deployment
oc-deploy:
	@echo "☁️  Deploying to OpenShift..."
	@echo "Building and pushing to Quay.io..."
	podman build -t quay.io/$(USER)/crew-ai-software:latest -f Containerfile .
	podman push quay.io/$(USER)/crew-ai-software:latest
	@echo "Creating OpenShift deployment..."
	oc new-app quay.io/$(USER)/crew-ai-software:latest --name=crew-ai-studio -e CONFIG_FILE_PATH=/config/crew.config.yaml || true
	oc expose svc/crew-ai-studio || true
	@echo "✅ Deployed to OpenShift"

oc-logs:
	@echo "📋 Viewing OpenShift logs..."
	oc logs -f deployment/crew-ai-studio

oc-delete:
	@echo "🗑️  Deleting from OpenShift..."
	oc delete all -l app=crew-ai-studio
