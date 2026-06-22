"""Shared content-quality checks for crew_studio in-container tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app/agent/src")

from llamaindex_crew.utils.output_parser import (  # noqa: E402
    is_valid_design_spec,
    is_valid_gherkin_feature,
    is_valid_markdown_artifact,
    is_valid_tech_stack,
    looks_like_raw_agent_dump,
)

# Generic placeholder names the model copies from prompt examples.
_PLACEHOLDER_FEATURE_NAMES = frozenset({
    "data_transformation.feature",
    "data_input.feature",
})


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def validate_po_artifacts(workspace: Path, vision: str = "") -> list[str]:
    """Return human-readable errors for product-owner outputs."""
    errors: list[str] = []
    vision_lower = (vision or "").lower()

    req = workspace / "requirements.md"
    stories = workspace / "user_stories.md"
    features_dir = workspace / "features"

    if not req.exists():
        errors.append("requirements.md missing")
    else:
        text = _read(req)
        if not is_valid_markdown_artifact(text, min_chars=100, min_lines=3):
            errors.append(
                f"requirements.md invalid ({len(text)} chars): "
                "must be markdown with headings, not truncated or raw agent dump"
            )

    if not stories.exists():
        errors.append("user_stories.md missing")
    else:
        text = _read(stories)
        if looks_like_raw_agent_dump(text):
            errors.append("user_stories.md contains raw agent/tool transcript")
        elif not is_valid_markdown_artifact(text, min_chars=80, min_lines=3):
            errors.append(f"user_stories.md too short or not markdown ({len(text)} chars)")

    feature_files = sorted(features_dir.glob("*.feature")) if features_dir.exists() else []
    if not feature_files:
        errors.append("no features/*.feature files")
    for feat in feature_files:
        text = _read(feat)
        if not is_valid_gherkin_feature(text):
            errors.append(
                f"{feat.name} is not valid Gherkin "
                f"(need Feature/Scenario/steps, got {len(text)} chars)"
            )
        if (
            feat.name in _PLACEHOLDER_FEATURE_NAMES
            and "data processing" not in vision_lower
            and "cli" not in vision_lower
            and "csv" not in vision_lower
        ):
            errors.append(
                f"{feat.name} looks like a copied prompt example, not this project's domain"
            )

    return errors


def validate_design_spec(workspace: Path) -> list[str]:
    errors: list[str] = []
    path = workspace / "design_spec.md"
    if not path.exists():
        errors.append("design_spec.md missing")
        return errors
    text = _read(path)
    if not is_valid_design_spec(text):
        preview = text[:120].replace("\n", " ")
        errors.append(
            f"design_spec.md invalid ({len(text)} chars): "
            f"expected markdown design doc, not tool-call dump. Preview: {preview!r}"
        )
    return errors


def validate_tech_stack(workspace: Path) -> list[str]:
    errors: list[str] = []
    for name in ("tech_stack.md", "implementation_plan.md"):
        path = workspace / name
        if not path.exists():
            errors.append(f"{name} missing")
    ts = _read(workspace / "tech_stack.md")
    if ts and not is_valid_tech_stack(ts):
        errors.append(
            f"tech_stack.md invalid ({len(ts)} chars): "
            "must include a file tree or source paths"
        )
    impl = _read(workspace / "implementation_plan.md")
    if impl and not is_valid_markdown_artifact(impl, min_chars=80, min_lines=3):
        errors.append(f"implementation_plan.md invalid ({len(impl)} chars)")
    return errors


def validate_dev_sources(workspace: Path, paths: list[str], *, min_bytes: int = 40) -> list[str]:
    errors: list[str] = []
    for rel in paths:
        if not rel:
            continue
        path = workspace / rel
        if not path.exists():
            errors.append(f"dev file missing: {rel}")
            continue
        size = path.stat().st_size
        if size < min_bytes:
            errors.append(f"dev file too small ({size} B): {rel}")
        text = _read(path)
        if looks_like_raw_agent_dump(text):
            errors.append(f"dev file contains raw agent dump: {rel}")
    return errors


def assert_or_exit(errors: list[str], label: str) -> None:
    if not errors:
        return
    print(f"\n❌ {label} ASSERTIONS FAILED:")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)
