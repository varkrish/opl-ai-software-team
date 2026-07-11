"""Tests for approved solution_spec binding in downstream phases."""
from llamaindex_crew.utils.vision_stack_analysis import (
    build_stack_selection_brief,
    detect_solution_spec_mismatch,
    format_approved_solution_contract,
)


WEALTH_TAX_SPEC = """
# Solution Architecture
React + NestJS monorepo with:
- api-gateway (NestJS)
- ob-service (open banking)
- transaction-service
- tax-service
- budget-module
"""

NEXTJS_MONOLITH = """
# Technology Stack
Next.js 14 monolith
## File Structure
```
src/
├── pages/
│   └── api/
│       └── transactions.ts
```
"""

NESTJS_TREE = """
# Technology Stack
React + NestJS monorepo
## File Structure
```
apps/
├── api-gateway/
├── ob-service/
├── transaction-service/
├── tax-service/
└── web-client/
```
"""


NESTJS_SPEC = """
## Technology Stack Choices
| **Backend** | NestJS (TypeScript) • GraphQL • Prisma |
| **Web Front-end** | Next.js 14 (React 18) • Tailwind CSS |

## Explicit Non-Goals
| **Full Laravel/PHP stack** (Firefly III) | Language mismatch; duplicate business logic. |
"""

NESTJS_TECH_STACK = """
# Technology Stack
- **Backend**: NestJS (TypeScript) – GraphQL, Prisma ORM (PostgreSQL)
- **Web Front-end**: Next.js 14 (React 18) – Tailwind CSS
"""

NEXTJS_STARTER_STACK = """
# Technology Stack
- **Frontend**: React 18, Next.js 14 (App Router), TypeScript, Tailwind CSS
- **Backend**: Next.js API Routes, Node.js 20, Prisma ORM, PostgreSQL 15
## File Structure
```
apps/web/
apps/api/
```
"""


class TestDetectSolutionSpecMismatch:
    def test_rejected_laravel_in_non_goals_does_not_fail_nestjs_stack(self):
        assert detect_solution_spec_mismatch(NESTJS_SPEC, NESTJS_TECH_STACK) is None

    def test_nextjs_monolith_violates_nestjs_microservices(self):
        reason = detect_solution_spec_mismatch(WEALTH_TAX_SPEC, NEXTJS_MONOLITH)
        assert reason is not None
        assert "nest" in reason.lower() or "service" in reason.lower()

    def test_matching_tree_passes(self):
        assert detect_solution_spec_mismatch(WEALTH_TAX_SPEC, NESTJS_TREE) is None

    def test_nextjs_starter_satisfies_express_or_alternative(self):
        spec = """
| **Frontend** | React 18, Next.js 14 (App Router), TypeScript |
| **Backend / API** | Next.js API Routes **or** Express, Node.js 20, Prisma ORM |
"""
        assert detect_solution_spec_mismatch(spec, NEXTJS_STARTER_STACK) is None

    def test_collapsed_monolith_missing_named_components(self):
        reason = detect_solution_spec_mismatch(WEALTH_TAX_SPEC, NEXTJS_MONOLITH)
        assert reason is not None
        assert "component" in reason.lower() or "service" in reason.lower()


class TestApprovedSolutionBrief:
    def test_approved_mode_mentions_binding(self):
        brief = build_stack_selection_brief(
            "Build a wealth app",
            approved_solution=True,
        )
        assert "APPROVED SOLUTION SPEC is binding" in brief
        assert "do not simplify" in brief.lower()

    def test_default_mode_allows_simplify(self):
        brief = build_stack_selection_brief("Build a wealth app")
        assert "simplify the stack" in brief.lower()


class TestFormatApprovedSolutionContract:
    def test_includes_spec_body(self):
        section = format_approved_solution_contract("My approved arch")
        assert "BINDING" in section
        assert "My approved arch" in section

    def test_empty_returns_empty(self):
        assert format_approved_solution_contract("") == ""
        assert format_approved_solution_contract("   ") == ""
