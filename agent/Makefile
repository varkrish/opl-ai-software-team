.PHONY: help install install-dev install-test install-docs clean test test-unit test-integration test-e2e test-api test-ui test-quick test-all test-coverage lint format docs docs-serve docs-deploy run-web docker-build docker-run

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Python and pytest paths
PYTHON := python3
PYTEST := PYTHONPATH=src pytest
PIP := pip3

# Project paths
SRC_DIR := src
TESTS_DIR := tests
DOCS_DIR := docs
BUILD_DIR := build
DIST_DIR := dist

help: ## Show this help message
	@echo "$(BLUE)AI Software Development Crew - Makefile Commands$(NC)"
	@echo ""
	@echo "$(GREEN)Installation:$(NC)"
	@grep -E '^install.*:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Testing:$(NC)"
	@grep -E '^test.*:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Development:$(NC)"
	@grep -E '^(lint|format|clean|run|docker).*:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Documentation:$(NC)"
	@grep -E '^docs.*:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

# ============================================================================
# Installation
# ============================================================================

install: ## Install production dependencies
	@echo "$(BLUE)Installing production dependencies...$(NC)"
	$(PIP) install -e .
	@echo "$(GREEN)✓ Installation complete$(NC)"

install-dev: ## Install development dependencies
	@echo "$(BLUE)Installing development dependencies...$(NC)"
	$(PIP) install -e ".[test]"
	@echo "$(GREEN)✓ Development installation complete$(NC)"

install-test: ## Install test dependencies only
	@echo "$(BLUE)Installing test dependencies...$(NC)"
	$(PIP) install pytest pytest-cov pytest-asyncio pytest-mock pytest-timeout httpx faker
	@echo "$(GREEN)✓ Test dependencies installed$(NC)"

install-docs: ## Install documentation dependencies
	@echo "$(BLUE)Installing documentation dependencies...$(NC)"
	$(PIP) install mkdocs mkdocs-material mkdocstrings[python] mkdocs-mermaid2-plugin
	@echo "$(GREEN)✓ Documentation dependencies installed$(NC)"

install-playwright: ## Install Playwright for UI tests
	@echo "$(BLUE)Installing Playwright...$(NC)"
	$(PIP) install playwright
	playwright install chromium
	@echo "$(GREEN)✓ Playwright installed$(NC)"

# ============================================================================
# Testing
# ============================================================================

test: test-quick ## Run default tests (quick tests)

test-unit: ## Run unit tests only (fast, < 1s each)
	@echo "$(BLUE)Running unit tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/unit/ -m unit -v
	@echo "$(GREEN)✓ Unit tests complete$(NC)"

test-integration: ## Run integration tests (< 30s)
	@echo "$(BLUE)Running integration tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/integration/ -m integration -v
	@echo "$(GREEN)✓ Integration tests complete$(NC)"

test-e2e: ## Run all E2E tests (slow, requires API keys)
	@echo "$(BLUE)Running E2E tests...$(NC)"
	@if [ -z "$$OPENROUTER_API_KEY" ] && [ -z "$$OPENAI_API_KEY" ]; then \
		echo "$(RED)Error: E2E tests require OPENROUTER_API_KEY or OPENAI_API_KEY$(NC)"; \
		exit 1; \
	fi
	$(PYTEST) $(TESTS_DIR)/e2e/ -m e2e -v
	@echo "$(GREEN)✓ E2E tests complete$(NC)"

test-e2e-fast: ## Run fast E2E tests only (skip slow tests)
	@echo "$(BLUE)Running fast E2E tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/e2e/ -m "e2e and not slow" -v
	@echo "$(GREEN)✓ Fast E2E tests complete$(NC)"

test-api: ## Run API tests (FREE, < 30s)
	@echo "$(BLUE)Running API tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/e2e/test_web_api_e2e.py -m api -v
	@echo "$(GREEN)✓ API tests complete$(NC)"

test-ui: ## Run UI tests with Playwright (FREE, requires Playwright)
	@echo "$(BLUE)Running UI tests...$(NC)"
	@command -v playwright >/dev/null 2>&1 || { \
		echo "$(RED)Error: Playwright not installed. Run 'make install-playwright'$(NC)"; \
		exit 1; \
	}
	$(PYTEST) $(TESTS_DIR)/e2e/test_web_ui_playwright.py -m ui -v
	@echo "$(GREEN)✓ UI tests complete$(NC)"

test-quick: ## Run quick tests (unit + integration, < 1 min)
	@echo "$(BLUE)Running quick tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/unit/ $(TESTS_DIR)/integration/ -v --tb=short
	@echo "$(GREEN)✓ Quick tests complete$(NC)"

test-all: ## Run all tests except E2E and UI
	@echo "$(BLUE)Running all tests (except E2E and UI)...$(NC)"
	$(PYTEST) $(TESTS_DIR)/ -m "not e2e and not ui" -v
	@echo "$(GREEN)✓ All tests complete$(NC)"

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	$(PYTEST) $(TESTS_DIR)/ -m "not e2e and not ui" \
		--cov=$(SRC_DIR) \
		--cov-report=html \
		--cov-report=term-missing \
		--cov-report=xml
	@echo "$(GREEN)✓ Coverage report generated in htmlcov/index.html$(NC)"

test-calculator: ## Run calculator E2E test example
	@echo "$(BLUE)Running calculator E2E test...$(NC)"
	$(PYTEST) $(TESTS_DIR)/e2e/test_calculator_complete.py -v -s
	@echo "$(GREEN)✓ Calculator test complete$(NC)"

test-workflow: ## Run workflow E2E tests
	@echo "$(BLUE)Running workflow E2E tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/e2e/test_workflow_e2e.py -v -s
	@echo "$(GREEN)✓ Workflow tests complete$(NC)"

# ============================================================================
# Development
# ============================================================================

lint: ## Run linters (ruff, mypy)
	@echo "$(BLUE)Running linters...$(NC)"
	@command -v ruff >/dev/null 2>&1 || { \
		echo "$(YELLOW)Installing ruff...$(NC)"; \
		$(PIP) install ruff; \
	}
	ruff check $(SRC_DIR) || true
	@echo "$(GREEN)✓ Linting complete$(NC)"

format: ## Format code with ruff
	@echo "$(BLUE)Formatting code...$(NC)"
	@command -v ruff >/dev/null 2>&1 || { \
		echo "$(YELLOW)Installing ruff...$(NC)"; \
		$(PIP) install ruff; \
	}
	ruff format $(SRC_DIR)
	@echo "$(GREEN)✓ Code formatted$(NC)"

clean: ## Clean build artifacts and caches
	@echo "$(BLUE)Cleaning build artifacts...$(NC)"
	rm -rf $(BUILD_DIR) $(DIST_DIR) *.egg-info
	rm -rf .pytest_cache .coverage htmlcov .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.egg" -delete
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

# ============================================================================
# Running the Application
# ============================================================================

run-web: ## Start Flask web UI
	@echo "$(BLUE)Starting Flask web UI on http://localhost:8080$(NC)"
	PYTHONPATH=$(SRC_DIR) $(PYTHON) -m llamaindex_crew.web.web_app

run-workflow: ## Run workflow from command line (requires vision argument)
	@echo "$(BLUE)Running workflow...$(NC)"
	@if [ -z "$(VISION)" ]; then \
		echo "$(RED)Error: VISION argument required$(NC)"; \
		echo "Usage: make run-workflow VISION='Create a simple calculator'"; \
		exit 1; \
	fi
	PYTHONPATH=$(SRC_DIR) $(PYTHON) -m llamaindex_crew.main "$(VISION)"

# ============================================================================
# Docker
# ============================================================================

docker-build: ## Build Docker image
	@echo "$(BLUE)Building Docker image...$(NC)"
	docker build -t crew-ai-software:latest .
	@echo "$(GREEN)✓ Docker image built$(NC)"

docker-run: ## Run Docker container (web UI)
	@echo "$(BLUE)Starting Docker container...$(NC)"
	docker run -d -p 8080:8080 \
		--name crew-ai-web \
		-e OPENROUTER_API_KEY=$${OPENROUTER_API_KEY} \
		crew-ai-software:latest
	@echo "$(GREEN)✓ Docker container running on http://localhost:8080$(NC)"

docker-stop: ## Stop Docker container
	@echo "$(BLUE)Stopping Docker container...$(NC)"
	docker stop crew-ai-web || true
	docker rm crew-ai-web || true
	@echo "$(GREEN)✓ Docker container stopped$(NC)"

docker-clean: ## Remove Docker images and containers
	@echo "$(BLUE)Cleaning Docker images...$(NC)"
	docker stop crew-ai-web || true
	docker rm crew-ai-web || true
	docker rmi crew-ai-software:latest || true
	@echo "$(GREEN)✓ Docker cleanup complete$(NC)"

# ============================================================================
# Documentation
# ============================================================================

docs: ## Build documentation with MkDocs
	@echo "$(BLUE)Building documentation...$(NC)"
	@command -v mkdocs >/dev/null 2>&1 || { \
		echo "$(YELLOW)Installing MkDocs...$(NC)"; \
		make install-docs; \
	}
	mkdocs build
	@echo "$(GREEN)✓ Documentation built in site/$(NC)"

docs-serve: ## Serve documentation locally
	@echo "$(BLUE)Starting documentation server...$(NC)"
	@command -v mkdocs >/dev/null 2>&1 || { \
		echo "$(YELLOW)Installing MkDocs...$(NC)"; \
		make install-docs; \
	}
	@echo "$(GREEN)Documentation available at http://127.0.0.1:8000$(NC)"
	mkdocs serve

docs-deploy: ## Deploy documentation to GitHub Pages
	@echo "$(BLUE)Deploying documentation to GitHub Pages...$(NC)"
	@command -v mkdocs >/dev/null 2>&1 || { \
		echo "$(YELLOW)Installing MkDocs...$(NC)"; \
		make install-docs; \
	}
	mkdocs gh-deploy --force
	@echo "$(GREEN)✓ Documentation deployed to GitHub Pages$(NC)"

# ============================================================================
# CI/CD Helpers
# ============================================================================

ci-install: ## Install dependencies for CI/CD
	@echo "$(BLUE)Installing CI dependencies...$(NC)"
	$(PIP) install -e ".[test]"
	@echo "$(GREEN)✓ CI dependencies installed$(NC)"

ci-test: ## Run tests for CI/CD (quick tests + coverage)
	@echo "$(BLUE)Running CI tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/ -m "not e2e and not ui" \
		--cov=$(SRC_DIR) \
		--cov-report=xml \
		--cov-report=term
	@echo "$(GREEN)✓ CI tests complete$(NC)"

ci-test-e2e: ## Run E2E tests for CI/CD (requires API keys)
	@echo "$(BLUE)Running CI E2E tests...$(NC)"
	$(PYTEST) $(TESTS_DIR)/e2e/ -m "e2e and not slow" -v --timeout=600
	@echo "$(GREEN)✓ CI E2E tests complete$(NC)"

# ============================================================================
# Package Building
# ============================================================================

build: clean ## Build Python package
	@echo "$(BLUE)Building package...$(NC)"
	$(PYTHON) -m build
	@echo "$(GREEN)✓ Package built in $(DIST_DIR)/$(NC)"

publish-test: build ## Publish to Test PyPI
	@echo "$(BLUE)Publishing to Test PyPI...$(NC)"
	$(PYTHON) -m twine upload --repository testpypi $(DIST_DIR)/*
	@echo "$(GREEN)✓ Published to Test PyPI$(NC)"

publish: build ## Publish to PyPI
	@echo "$(BLUE)Publishing to PyPI...$(NC)"
	$(PYTHON) -m twine upload $(DIST_DIR)/*
	@echo "$(GREEN)✓ Published to PyPI$(NC)"

# ============================================================================
# Utility
# ============================================================================

version: ## Show version information
	@echo "$(BLUE)Version Information:$(NC)"
	@echo "Python: $$($(PYTHON) --version)"
	@echo "Pytest: $$($(PYTEST) --version | head -1)"
	@echo "Pip: $$($(PIP) --version)"

check-env: ## Check environment setup
	@echo "$(BLUE)Checking environment...$(NC)"
	@echo "Python: $$(which $(PYTHON))"
	@echo "PYTHONPATH: $${PYTHONPATH:-not set}"
	@echo "OPENROUTER_API_KEY: $${OPENROUTER_API_KEY:+set}$${OPENROUTER_API_KEY:-not set}"
	@echo "OPENAI_API_KEY: $${OPENAI_API_KEY:+set}$${OPENAI_API_KEY:-not set}"
	@echo "$(GREEN)✓ Environment check complete$(NC)"

tree: ## Show project tree structure
	@echo "$(BLUE)Project Structure:$(NC)"
	tree -L 3 -I '__pycache__|*.pyc|.git|.pytest_cache|htmlcov|.ruff_cache|site|build|dist|*.egg-info' || \
		echo "Install 'tree' command for better visualization"
