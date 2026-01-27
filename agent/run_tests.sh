#!/usr/bin/env bash
# Test Runner Script for AI Software Development Crew
# Provides convenient commands for running different test suites

set -e

# Set PYTHONPATH to include src directory
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check for .env file
if [ ! -f .env ]; then
    print_warning ".env file not found. Some tests may fail without API keys."
fi

# Parse command
COMMAND=${1:-all}

case $COMMAND in
    "unit")
        print_header "Running Unit Tests"
        pytest tests/unit/ -m unit -v
        ;;
    
    "integration")
        print_header "Running Integration Tests"
        pytest tests/integration/ -m integration -v
        ;;
    
    "e2e")
        print_header "Running E2E Tests"
        if [ -z "$OPENROUTER_API_KEY" ] && [ -z "$OPENAI_API_KEY" ]; then
            print_error "E2E tests require OPENROUTER_API_KEY or OPENAI_API_KEY"
            print_warning "Set in .env file or export environment variable"
            exit 1
        fi
        pytest tests/e2e/ -m e2e -v
        ;;
    
    "e2e-fast")
        print_header "Running Fast E2E Tests (excluding slow)"
        pytest tests/e2e/ -m "e2e and not slow" -v
        ;;
    
    "api")
        print_header "Running API Tests"
        pytest tests/e2e/ -m api -v
        ;;
    
    "ui")
        print_header "Running UI Tests"
        if ! command -v playwright &> /dev/null; then
            print_error "Playwright not installed"
            print_warning "Run: playwright install chromium"
            exit 1
        fi
        pytest tests/e2e/ -m ui -v
        ;;
    
    "all")
        print_header "Running All Tests (except E2E and UI)"
        pytest tests/ -m "not e2e and not ui" -v
        ;;
    
    "all-with-e2e")
        print_header "Running ALL Tests (including E2E)"
        if [ -z "$OPENROUTER_API_KEY" ] && [ -z "$OPENAI_API_KEY" ]; then
            print_error "E2E tests require API keys"
            exit 1
        fi
        pytest tests/ -v
        ;;
    
    "coverage")
        print_header "Running Tests with Coverage"
        pytest tests/ -m "not e2e and not ui" --cov=src --cov-report=html --cov-report=term
        print_success "Coverage report generated in htmlcov/index.html"
        ;;
    
    "quick")
        print_header "Running Quick Tests Only"
        pytest tests/unit/ tests/integration/ -v --tb=short
        ;;
    
    "calculator")
        print_header "Running Calculator E2E Test"
        pytest tests/e2e/test_calculator_complete.py -v -s
        ;;
    
    "workflow")
        print_header "Running Workflow E2E Tests"
        pytest tests/e2e/test_workflow_e2e.py -v -s
        ;;
    
    "help"|"-h"|"--help")
        echo "Usage: ./run_tests.sh [command]"
        echo ""
        echo "Commands:"
        echo "  unit           - Run unit tests (fast)"
        echo "  integration    - Run integration tests"
        echo "  e2e            - Run all E2E tests (slow, requires API keys)"
        echo "  e2e-fast       - Run fast E2E tests only"
        echo "  api            - Run API tests"
        echo "  ui             - Run UI tests (requires Playwright)"
        echo "  all            - Run all tests except E2E and UI"
        echo "  all-with-e2e   - Run ALL tests including E2E"
        echo "  coverage       - Run tests with coverage report"
        echo "  quick          - Run quick tests (unit + integration)"
        echo "  calculator     - Run calculator E2E test only"
        echo "  workflow       - Run workflow E2E tests"
        echo "  help           - Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./run_tests.sh unit"
        echo "  ./run_tests.sh e2e"
        echo "  ./run_tests.sh coverage"
        ;;
    
    *)
        print_error "Unknown command: $COMMAND"
        echo "Run './run_tests.sh help' for usage"
        exit 1
        ;;
esac

# Print summary
if [ $? -eq 0 ]; then
    print_success "Tests completed successfully!"
else
    print_error "Tests failed!"
    exit 1
fi
