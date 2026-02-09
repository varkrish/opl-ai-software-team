.PHONY: setup agent-test studio-run studio-dev studio-build studio-test-component studio-test-e2e studio-test-open backend-test-api backend-test-e2e backend-test-e2e-quick backend-test-all reset-db compose-up compose-down compose-logs compose-clean container-build container-build-backend container-build-frontend container-run container-stop help

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
	@echo "Studio UI (PatternFly):"
	@echo "  studio-dev            - Start Vite dev server (port 3000)"
	@echo "  studio-build          - Production build to studio-ui/dist/"
	@echo "  studio-test-component - Run Cypress component tests (headless)"
	@echo "  studio-test-e2e       - Run Cypress E2E tests (headless)"
	@echo "  studio-test-open      - Open Cypress interactive runner"
	@echo "  backend-test-api       - Run backend API tests (pytest)"
	@echo "  backend-test-e2e       - Run backend E2E tests (job execution)"
	@echo "  backend-test-e2e-quick - Run quick E2E smoke test"
	@echo "  backend-test-all       - Run all backend tests"
	@echo "  reset-db               - Clear job DB and workspace (start from scratch)"
	@echo ""
	@echo "Container Operations (Podman / Docker):"
	@echo "  compose-up      - Build & start full stack (frontend + backend)"
	@echo "  compose-down    - Stop all services"
	@echo "  compose-logs    - Follow all service logs"
	@echo "  compose-clean   - Stop & remove volumes"
	@echo "  container-build - Build all images individually"
	@echo "  container-run   - Start via compose"
	@echo "  container-stop  - Stop via compose"
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

# Studio UI (PatternFly React)
studio-dev:
	@echo "Starting Studio UI dev server..."
	cd studio-ui && npm run dev

studio-build:
	@echo "Building Studio UI for production..."
	cd studio-ui && npm run build

studio-test-component:
	@echo "Running Cypress component tests..."
	cd studio-ui && npx cypress run --component

studio-test-e2e:
	@echo "Running Cypress E2E tests..."
	cd studio-ui && npx cypress run --e2e

studio-test-open:
	@echo "Opening Cypress interactive runner..."
	cd studio-ui && npx cypress open

backend-test-api:
	@echo "Running backend API tests..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	cd agent && pytest tests/api/ -v

backend-test-e2e:
	@echo "Running backend E2E tests..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR):$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	cd agent && pytest tests/e2e/ -v -s

backend-test-e2e-quick:
	@echo "Running quick E2E smoke test..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR):$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	cd agent && pytest tests/e2e/test_job_execution.py::test_job_starts_within_timeout -v

backend-test-all:
	@echo "Running all backend tests..."
	@make backend-test-api && make backend-test-e2e

reset-db:
	@echo "Resetting DB and workspace..."
	./scripts/reset_db.sh

# Podman / Docker Compose Operations
compose-up:
	@echo "🚀 Starting full stack (backend + frontend)..."
	podman-compose up -d --build
	@echo "✅ Frontend: http://localhost:3000"
	@echo "✅ Backend:  http://localhost:8080"

compose-down:
	@echo "🛑 Stopping all services..."
	podman-compose down

compose-logs:
	@echo "📋 Following logs..."
	podman-compose logs -f

compose-clean:
	@echo "🗑️  Stopping and removing volumes..."
	podman-compose down -v

# Individual container builds
container-build-backend:
	@echo "🏗️  Building backend image..."
	podman build -t crew-backend:latest -f Containerfile.backend .

container-build-frontend:
	@echo "🏗️  Building frontend image..."
	podman build -t crew-frontend:latest -f Containerfile.frontend .

container-build: container-build-backend container-build-frontend
	@echo "✅ All images built"

container-run:
	@echo "🚀 Starting full stack..."
	podman-compose up -d
	@echo "✅ Frontend: http://localhost:3000"
	@echo "✅ Backend:  http://localhost:8080"

container-stop:
	@echo "🛑 Stopping all services..."
	podman-compose down

# OpenShift Deployment
oc-deploy:
	@echo "☁️  Deploying to OpenShift..."
	@echo "Building and pushing backend to Quay.io..."
	podman build -t quay.io/$(USER)/crew-backend:latest -f Containerfile.backend .
	podman push quay.io/$(USER)/crew-backend:latest
	@echo "Building and pushing frontend to Quay.io..."
	podman build -t quay.io/$(USER)/crew-frontend:latest -f Containerfile.frontend .
	podman push quay.io/$(USER)/crew-frontend:latest
	@echo "Creating OpenShift deployments..."
	oc new-app quay.io/$(USER)/crew-backend:latest --name=crew-backend || true
	oc new-app quay.io/$(USER)/crew-frontend:latest --name=crew-frontend || true
	oc expose svc/crew-frontend || true
	@echo "✅ Deployed to OpenShift"

oc-logs:
	@echo "📋 Viewing OpenShift logs..."
	oc logs -f deployment/crew-backend

oc-delete:
	@echo "🗑️  Deleting from OpenShift..."
	oc delete all -l app=crew-backend
	oc delete all -l app=crew-frontend
