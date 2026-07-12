"""
Infer delivery requirements and stack constraints from project vision.

Used by Designer and Tech Architect to choose the *minimal appropriate* stack
without requiring users to spell out anti-patterns ("no Frappe", etc.).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Named stacks the pipeline recognises in vision text or artifacts.
_NAMED_TECHNOLOGIES: Tuple[Tuple[str, Sequence[str]], ...] = (
    ("frappe", ("frappe", "erpnext", "bench new-app", "bench init")),
    ("django", ("django",)),
    ("fastapi", ("fastapi",)),
    ("flask", ("flask",)),
    ("spring boot", ("spring boot", "springboot", "spring-boot")),
    ("react", ("react", "react.js", "reactjs")),
    ("angular", ("angular",)),
    ("vue", ("vue", "vue.js", "vuejs")),
    ("next.js", ("next.js", "nextjs")),
    ("nestjs", ("nestjs", "nest.js")),
    ("express", ("express", "express.js")),
    ("node.js", ("node.js", "nodejs")),
    ("rails", ("rails", "ruby on rails")),
    ("laravel", ("laravel",)),
    ("dotnet", (".net", "asp.net", "aspnet")),
    ("apache camel", ("apache camel", "camel route")),
    ("react native", ("react native",)),
    ("flutter", ("flutter",)),
)

# Application-framework markers that imply a server-side platform (not infra alone).
_APPLICATION_FRAMEWORK_MARKERS: Tuple[Tuple[str, Sequence[str]], ...] = (
    ("frappe", ("frappe", "doctype", "hooks.py", "erpnext", "bench ")),
    ("django", ("django", "manage.py", "wsgi.py")),
    ("fastapi", ("fastapi", "uvicorn")),
    ("flask", ("flask", "flask_app")),
    ("spring boot", ("spring boot", "springboot", "@springbootapplication", "pom.xml")),
    ("express", ("express", "express()")),
    ("rails", ("rails", "application.rb")),
    ("laravel", ("laravel", "artisan")),
    ("react", ("react", "jsx", "tsx", "@testing-library/react")),
    ("angular", ("angular", "@angular/")),
    ("vue", ("vue", "vuex", "pinia")),
    ("next.js", ("next.js", "nextjs", "getserversideprops")),
    ("nestjs", ("nestjs", "nest.js", "@nestjs")),
)

_PERSISTENCE_PATTERNS = (
    r"\bdatabase\b", r"\bdb\b", r"\bsql\b", r"\bpostgres", r"\bmysql\b",
    r"\bmariadb\b", r"\bmongo", r"\bcrud\b", r"\bpersist", r"\bmigration",
    r"\bschema\b", r"\btable\b", r"\bentity\b", r"\borm\b",
)
_API_PATTERNS = (
    r"\bapi\b", r"\brest\b", r"\bgraphql\b", r"\bendpoint", r"\bwebhook",
    r"\bmicroservice",
)
_AUTH_PATTERNS = (
    r"\bauth", r"\blogin\b", r"\bsign[\s-]?in\b", r"\buser\s+account",
    r"\bjwt\b", r"\boauth", r"\bpermission", r"\brbac\b", r"\bsso\b",
)
_CLIENT_SURFACE_PATTERNS = (
    r"\bpage\b", r"\bui\b", r"\bfrontend\b", r"\bscreen\b", r"\bwidget\b",
    r"\bcomponent\b", r"\bvisuali", r"\bchart\b", r"\bmap\b", r"\bdashboard\b",
    r"\blanding\b", r"\bform\b", r"\bdisplay\b", r"\brender\b",
)
_MARKUP_PREFIX = re.compile(r"^\s*(<!doctype\s+html|<html[\s>])", re.IGNORECASE)


@dataclass
class CapabilityProfile:
    """Capabilities implied by the vision — not a hardcoded stack choice."""

    explicit_technologies: List[str] = field(default_factory=list)
    needs_persistence: bool = False
    needs_api: bool = False
    needs_auth: bool = False
    needs_server_runtime: bool = False
    has_client_surface: bool = False
    delivery_surface: str = "unspecified"
    complexity: str = "moderate"
    evidence: List[str] = field(default_factory=list)
    suggested_path: str = "full"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize(text: str) -> str:
    return (text or "").lower()


def _match_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _extract_named_technologies(text: str) -> List[str]:
    found: List[str] = []
    for name, markers in _NAMED_TECHNOLOGIES:
        if any(m in text for m in markers):
            found.append(name)
    return found


def _looks_like_markup_payload(vision: str) -> bool:
    stripped = (vision or "").lstrip()
    return bool(_MARKUP_PREFIX.match(stripped))


def infer_capability_profile(vision: str, user_stories: str = "") -> CapabilityProfile:
    """Infer what the vision actually requires — tiers, persistence, named tech."""
    combined = _normalize(f"{vision}\n{user_stories}")
    profile = CapabilityProfile()
    profile.explicit_technologies = _extract_named_technologies(combined)

    if _looks_like_markup_payload(vision):
        profile.has_client_surface = True
        profile.evidence.append("vision contains markup deliverable")

    if _match_any(combined, _PERSISTENCE_PATTERNS):
        profile.needs_persistence = True
        profile.evidence.append("persistence/data storage implied")
    if _match_any(combined, _API_PATTERNS):
        profile.needs_api = True
        profile.evidence.append("API/service interface implied")
    if _match_any(combined, _AUTH_PATTERNS):
        profile.needs_auth = True
        profile.evidence.append("authentication/authorization implied")
    if _match_any(combined, _CLIENT_SURFACE_PATTERNS):
        profile.has_client_surface = True
        profile.evidence.append("client/UI deliverable implied")

    profile.needs_server_runtime = (
        profile.needs_persistence
        or profile.needs_api
        or profile.needs_auth
        or "backend" in combined
        or "server-side" in combined
        or "server side" in combined
        or (
            bool(profile.explicit_technologies)
            and not _is_client_only_technology(profile.explicit_technologies)
        )
    )

    profile.delivery_surface = _infer_delivery_surface(profile, combined)
    profile.complexity = _infer_complexity(profile, combined)
    profile.suggested_path = _suggest_solutioning_path(profile)
    return profile


def _has_named_application_framework(profile: CapabilityProfile) -> bool:
    """True when vision names a non-client-only application framework."""
    if not profile.explicit_technologies:
        return False
    return not _is_client_only_technology(profile.explicit_technologies)


def _suggest_solutioning_path(profile: CapabilityProfile) -> str:
    """Heuristic Fast vs Full suggestion from a CapabilityProfile."""
    if (
        profile.complexity == "minimal"
        and profile.delivery_surface == "client_deliverable"
        and not _has_named_application_framework(profile)
        and not profile.needs_server_runtime
        and not profile.needs_api
        and not profile.needs_auth
        and not profile.needs_persistence
    ):
        return "fast"
    return "full"


def decide_solutioning_path(
    vision: str,
    solutioning_path: Optional[str] = None,
    user_stories: str = "",
    profile: Optional[CapabilityProfile] = None,
) -> str:
    """
    Resolve the effective solutioning path for a job.

    - ``full`` / ``fast`` overrides always win.
    - ``adaptive`` uses CapabilityProfile heuristics.
    - Missing / unknown path defaults to ``full`` (safer create-job default).
    """
    preference = (solutioning_path or "").strip().lower()
    if preference == "fast":
        return "fast"
    if preference == "full":
        return "full"
    # "adaptive" or "" (unspecified / auto) → infer from vision
    if preference in ("adaptive", ""):
        resolved = profile or infer_capability_profile(vision, user_stories)
        return _suggest_solutioning_path(resolved)
    return "full"


def _is_client_only_technology(technologies: Sequence[str]) -> bool:
    client_only = {"react", "angular", "vue", "react native", "flutter"}
    return bool(technologies) and all(t in client_only for t in technologies)


def _infer_delivery_surface(profile: CapabilityProfile, text: str) -> str:
    if any(t in ("frappe", "django", "rails", "laravel") for t in profile.explicit_technologies):
        return "platform_app"
    if profile.needs_api and not profile.has_client_surface:
        return "api_service"
    if profile.needs_server_runtime and profile.has_client_surface:
        return "fullstack"
    if profile.has_client_surface and not profile.needs_server_runtime:
        return "client_deliverable"
    if "microservice" in text or "service" in text:
        return "api_service"
    if profile.explicit_technologies:
        return "named_stack"
    return "unspecified"


def _infer_complexity(profile: CapabilityProfile, text: str) -> str:
    if "simple" in text or "minimal" in text or "single" in text:
        if not profile.needs_server_runtime:
            return "minimal"
    if profile.needs_server_runtime and (profile.needs_api or profile.needs_persistence):
        return "complex"
    if profile.delivery_surface == "client_deliverable" and not profile.needs_auth:
        return "minimal"
    return "moderate"


def _frameworks_in_text(text: str) -> List[str]:
    lower = _normalize(text)
    found: List[str] = []
    for name, markers in _APPLICATION_FRAMEWORK_MARKERS:
        if any(m in lower for m in markers):
            found.append(name)
    return found


_SERVER_PLATFORMS = {
    "frappe", "django", "fastapi", "flask", "spring boot", "rails", "laravel", "express",
}
_TIER_MARKERS: Tuple[Tuple[str, Sequence[str]], ...] = (
    ("application_server", ("frappe", "django", "fastapi", "flask", "spring boot",
                            "rails", "laravel", "express", "uvicorn", "gunicorn")),
    ("database", ("mariadb", "postgres", "mysql", "mongodb", "sqlite", "database", "orm")),
    ("cms_platform", ("frappe", "wordpress", "drupal", "cms")),
)


def _chosen_unlocks_tier(chosen: Sequence[str], markers: Sequence[str]) -> bool:
    """True when chosen_stack already selects something belonging to this tier.

    Technology-agnostic: string overlap only — no per-framework companion lists.
    """
    chosen_l = [c.lower() for c in chosen if c]
    markers_l = [m.lower() for m in markers]
    for c in chosen_l:
        for m in markers_l:
            if m == c or m in c or c in m:
                return True
    return False


def _tier_markers(tier: str) -> Sequence[str]:
    for name, markers in _TIER_MARKERS:
        if name == tier:
            return markers
    return ()


def _effective_forbidden_tiers(
    forbidden: Sequence[str],
    chosen: Sequence[str],
) -> List[str]:
    """Drop forbidden tiers that chosen_stack already unlocks."""
    out: List[str] = []
    for tier in forbidden:
        markers = _tier_markers(tier)
        if markers and _chosen_unlocks_tier(chosen, markers):
            continue
        out.append(tier)
    return out


def detect_stack_overreach(
    vision: str,
    artifact: str,
    user_stories: str = "",
    stack_manifest: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Return a human-readable reason when *artifact* introduces stack tiers or
    frameworks not justified by the vision (or locked manifest), or None when coherent.
    """
    if not (vision or "").strip() or not (artifact or "").strip():
        return None

    artifact_lower = _normalize(artifact)
    introduced = _frameworks_in_text(artifact)

    if stack_manifest:
        violation = _manifest_forbidden_violation(artifact, stack_manifest)
        if violation:
            return violation
        chosen = [t.lower() for t in (stack_manifest.get("chosen_stack") or [])]
        # Locked contract wins: only reject named frameworks outside chosen_stack.
        # Do not re-apply vision heuristics that contradict an approved solution.
        if chosen:
            for fw in introduced:
                if fw in _SERVER_PLATFORMS and not any(
                    fw == c or fw in c or c in fw for c in chosen
                ):
                    return (
                        f"Artifact introduces {fw!r} but locked stack_manifest "
                        f"chosen_stack={chosen} does not include it."
                    )
            return None

    profile = infer_capability_profile(vision, user_stories)
    if not introduced:
        return None

    explicit_lower = [t.lower() for t in profile.explicit_technologies]

    # Vision names a stack — artifact must stay on that stack.
    for fw in introduced:
        if explicit_lower and not any(fw in t or t in fw for t in explicit_lower):
            return (
                f"Artifact introduces {fw!r} but the vision specifies "
                f"{', '.join(profile.explicit_technologies)} — stack mismatch."
            )

    # No server-side need — application platforms are overreach.
    if not profile.needs_server_runtime:
        for fw in introduced:
            if fw in _SERVER_PLATFORMS:
                return (
                    f"Artifact introduces server-side platform {fw!r}, but the vision "
                    "does not require persistence, APIs, authentication, or a named "
                    "application framework — choose a minimal client-side deliverable."
                )

    # Client deliverable without API — full backend stacks are overreach.
    if profile.delivery_surface == "client_deliverable" and not profile.needs_api:
        for fw in introduced:
            if fw in _SERVER_PLATFORMS:
                return (
                    f"Artifact introduces {fw!r} for a client-only deliverable; "
                    "the vision describes a UI/content artifact without backend tiers."
                )

    return None


def _extract_service_components(text: str) -> List[str]:
    """Named services/modules from an approved solution spec (technology-agnostic)."""
    lower = _normalize(text)
    found: List[str] = []
    for match in re.finditer(
        r"\b(api-gateway|[a-z][a-z0-9-]*-(?:service|module|gateway))\b",
        lower,
    ):
        name = match.group(1)
        if name not in found and name not in ("microservice",):
            found.append(name)
    return found[:20]


def extract_named_components(*texts: str) -> List[str]:
    """
    Extract named modules/components from design or solution specs.

    Technology-agnostic: uses numbered lists and component section headings,
    not framework keywords. Path-like entries (e.g. ``/pages``, ``/api``) are
    kept as folder contracts and matched as directories later.
    """
    seen: set[str] = set()
    found: List[str] = []

    def _add(name: str) -> None:
        name = _normalize_component_label(name)
        key = name.lower()
        if name and key not in seen and len(name) >= 2:
            seen.add(key)
            found.append(name)

    for text in texts:
        if not (text or "").strip():
            continue

        # Numbered bold items: "1. **Gateway API (NestJS)**" or "1. **`/pages` (UI)**"
        for match in re.finditer(r"^\s*\d+\.\s+\*\*([^*]+)\*\*", text, re.MULTILINE):
            _add(match.group(1))

        # Component sections only — avoid matching every bold label in design_spec
        section_re = re.compile(
            r"(?:(?:major components|modules|bounded contexts|core components)"
            r"[^\n]*\n)(.*?)(?=\n#{1,3}\s|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        for section in section_re.findall(text):
            for match in re.finditer(r"^\s*\d+\.\s+\*\*([^*]+)\*\*", section, re.MULTILINE):
                _add(match.group(1))
            for match in re.finditer(r"^\s*\d+\.\s+([A-Z][^\n]{2,80})$", section, re.MULTILINE):
                _add(match.group(1))

    return found[:30]


def _normalize_component_label(name: str) -> str:
    """Strip markdown noise and parenthetical roles from a component label."""
    name = re.sub(r"[`'\"]", "", name or "")
    name = re.sub(r"\([^)]*\)", "", name).strip()
    name = name.strip("/").strip().rstrip(":")
    return name


def _component_slug(name: str) -> str:
    base = _normalize_component_label(name)
    base = base.replace("/", "-").replace("\\", "-")
    return re.sub(r"\s+", "-", base.lower())


def _component_path_tokens(name: str) -> set[str]:
    """Significant tokens from a component name for path matching."""
    cleaned = _normalize_component_label(name).lower().replace("\\", "/")
    tokens: set[str] = set()
    if cleaned:
        tokens.add(cleaned.replace(" ", "-"))
        tokens.add(_component_slug(name))
    for part in re.split(r"[/.\-\s_]+", cleaned):
        part = part.strip()
        if len(part) >= 2:
            tokens.add(part)
    return {t for t in tokens if t}


def component_reflected_in_paths(component: str, paths: List[str]) -> bool:
    """True when a component slug, path segment, or directory prefix appears in file paths."""
    tokens = _component_path_tokens(component)
    if not tokens:
        return True
    for path in paths:
        normalized = path.lower().replace("\\", "/").replace("_", "-")
        segments = set(re.split(r"[/.\-]+", normalized))
        if tokens & segments:
            return True
        # Path-like contracts: `/pages` → require pages/… or …/pages/…
        for token in tokens:
            if (
                normalized == token
                or normalized.startswith(token + "/")
                or f"/{token}/" in f"/{normalized}"
            ):
                return True
    return False


def component_reflected_in_artifact(
    component: str,
    artifact: str,
    paths: Optional[List[str]] = None,
) -> bool:
    """
    True when a named component appears in a tech stack artifact.

    Checks parsed file paths first, then falls back to slug/token presence in
    the full artifact text (covers directory-only trees).
    """
    path_list = paths or []
    if path_list and component_reflected_in_paths(component, path_list):
        return True
    lower = _normalize(artifact)
    slug = _component_slug(component)
    if slug and slug in lower:
        return True
    tokens = _component_path_tokens(component)
    for token in tokens:
        if len(token) >= 4 and re.search(rf"\b{re.escape(token)}\b", lower):
            return True
    return False


def _manifest_forbidden_violation(
    artifact: str,
    stack_manifest: Dict[str, Any],
) -> Optional[str]:
    """Return a reason when artifact introduces a forbidden tier.

    Technology-agnostic rule:
    - If ``chosen_stack`` already selects something belonging to a tier, that
      tier is unlocked (approved contract selected it) — do not flag it.
    - Otherwise any marker hit for a remaining forbidden tier is a violation.
    """
    artifact_lower = _normalize(artifact)
    chosen = [t.lower() for t in (stack_manifest.get("chosen_stack") or [])]
    forbidden = _effective_forbidden_tiers(
        [t.lower() for t in (stack_manifest.get("forbidden_tiers") or [])],
        chosen,
    )
    for tier in forbidden:
        markers = _tier_markers(tier)
        if any(m in artifact_lower for m in markers):
            return (
                f"Artifact violates locked stack_manifest: introduces forbidden "
                f"tier {tier!r} (chosen_stack={chosen})."
            )
    return None


def _extract_rejected_technologies(solution_spec: str) -> set[str]:
    """Technologies mentioned only as rejected alternatives in an approved solution_spec."""
    rejected: set[str] = set()
    if not (solution_spec or "").strip():
        return rejected

    rejection_lines: List[str] = []
    in_rejection_section = False
    for line in (solution_spec or "").splitlines():
        lower = line.lower()
        if re.search(
            r"non[\s-]?goal|explicit non|rejected alternative|why not|not in scope",
            lower,
        ):
            in_rejection_section = True
            rejection_lines.append(line)
            continue
        if in_rejection_section:
            if re.match(r"^#{1,3}\s+\S", line.strip()) and not re.search(
                r"non[\s-]?goal|explicit non", lower
            ):
                in_rejection_section = False
            else:
                rejection_lines.append(line)
        if any(
            phrase in lower
            for phrase in (
                "language mismatch",
                "rather than port",
                "would require duplicate",
                "operational overhead",
                "duplicate business logic",
                "not in scope for the mvp",
            )
        ):
            rejection_lines.append(line)
        if re.search(r"\|\s*\*\*full .+ stack\*\*", line, re.IGNORECASE):
            rejection_lines.append(line)

    chunk = "\n".join(rejection_lines)
    if chunk.strip():
        rejected.update(_frameworks_in_text(chunk))
        rejected.update(_extract_named_technologies(_normalize(chunk)))
    return rejected


def _extract_chosen_technologies(solution_spec: str) -> Tuple[set[str], set[str]]:
    """Return (frameworks, named tech) that the approved spec actually selects."""
    rejected = _extract_rejected_technologies(solution_spec)
    spec_lower = _normalize(solution_spec)
    frameworks = set(_frameworks_in_text(solution_spec)) - rejected
    techs = set(_extract_named_technologies(spec_lower)) - rejected
    return frameworks, techs


def detect_solution_spec_mismatch(
    solution_spec: str,
    artifact: str,
    *,
    stack_manifest: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Structural validation for human-approved solution_spec.

    Checks named components and locked manifest constraints only — not per-framework
    keyword rules. Stack/framework fidelity is enforced via prompts and the approved
    solution_spec text passed to agents.
    """
    if not (solution_spec or "").strip() or not (artifact or "").strip():
        return None

    artifact_lower = _normalize(artifact)

    if stack_manifest:
        manifest_violation = _manifest_forbidden_violation(artifact, stack_manifest)
        if manifest_violation:
            return manifest_violation

    services = _extract_service_components(solution_spec)
    components = extract_named_components(solution_spec)
    names_to_check = list(dict.fromkeys(components + services))
    if names_to_check:
        artifact_paths = re.findall(
            r"[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+",
            artifact,
        )
        reflected = sum(
            1 for name in names_to_check
            if component_reflected_in_artifact(name, artifact, artifact_paths)
        )
        if reflected < max(1, len(names_to_check) // 2):
            return (
                "Approved solution_spec defines named components "
                f"{names_to_check}, but the artifact only reflects "
                f"{reflected}/{len(names_to_check)} of them."
            )

    return None


def format_approved_solution_contract(solution_spec: str) -> str:
    """Prompt section marking solution_spec as human-reviewed and binding."""
    spec = (solution_spec or "").strip()
    if not spec:
        return ""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "APPROVED SOLUTION SPEC (BINDING — human reviewed)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "The user reviewed and approved this architecture during solution review.\n"
        "Your output MUST implement it exactly — same technologies, services, and\n"
        "folder layout. Do NOT simplify to a monolith or substitute frameworks.\n\n"
        f"{spec}\n"
    )


def build_stack_selection_brief(
    vision: str,
    user_stories: str = "",
    *,
    approved_solution: bool = False,
) -> str:
    """Structured constraints for Designer / Tech Architect prompts."""
    profile = infer_capability_profile(vision, user_stories)
    lines = [
        "STACK SELECTION BRIEF (derived from vision — binding constraints):",
        f"- Delivery surface: {profile.delivery_surface}",
        f"- Complexity tier: {profile.complexity}",
    ]

    if profile.explicit_technologies:
        lines.append(
            "- Named in vision: "
            + ", ".join(profile.explicit_technologies)
            + " — use these; do not substitute."
        )
    else:
        lines.append(
            "- No application framework named in vision — select the MINIMAL stack "
            "that satisfies the described deliverable. Do not assume a CMS, ERP, "
            "or full application platform by default."
        )

    caps: List[str] = []
    if profile.needs_persistence:
        caps.append("persistent data storage")
    if profile.needs_api:
        caps.append("HTTP/API layer")
    if profile.needs_auth:
        caps.append("authentication/authorization")
    if profile.has_client_surface:
        caps.append("client/UI presentation")
    if caps:
        lines.append("- Required capabilities: " + "; ".join(caps))
    else:
        lines.append("- Required capabilities: presentation/content only (no backend tiers inferred)")

    if not profile.needs_server_runtime:
        lines.append(
            "- Do NOT add application servers, databases, ORMs, CMS platforms, "
            "or multi-tier backends unless the vision explicitly requires them."
        )

    if approved_solution:
        lines.append(
            "- APPROVED SOLUTION SPEC is binding (human reviewed). Implement its "
            "architecture and technologies exactly — do NOT simplify or substitute."
        )
        lines.append(
            "- design_spec and stack_manifest must stay consistent with the approved "
            "solution_spec; the vision does not override an approved architecture."
        )
    else:
        lines.append(
            "- The PROJECT VISION outranks the design specification for stack breadth. "
            "If design_spec over-scopes, simplify the stack to match the vision."
        )
        lines.append(
            "- Apply framework-specific skills ONLY when the vision (or justified "
            "capabilities above) requires that framework — never because it appeared "
            "in an upstream design draft."
        )

    if profile.evidence:
        lines.append("- Inference evidence: " + "; ".join(profile.evidence[:5]))

    return "\n".join(lines)
