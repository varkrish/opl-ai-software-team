"""Skill-first framework reference for wiring-patch injection.

When skills are available they are authoritative for folders, files, and
``<wiring_patch>`` shape. Hardcoded stack trees (Go/Frappe/Java/…) are not
injected — that would fight skill content. A language-neutral fallback is used
only when no skill matched.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_stack_manifest(workspace_path: Optional[Path]) -> Optional[dict]:
    if not workspace_path:
        return None
    path = Path(workspace_path) / "stack_manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def infer_app_slug(vision: str = "", *, fallback: str = "my_app") -> str:
    """Derive a snake_case app slug from vision text (never 'app_name')."""
    text = (vision or "").strip()
    patterns = (
        r"(?:frappe\s+app|named|called)\s+[\"']?([A-Za-z][A-Za-z0-9 _-]{1,40})",
        r"(?:for|build(?:ing)?|create)\s+(?:a\s+)?([A-Za-z][A-Za-z0-9 _-]{2,40}?)"
        r"(?:\s+(?:app|application|system|service|platform))?",
    )
    candidate = ""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            break
    if not candidate:
        stop = {
            "create", "build", "make", "a", "an", "the", "for", "with", "and",
            "app", "application", "frappe", "using", "simple", "project",
        }
        words = [
            w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", text)
            if w.lower() not in stop
        ]
        candidate = "_".join(words[:3]) if words else fallback
    slug = re.sub(r"[^a-z0-9]+", "_", candidate.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug or slug in {"app", "app_name", "my_app", "example"}:
        slug = fallback if fallback not in {"app_name", "example"} else "project_app"
    if slug[0].isdigit():
        slug = f"app_{slug}"
    return slug[:48]


def detect_stack_family(
    vision: str = "",
    *,
    skill_context: str = "",
    stack_manifest: Optional[dict] = None,
    workspace_path: Optional[Path] = None,
) -> str:
    """Return one of: frappe|go|java|javascript|typescript|python|generic.

    Used for logging / safety-net routing — not for injecting concrete trees.
    """
    manifest = stack_manifest if stack_manifest is not None else _load_stack_manifest(workspace_path)
    tokens: list[str] = []
    if isinstance(manifest, dict):
        for key in ("chosen_stack", "explicit_technologies"):
            vals = manifest.get(key) or []
            if isinstance(vals, list):
                tokens.extend(str(v).lower() for v in vals)
        for key in ("skills_query", "delivery_surface", "language"):
            if manifest.get(key):
                tokens.append(str(manifest[key]).lower())
    blob = " ".join(tokens) + "\n" + (vision or "").lower() + "\n" + (skill_context or "").lower()

    if any(t in blob for t in ("frappe", "erpnext", "doctype", "bench new-app")):
        return "frappe"
    vision_l = (vision or "").lower()
    if any(t in blob for t in ("golang", "go.mod", "gin-gonic")) or re.search(
        r"\b(golang|go\s+module)\b", vision_l
    ):
        return "go"
    if re.search(r"\bgo\b", vision_l) and not any(
        t in vision_l for t in ("frappe", "python", "java", "react", "node")
    ):
        return "go"
    if any(t in blob for t in ("spring", "java", "maven", "gradle", "pom.xml")):
        return "java"
    if any(t in blob for t in ("typescript", "tsx", "next.js", "nestjs", "angular")):
        return "typescript"
    if any(t in blob for t in ("javascript", "node.js", "nodejs", "react", "express", "vue")):
        return "javascript"
    if any(t in blob for t in ("python", "fastapi", "django", "flask", "pytest")):
        return "python"
    return "generic"


_NEUTRAL_WIRING_RULES = """\
WIRING RULES (language-neutral — skill content supplies concrete paths):
- Derive .module, .language, and packages[*].files from FRAMEWORK SKILLS when present.
- Never invent a competing language layout that contradicts those skills.
- Never use the literal token app_name as a path segment or .module value.
- Never use bare layer names (api, cmd, src, service) alone as .module.
- Always set .packages["<pkg>"].files to concrete paths with extensions.
- Symbol keys MUST be "package.SymbolName".
"""


def build_neutral_wiring_fallback(*, vision: str = "") -> str:
    """Minimal fallback when no skill matched — no stack-specific tree."""
    slug = infer_app_slug(vision, fallback="my_app")
    return f"""\
{_NEUTRAL_WIRING_RULES}

No indexed skill matched closely enough. Use a real import root from the vision
(example slug only: {slug}) and emit paths that match the chosen stack in the vision.
Do not copy a Go, Java, or Frappe layout unless the vision/skills require it.

<wiring_patch>
.module = "{slug}"
| .language = "<primary language from vision>"
| .packages["{slug}"].files = ["{slug}/<entrypoint with extension>"]
| .packages["{slug}"].owns = ["<primary symbol>"]
</wiring_patch>
"""


def build_stack_wiring_example(
    family: str,
    *,
    vision: str = "",
) -> str:
    """Deprecated alias — returns language-neutral fallback (skills own layout)."""
    return build_neutral_wiring_fallback(vision=vision)


def compose_framework_reference_with_wiring(
    skill_context: str,
    *,
    vision: str = "",
    workspace_path: Optional[Path] = None,
    stack_manifest: Optional[dict] = None,
) -> str:
    """Merge skills + neutral wiring rules. Skills are authoritative for layout.

    Concrete package trees come from skill docs, not hardcoded stack examples.
    """
    family = detect_stack_family(
        vision,
        skill_context=skill_context or "",
        stack_manifest=stack_manifest,
        workspace_path=workspace_path,
    )
    skills = (skill_context or "").strip()
    if skills:
        logger.info("Framework reference: skills authoritative (family=%s)", family)
        return (
            "FRAMEWORK SKILLS (AUTHORITATIVE for folders, files, and wiring_patch paths):\n"
            "Follow these skill conventions. Do not invent a competing layout from memory.\n\n"
            f"{skills}\n\n"
            f"{_NEUTRAL_WIRING_RULES}"
        )
    logger.info("Framework reference: no skills — neutral fallback (family=%s)", family)
    return build_neutral_wiring_fallback(vision=vision)
