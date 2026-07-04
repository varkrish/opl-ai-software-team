# Agentic Platform — Quality & Integration

> **Updated:** July 2026  
> **Evidence base:** Latest retained build output in this repo — calculator E2E workspace (`agent/tests/e2e/test_workspace_llamaindex_e2e/`, 30 Jun 2026).  
> **Current status:** Core validation and loop infrastructure is **implemented**. Gaps below are grounded in that run plus validator design limits.

---

## Latest measured output (calculator E2E, 30 Jun 2026)

The only substantive generated project kept under this repo. `opl-ai-software-team/workspace/job-*` folders are **unit-test stubs** (single `a.java` each) — not real build output. `opl_ai_mono/workspace/` is empty.

| Metric | Result |
|---|---|
| Project | Python calculator — 4 source files, pytest tests, full planning artifacts |
| Integration (syntax + import paths) | **Pass** — all files in `validation_report.json` |
| Completeness, deps, tech stack, package structure | **Pass** |
| Duplicate files check | **Fail** — false positive: `src/__init__.py` vs `src/calculator/__init__.py` |
| Duplicate code blocks | **Fail** — repeated docstring/validation patterns (non-blocking) |
| Smoke test | **Pass** — syntax-only mode (`SMOKE_TEST_BACKEND=syntax_only`) |
| Cross-file imports | `from src.calculator.calculator import Calculator` — correct on this small project |
| Container smoke log | Earlier `SyntaxError` at line 7 logged; final source is valid — suggests fix pass or log from prior iteration |

**Takeaway:** Small greenfield Python apps validate cleanly. This run does **not** exercise multi-file JS/TS stacks where export-name mismatches show up. Need a retained 20+ file Node or Frappe workspace to measure that gap empirically.

---

## Implemented

| Capability | Implementation | Key location |
|---|---|---|
| Post-generation validation | `CodeCompletenessValidator` — syntax, local import resolution, per-file integration | `orchestrator/code_validator.py` |
| Shared interface contract | `tech_stack.md`, `design_spec.md`, `implementation_plan.md`, `api_contract.yaml`, `solution_spec.md` | TechArch + solutioning loop |
| Single-stack enforcement | `validate_tech_stack_conformance()` | `code_validator.py` |
| Dependency-aware ordering | Task deps in `implementation_plan.md`; Dev generates in plan order | `task_manager.py`, `software_dev_workflow.py` |
| Manifest completeness | `validate_dependency_manifest()`, `validate_package_json_completeness()`, auto-fix | `code_validator.py`, `_auto_fix_issues()` |
| Runnable smoke / test loop | Feature test bed — container tests → DevAgent fix → retry | `_run_feature_test_bed_loop()` |
| Hallucinated library check | npm / PyPI / Maven registry lookup | `code_validator.py` |
| Validation persistence | `validation_issues` table; DevAgent reads pending issues | `job_database.py`, workflow |
| Cross-file export context (prompt) | `extract_export_summary()` injected at file generation | `software_dev_workflow.py` |
| API contract conformance | OpenAPI from TechArch; endpoint check | `tech_architect_agent.py`, `code_validator.py` |
| Loop engineering | Solutioning loop; test bed with `loop_state` in job metadata | `solutioning_loop.py`, workflow |
| Brownfield edits | RefinementAgent + tldr (`code_impact`, impact scope) | `refinement_agent.py`, `refinement_runner.py` |

Full suite: `_run_validation_suite()` at end of development. External: `crew-code-validator` on port 8180.

---

## Remaining gaps

### 1. Export name / signature matching (unmeasured on latest run)

**What works:** Import **paths** resolve (`./foo` exists; `from src.calculator.calculator import Calculator` on calculator E2E).  
**What's not validated:** Named symbol exists in target module (`getProfile` vs `fetchProfile`); default vs named export mismatch; call-site signature compatibility.

Calculator E2E is too small to hit this. Validator design gap remains for multi-file JS/TS/Python apps.

**Next step:** Named export validation — cross-check imports against `extract_export_summary()` on dependency files.

### 2. Validator false positives (seen on calculator E2E)

Duplicate-file check flags legitimate nested `__init__.py` files. Duplicate-code check flags repeated test parametrization patterns.

**Next step:** Scope duplicate-file check to same directory; raise duplicate-code threshold for test files.

### 3. Smoke test depth (seen on calculator E2E)

Final report passed syntax-only smoke; `smoke_test_container.log` shows a prior syntax error. Full `pytest` in container may not be the gate when `SMOKE_TEST_BACKEND=syntax_only`.

**Next step:** Default `test_plan.md` per stack; run real pytest/vitest in test bed for E2E and production jobs.

### 4. Prevention vs detection

Validators run after generation. First-pass quality varies by model and project size.

**Next step:** Post-job confidence score (`opl-crew-enhancements.md`) to measure first-pass vs post-fix.

### 5. Large-project context truncation

Export summaries in prompts truncate on 40+ file projects.

**Next step:** RAG over `DocumentIndexer` for contract sections at file generation time.

### 6. Platform self-modification (out of scope)

Import + refinement targets customer repos. OPL platform code (multi-repo mono, no OPL-specific skills) should be built by the engineering team.

---

## Priority for next improvements

| Priority | Improvement | Evidence | Effort |
|---|---|---|---|
| 1 | Named export validation | Design gap; not seen in calculator E2E (too small) | Medium |
| 2 | Fix duplicate-file / duplicate-code false positives | **Failed checks on calculator E2E** | Low |
| 3 | Real pytest smoke (not syntax-only) on E2E | Log shows shallow gate | Low |
| 4 | Post-job confidence score | No measurement yet | Medium |
| 5 | Retain + analyze a multi-file Node/Frappe workspace | No such output in repo today | — (run first) |

---

## How to evaluate a run today

1. **`validation_report.json`** in job workspace (if retained)
2. **`validation_issues`** — Studio UI or `GET /api/jobs/{id}`
3. **`jobs.metadata.confidence_score`** — once post-job hook ships
4. **Refinement count** — `refinements` table; 0–1 healthy, 3+ suggests first-pass problems
5. **`jobs.metadata.loop_state.test_iteration`** — test bed retries
6. **`smoke_test_container.log`** — actual container output vs syntax-only pass

---

## What to run next for better data

To replace calculator-only evidence with multi-file findings:

1. Submit a Node or Frappe vision (20+ files expected)
2. Keep workspace after completion (do not wipe)
3. Read `validation_report.json`, `validation_issues`, `smoke_test_container.log`
4. Manually spot-check one cross-file chain (routes → controller → model)

Further gains: confidence score (measurement), export-level validation (tighten), MemMachine (cross-job context — see `opl-crew-enhancements.md`).
