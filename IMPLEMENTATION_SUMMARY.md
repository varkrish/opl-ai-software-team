# Pluggable Agentic Systems - Option A Implementation Summary

## Overview
Implemented minimal pluggable backend architecture allowing users to select between different agentic AI systems (OPL AI Team and Aider) from the Landing page.

## Implementation Status: ✅ COMPLETE

### Backend Changes

#### 1. Backend Registry (`agent/src/llamaindex_crew/backends/`)
- **`__init__.py`**: Simple `Backend` class protocol and `BackendRegistry` singleton
- **`opl_crew.py`**: `OPLCrewBackend` wrapper for existing LlamaIndex workflow
- **`aider_backend.py`**: `AiderBackend` for CLI-based AI pair programming
  - Auto-detects `aider` CLI using `shutil.which()`
  - Runs as subprocess with output streaming

#### 2. API Changes (`crew_studio/llamaindex_web_app.py`)
- **New endpoint**: `GET /api/backends` - Lists available backends with availability status
- **Updated endpoint**: `POST /api/jobs` - Accepts optional `backend` parameter
- **New function**: `run_job_with_backend()` - Executes jobs using selected backend
- Validates backend availability before job creation

### Frontend Changes

#### 1. Type Definitions (`studio-ui/src/types/index.ts`)
- Added `BackendOption` interface with `name`, `display_name`, `available` fields

#### 2. API Client (`studio-ui/src/api/client.ts`)
- Added `getBackends()` function
- Updated `createJob()` to accept optional `backend` parameter

#### 3. Landing Page (`studio-ui/src/pages/Landing.tsx`)
- Added PatternFly `Select` dropdown for backend selection
- Loads available backends on component mount
- Defaults to "OPL AI Team"
- Disables unavailable backends (e.g., Aider if not installed)
- Passes selected backend to job creation

### Testing

#### 1. API Tests (`agent/tests/api/test_backends_endpoints.py`)
- `test_list_backends()` - Verifies GET /api/backends returns correct structure
- `test_create_job_with_backend()` - Tests job creation with valid backend
- `test_create_job_with_invalid_backend()` - Tests error handling for unknown backends
- `test_create_job_with_unavailable_backend()` - Tests error handling for unavailable backends

All tests passing ✅

#### 2. Build Verification
- Backend: Flask app imports successfully, registry loads 2 backends
- Frontend: TypeScript compiles, Vite build completes successfully

## Features

### Current Backends
1. **OPL AI Team** (default, always available)
   - Existing LlamaIndex-based multi-agent software dev crew
   - 6 specialized agents (PM, Architect, Dev, QA, DevOps, Docs)
   
2. **Aider** (requires `aider` CLI installation)
   - AI pair programming in the terminal
   - Auto-detected via `shutil.which("aider")`
   - Runs as subprocess with streaming output

### User Experience
- Small dropdown on Landing page (similar to ChatGPT model selector)
- Shows availability status (dims unavailable options)
- Seamless integration with existing job creation flow
- Maintains backward compatibility (defaults to OPL if backend not specified)

## Scope Reductions from Comprehensive Plan

Following the "Option A (Minimal)" approach, we **excluded**:
- ❌ Additional backends (OpenHands, Claude CLI, Cursor CLI, GitHub Copilot)
- ❌ Settings page "Agentic Systems" tab
- ❌ Per-backend configuration storage (ConfigStore)
- ❌ Database schema changes (backend column, user_config table)
- ❌ `/api/backends/detect` and `/api/config` endpoints
- ❌ BuildProgress adaptations for different phase sets
- ❌ Comprehensive unit tests for registry/ABC
- ❌ Cypress E2E tests for backend selection
- ❌ Strict TDD workflow

## Future Enhancements (Deferred)

When adding more backends later:
1. Create new backend classes in `agent/src/llamaindex_crew/backends/`
2. Register them in `backends/__init__.py`
3. Add configuration UI in Settings page if needed
4. Consider database persistence for user preferences

## Technical Notes

### Dependencies
- No new Python dependencies required for core functionality
- Aider backend requires `aider-chat` package (optional)
- Frontend uses existing PatternFly React components

### Backward Compatibility
- Default behavior unchanged (uses OPL AI Team)
- Existing API clients work without modification
- Old jobs continue to function normally

### Performance
- Backend registry loads on app startup (fast, no DB queries)
- CLI detection uses `shutil.which()` (cached by OS)
- Job execution delegated to selected backend (same performance as before for OPL)

## Files Modified/Created

### Created
- `agent/src/llamaindex_crew/backends/__init__.py`
- `agent/src/llamaindex_crew/backends/opl_crew.py`
- `agent/src/llamaindex_crew/backends/aider_backend.py`
- `agent/tests/api/test_backends_endpoints.py`

### Modified
- `crew_studio/llamaindex_web_app.py` (added endpoint, updated job creation)
- `studio-ui/src/types/index.ts` (added BackendOption)
- `studio-ui/src/api/client.ts` (added getBackends, updated createJob)
- `studio-ui/src/pages/Landing.tsx` (added backend selector)
- `studio-ui/tsconfig.json` (excluded cypress from build)

## Verification Commands

```bash
# Backend: Test registry
cd agent
PYTHONPATH=$PWD:$PWD/src python3 -c "from llamaindex_crew.backends import registry; print(registry.list_backends())"

# Backend: Run API tests
cd agent
PYTHONPATH=$PWD/..:$PWD:$PWD/src python3.12 -m pytest tests/api/test_backends_endpoints.py -v

# Frontend: Build
cd studio-ui
npm run build

# Backend: Verify imports
cd ..
python3 -c "import sys; sys.path.insert(0, 'agent'); sys.path.insert(0, 'agent/src'); from crew_studio.llamaindex_web_app import app; from src.llamaindex_crew.backends import registry; print('OK')"
```

All verifications passing ✅
