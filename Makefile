.PHONY: setup agent-test studio-run studio-dev studio-build studio-test-component studio-test-e2e studio-test-open backend-test-api backend-test-e2e backend-test-e2e-quick backend-test-all refine-test refine-test-e2e reset-db compose-up compose-down compose-logs compose-clean container-build container-build-backend container-build-frontend container-run container-stop help

ROOT_DIR := $(shell pwd)

help:
	@echo "AI Software Development Crew Monorepo"
	@echo "======================================"
	@echo ""
	@echo "Setup & Development:"
	@echo "  setup           - Install agent dependencies"
	@echo "  agent-test      - Run agent framework tests"
	@echo "  studio-run      - Start backend (Flask on port 8080)"
	@echo "  studio-dev      - Start frontend dev server (Vite on port 3000)"
	@echo "  studio-build    - Production build to studio-ui/dist/"
	@echo ""
	@echo "Testing:"
	@echo "  backend-test-api       - Run backend API tests (pytest)"
	@echo "  backend-test-e2e       - Run backend E2E tests (job execution)"
	@echo "  backend-test-e2e-quick - Run quick E2E smoke test"
	@echo "  backend-test-all       - Run all backend tests"
	@echo "  refine-test            - Run refinement unit + API tests"
	@echo "  refine-test-e2e        - Run refinement E2E test (slow)"
	@echo "  reset-db               - Clear job DB and workspace"
	@echo ""
	@echo "Container Operations (Podman / Docker):"
	@echo "  compose-up      - Build & start full stack (frontend + backend)"
	@echo "  compose-down    - Stop all services"
	@echo "  compose-logs    - Follow all service logs"
	@echo "  compose-clean   - Stop & remove volumes"
	@echo "  container-build - Build all images individually"
	@echo ""
	@echo "OpenShift / Helm Deployment:"
	@echo "  helm-build-push - Build & push images to Quay.io"
	@echo "  helm-deploy     - Build, push, deploy to OCP via Helm"
	@echo "  helm-deploy-dev - Deploy with dev overlay"
	@echo "  helm-status     - Check Helm release status"
	@echo "  helm-uninstall  - Remove Helm release from cluster"
	@echo "  oc-logs         - Follow backend pod logs"
	@echo ""
	@echo "Required env vars for helm-deploy:"
	@echo "  LLM_API_KEY      - API key for the LLM provider"
	@echo "  LLM_API_BASE_URL - MaaS endpoint URL"

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

refine-test:
	@echo "Running refinement unit + API tests..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR):$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	cd agent && pytest tests/unit/test_refinement_agent.py tests/api/test_refine_endpoint.py -v

refine-test-e2e:
	@echo "Running refinement E2E test..."
	@rm -rf agent/src/llamaindex_crew/web
	@ln -sfn $(ROOT_DIR)/crew_studio agent/src/llamaindex_crew/web
	export PYTHONPATH=$(ROOT_DIR):$(ROOT_DIR)/agent:$(ROOT_DIR)/agent/src:$(PYTHONPATH) && \
	cd agent && pytest tests/e2e/test_refine_e2e.py -v -s

backend-test-all:
	@echo "Running all backend tests..."
	@make backend-test-api && make refine-test && make backend-test-e2e

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

# ── OpenShift / Helm ─────────────────────────────────────────────────────────
HELM_CHART   := deploy/helm/crew-studio
HELM_RELEASE := crew-studio
HELM_NS      := crew-studio

helm-build-push:
	@echo "Building and pushing images to Quay.io..."
	podman build -t quay.io/$(USER)/crew-backend:latest -f Containerfile.backend .
	podman push quay.io/$(USER)/crew-backend:latest
	podman build -t quay.io/$(USER)/crew-frontend:latest -f Containerfile.frontend .
	podman push quay.io/$(USER)/crew-frontend:latest

helm-deploy: helm-build-push
	@echo "Deploying to OpenShift via Helm..."
	@test -n "$(LLM_API_KEY)" || (echo "ERROR: LLM_API_KEY is not set" && exit 1)
	@test -n "$(LLM_API_BASE_URL)" || (echo "ERROR: LLM_API_BASE_URL is not set" && exit 1)
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NS) --create-namespace \
		--set backend.image.repository=quay.io/$(USER)/crew-backend \
		--set frontend.image.repository=quay.io/$(USER)/crew-frontend \
		--set llm.apiKey=$(LLM_API_KEY) \
		--set llm.apiBaseUrl=$(LLM_API_BASE_URL)
	@echo "✅ Deployed to OpenShift"

helm-deploy-dev: helm-build-push
	@echo "Deploying (dev overlay) to OpenShift via Helm..."
	@test -n "$(LLM_API_KEY)" || (echo "ERROR: LLM_API_KEY is not set" && exit 1)
	@test -n "$(LLM_API_BASE_URL)" || (echo "ERROR: LLM_API_BASE_URL is not set" && exit 1)
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		--namespace $(HELM_NS) --create-namespace \
		-f $(HELM_CHART)/values-dev.yaml \
		--set backend.image.repository=quay.io/$(USER)/crew-backend \
		--set frontend.image.repository=quay.io/$(USER)/crew-frontend \
		--set llm.apiKey=$(LLM_API_KEY) \
		--set llm.apiBaseUrl=$(LLM_API_BASE_URL)
	@echo "✅ Deployed to OpenShift (dev)"

helm-status:
	helm status $(HELM_RELEASE) --namespace $(HELM_NS)

helm-uninstall:
	helm uninstall $(HELM_RELEASE) --namespace $(HELM_NS)

oc-logs:
	@echo "Viewing backend logs..."
	oc logs -f deployment/$(HELM_RELEASE)-backend -n $(HELM_NS)
