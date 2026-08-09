"""
Deterministic validation and repair of build manifests.

A malformed manifest breaks *every* source file, so the per-file remediation
loop rewrites correct code around a project the build tool cannot parse. The
loop cannot converge on this class of defect because the failure is never
attributed to the file that actually contains it.

Observed live (job d32dcaf7): a generated ``pom.xml`` used
``${spring.boot.version}`` as the **parent** version. Maven resolves the parent
*before* the child's ``<properties>`` are available, so it requested a literal
``${spring.boot.version}`` from Central and got a 404. The loop then ran
48 → 67 → 52 → 56 issues over four iterations without ever touching pom.xml,
while the DevAgent misread ``Could not find artifact … in central`` as "no
internet" and began reasoning toward deleting the dependency list.

Neither half of that needed a model. An unexpanded ``${...}`` inside a resolved
coordinate cannot occur in a correct build, and the repair is a lookup in
``<properties>``. Manifest parsing is a solved problem in every language's
standard library — this module uses it instead of spending a 14b's scarce and
unreliable reasoning on a diagnosis it demonstrably gets wrong.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

_MAVEN_NS = "{http://maven.apache.org/POM/4.0.0}"
_PROPERTY_REF = re.compile(r"\$\{([^}]+)\}")


@dataclass
class ManifestDefect:
    """A build manifest that is invalid, not merely incomplete."""

    file: str
    description: str
    # True when this module can fix it deterministically; False means it needs
    # the model (e.g. arbitrarily malformed JSON, which has no single repair).
    repairable: bool


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find_child(element, name: str):
    for child in element:
        if _strip_ns(child.tag) == name:
            return child
    return None


def _pom_properties(root) -> dict:
    props = {}
    node = _find_child(root, "properties")
    if node is None:
        return props
    for child in node:
        props[_strip_ns(child.tag)] = (child.text or "").strip()
    return props


def repair_pom_parent_version(pom_path: Path) -> List[str]:
    """
    Replace property references in the ``<parent>`` block with their literal
    values. Returns a description of each repair made (empty when none).

    **Only the ``<parent>`` block is touched.** ``${...}`` in a *dependency*
    version is idiomatic, valid Maven — those resolve after the model is built.
    Rewriting every coordinate would corrupt correct POMs, so this is
    deliberately narrow.

    Never raises: a manifest too broken to parse is reported by
    :func:`validate_manifests`, not repaired here.
    """
    pom_path = Path(pom_path)
    if not pom_path.is_file():
        return []

    try:
        text = pom_path.read_text(encoding="utf-8", errors="replace")
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, OSError, UnicodeDecodeError):
        return []

    parent = _find_child(root, "parent")
    if parent is None:
        return []

    props = _pom_properties(root)
    repairs: List[str] = []
    new_text = text

    for field in ("groupId", "artifactId", "version"):
        node = _find_child(parent, field)
        if node is None or not node.text:
            continue
        raw = node.text.strip()
        match = _PROPERTY_REF.fullmatch(raw)
        if not match:
            continue
        prop_name = match.group(1)
        literal = props.get(prop_name)
        if not literal:
            # No definition to substitute — escalate rather than invent one.
            logger.warning(
                "pom.xml <parent><%s> references ${%s} which is not defined in "
                "<properties>; leaving for the fix loop",
                field, prop_name,
            )
            continue
        # Textual replacement scoped to the <parent> block so an identical
        # property reference elsewhere (legal) is untouched.
        head, sep, tail = new_text.partition("</parent>")
        if not sep:
            continue
        head = head.replace(f"<{field}>{raw}</{field}>", f"<{field}>{literal}</{field}>")
        new_text = head + sep + tail
        repairs.append(
            f"pom.xml: <parent><{field}> was '{raw}' — Maven resolves the parent "
            f"before <properties> exist, so it cannot be a property reference. "
            f"Substituted the literal '{literal}'."
        )

    if repairs:
        try:
            pom_path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write repaired pom.xml: %s", exc)
            return []
    return repairs


def _check_pom(workspace: Path) -> Optional[ManifestDefect]:
    pom = workspace / "pom.xml"
    if not pom.is_file():
        return None
    try:
        text = pom.read_text(encoding="utf-8", errors="replace")
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, OSError, UnicodeDecodeError) as exc:
        return ManifestDefect("pom.xml", f"pom.xml is not valid XML: {exc}", repairable=False)

    parent = _find_child(root, "parent")
    if parent is None:
        return None
    for field in ("groupId", "artifactId", "version"):
        node = _find_child(parent, field)
        if node is None or not node.text:
            continue
        match = _PROPERTY_REF.fullmatch(node.text.strip())
        if not match:
            continue
        defined = match.group(1) in _pom_properties(root)
        return ManifestDefect(
            "pom.xml",
            (
                f"pom.xml <parent><{field}> uses the property reference "
                f"'{node.text.strip()}'. Maven resolves the parent POM before the "
                f"project's own <properties> are available, so the reference is "
                f"sent to the repository verbatim and cannot resolve. This is a "
                f"POM defect, not a repository or connectivity problem. "
                f"The parent coordinate must be a literal value."
            ),
            repairable=defined,
        )
    return None


def _check_package_json(workspace: Path) -> Optional[ManifestDefect]:
    path = workspace / "package.json"
    if not path.is_file():
        return None
    try:
        json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        return ManifestDefect(
            "package.json",
            f"package.json is not valid JSON: {exc}. Every module load fails "
            f"until this parses.",
            repairable=False,
        )
    return None


def _check_pyproject(workspace: Path) -> Optional[ManifestDefect]:
    path = workspace / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        import tomllib
    except ModuleNotFoundError:  # py<3.11
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return None
    try:
        tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 — TOMLDecodeError varies by backend
        return ManifestDefect(
            "pyproject.toml",
            f"pyproject.toml is not valid TOML: {exc}. The build backend cannot "
            f"read the project until this parses.",
            repairable=False,
        )
    return None


# ── unresolvable version pins ───────────────────────────────────────────────
#
# A test command like ``pip install -r requirements.txt && pytest tests`` fails
# closed: when the install fails, ``&&`` short-circuits and no test ever runs.
# The runner exits non-zero, ``_run_feature_test_bed_loop`` reads that as "tests
# failed", and DevAgent is sent to patch test code for three rounds over a
# one-line version pin. Job 107b3d3e spent its whole test budget that way.
#
# Detection is unambiguous, and pip prints the resolvable versions in the same
# message, so the repair is a lookup in the error text rather than a judgement.

_PIP_UNRESOLVABLE_RE = re.compile(
    r"Could not find a version that satisfies the requirement\s+"
    r"(?P<pkg>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*"
    r"(?:==|>=|<=|~=|!=)?\s*(?P<req>[A-Za-z0-9._*+-]+)?"
    r"[^\n]*?\(from versions:\s*(?P<avail>[^)]*)\)",
)
# npm names the package but never lists candidates.
_NPM_NOTARGET_RE = re.compile(
    r"No matching version found for\s+(?P<pkg>@?[A-Za-z0-9._/-]+?)@(?P<req>[^\s.]+[^\s]*?)\.?\s*$",
    re.MULTILINE,
)

_PRERELEASE_RE = re.compile(r"[A-Za-z]")


@dataclass
class DependencyDefect:
    """A pinned dependency version that the registry does not offer."""

    package: str
    requested: str
    available: List[str]
    ecosystem: str
    # False when the registry offered no alternatives — which usually means the
    # index was unreachable, not that the pin is wrong. Guessing there would
    # rewrite a correct manifest during a network outage.
    repairable: bool


def _version_key(version: str) -> tuple:
    parts = []
    for chunk in re.split(r"[._-]", version):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, 0))
    return tuple(parts)


def _newest_stable(versions: List[str]) -> Optional[str]:
    stable = [v for v in versions if not _PRERELEASE_RE.search(v)]
    pool = stable or []
    if not pool:
        return None
    return max(pool, key=_version_key)


def detect_dependency_resolution_failure(output: Optional[str]) -> Optional[DependencyDefect]:
    """Identify an unresolvable version pin in runner output, or None.

    Deliberately narrow. A genuine assertion failure, an empty log, or a
    network outage must all fall through to the normal paths — this only fires
    on a registry saying, in so many words, that the requested version does not
    exist.
    """
    if not output or not isinstance(output, str):
        return None

    match = _PIP_UNRESOLVABLE_RE.search(output)
    if match:
        raw = (match.group("avail") or "").strip()
        # Tokens can be truncated when the log is clipped mid-list; keep only
        # well-formed versions so a partial "0.3" tail cannot be selected.
        available = [
            tok.strip() for tok in raw.split(",")
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+)+[A-Za-z0-9.]*", tok.strip())
        ]
        return DependencyDefect(
            package=match.group("pkg"),
            requested=(match.group("req") or "").strip(),
            available=available,
            ecosystem="pip",
            repairable=bool(available and _newest_stable(available)),
        )

    npm = _NPM_NOTARGET_RE.search(output)
    if npm:
        return DependencyDefect(
            package=npm.group("pkg"),
            requested=npm.group("req"),
            available=[],
            ecosystem="npm",
            repairable=False,
        )
    return None


def repair_unresolvable_pins(workspace: Path, output: Optional[str]) -> List[str]:
    """Rewrite an impossible pin to the newest version the registry offers.

    Returns one description per repair, empty when there is nothing to do.
    Idempotent: a manifest already holding the chosen version reports no repair,
    so the caller can retry the run without spinning.

    Never raises — a repair gap must cost a retry, never a job.
    """
    defect = detect_dependency_resolution_failure(output)
    if not defect or not defect.repairable or defect.ecosystem != "pip":
        return []

    target = _newest_stable(defect.available)
    if not target:
        return []

    workspace = Path(workspace)
    try:
        candidates = sorted(workspace.rglob("requirements*.txt"))
    except OSError:
        return []

    repairs: List[str] = []
    # Match the package at line start, tolerating extras and any comparator.
    pin_re = re.compile(
        rf"^(?P<name>{re.escape(defect.package)}(?:\[[^\]]*\])?)\s*"
        rf"(?:==|>=|<=|~=|!=)\s*(?P<ver>[^\s;#]+)",
        re.IGNORECASE | re.MULTILINE,
    )

    for req_file in candidates:
        if any(part in {"node_modules", ".venv", "venv", ".git"} for part in req_file.parts):
            continue
        try:
            text = req_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        found = pin_re.search(text)
        if not found or found.group("ver") == target:
            continue

        new_text = pin_re.sub(lambda m: f"{m.group('name')}=={target}", text, count=1)
        try:
            req_file.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write repaired %s: %s", req_file.name, exc)
            continue

        rel = req_file.name
        try:
            rel = str(req_file.relative_to(workspace))
        except ValueError:
            pass
        repairs.append(
            f"{rel}: '{defect.package}=={found.group('ver')}' does not exist on the "
            f"index (offered: {', '.join(defect.available[:6])}). Pinned "
            f"'{defect.package}=={target}'. The install failed before any test "
            f"ran, so this was not a test failure."
        )

    for repair in repairs:
        logger.warning("[deps] %s", repair)
    return repairs


def validate_manifests(workspace: Path) -> Optional[ManifestDefect]:
    """
    Return the first manifest defect found, or None.

    Checks *validity*, not completeness — ``_auto_fix_issues`` already handles
    missing entries (requirements.txt, package.json deps, pom dependencies).
    The gap this closes is a manifest that is present but unparseable or
    semantically impossible, which breaks every file in the project at once.

    A workspace with no manifest is not a defect: static-HTML and script
    projects legitimately have none.
    """
    workspace = Path(workspace)
    for check in (_check_pom, _check_package_json, _check_pyproject):
        try:
            defect = check(workspace)
        except Exception as exc:  # noqa: BLE001 — validation must never fail a job
            logger.debug("Manifest check %s errored: %s", check.__name__, exc)
            continue
        if defect:
            return defect
    return None
