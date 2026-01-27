# Quick Start Guide

## Installation & First Run

### Step 1: Install Dependencies

```bash
cd ai_software_dev_crew

# Option A: Using uv (recommended - faster)
pip install uv
uv pip install -e .

# Option B: Using pip
pip install -e .
```

### Step 2: Set Up Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your OpenAI API key
# Minimum required:
OPENAI_API_KEY=sk-your-key-here
```

### Step 3: Test the Installation

```bash
# Run a simple test
uv run ai_software_dev_crew "Create a simple calculator function that adds two numbers"
```

## What Happens During Execution

### Phase 1: Business Analysis (30-60 seconds)
```
🔍 Analyzing requirements and creating Gherkin scenarios...
```
The BA crew will:
- Analyze your vision
- Create user stories
- Write Gherkin scenarios
- Define acceptance criteria

### Phase 2: Development (1-3 minutes)
```
💻 Implementing features using TDD...
```
The Dev crew will:
- Write failing tests (RED)
- Implement code to pass tests (GREEN)
- Refactor for quality
- Commit to git

### Phase 3: Results
```
✅ Development Complete!
💰 Budget Report
```
You'll see:
- Complete implementation in `workspace/`
- Test files and results
- Git history
- Cost breakdown

## Example Outputs

### Workspace Structure After Run
```
workspace/
├── .git/
├── .gitignore
├── src/
│   └── calculator.py
├── tests/
│   └── test_calculator.py
└── README.md
```

### Budget Report Example
```
💰 Budget Report
  Total Cost: $0.0523
  Budget Limit: $100.00
  Budget Used: 0.1%
  Remaining: $99.95

  Cost by Agent:
    - business_analyst: $0.0234
    - backend_developer: $0.0234
    - code_reviewer: $0.0055
```

## More Examples

### Example 1: TODO API
```bash
uv run ai_software_dev_crew "Build a TODO API with FastAPI that supports create, read, update, delete operations"
```

### Example 2: Data Validator
```bash
uv run ai_software_dev_crew "Create an email validator function with comprehensive tests"
```

### Example 3: File Processor
```bash
uv run ai_software_dev_crew "Build a CSV file processor that reads data and calculates statistics"
```

## Troubleshooting

### Issue: "Module not found" errors
**Solution:**
```bash
# Reinstall in development mode
pip install -e .
```

### Issue: "Budget exceeded"
**Solution:**
```bash
# Increase budget in .env
BUDGET_MAX_COST_PER_PROJECT=200.00
```

### Issue: "Workspace not found"
**Solution:**
```bash
# Create workspace directory
mkdir -p workspace
```

### Issue: Git errors
**Solution:**
The system auto-initializes git. If issues persist:
```bash
cd workspace
git init
```

## Advanced Usage

### With Docker Infrastructure

Start the full stack:
```bash
# Start Dragonfly (cache), RabbitMQ, PostgreSQL
docker-compose up -d

# Run the crew
uv run ai_software_dev_crew "Your vision here"

# Stop infrastructure
docker-compose down
```

### Custom Budget Limits

Edit `.env`:
```bash
BUDGET_MAX_COST_PER_PROJECT=50.00   # Total project budget
BUDGET_MAX_COST_PER_HOUR=5.00       # Hourly rate limit
BUDGET_ALERT_THRESHOLD=0.8          # Alert at 80%
```

### Different LLM Models

The system uses `gpt-4o-mini` by default. To change models, edit the crew files or set environment variables for different providers.

## Next Steps

1. ✅ Run your first crew
2. 📝 Review generated code in `workspace/`
3. 🧪 Check test coverage
4. 📊 Review budget usage
5. 🚀 Build something amazing!

## Getting Help

- Check the main [README.md](README.md) for detailed documentation
- Review [AI_CODING_ECOSYSTEM_SETUP.md](../AI_CODING_ECOSYSTEM_SETUP.md) for architecture details
- Open an issue on GitHub for bugs or questions

---

**Ready to build?** Run your first crew now! 🚀


