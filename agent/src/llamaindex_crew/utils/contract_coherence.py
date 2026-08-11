"""
One project, one layout: reject wiring contracts that declare two of them.

``_build_path_only_contract_from_specs`` builds the contract by harvesting every
path-looking token out of solution_spec.md, design_spec.md and tech_stack.md and
turning each parent directory into a package. It is a union with no conflict
resolution. Specs are written at different times, by different agents, at
different fidelity — so when an early one sketches a layout the final one does
not use, the contract declares BOTH, and every consumer downstream treats that
as the plan of record:

    declared source file missing: 'expense_tracker/api.py' is in
    wiring_contract.packages but not on disk

The remediation loop then creates it. The duplicate tree is not a failure of the
loop; it is the loop correctly executing an incoherent contract. On job 25539373
the agent even wrote *"The file structure shows no expense_tracker folder; it's
app"* in its reasoning and complied anyway, because the contract said so.

This is not specific to the path seed. ``sandbox_full_output`` was produced by a
**jq-patch** and still declared::

    internal/sandbox -> janitor.go, manager.go, podman.go
    internal/service -> janitor.go, manager.go, podman.go

Nor to a language — the same shape appeared in Python (app/ vs expense_tracker/)
and Java (the Maven tree vs root-level model/, controller/, service/). So the
check lives at the write boundary, where seed, jq-patch, prose-strengthening and
tldr enrichment all converge, instead of in any one producer.

**The discriminator is structural, not a keyword list.** Two packages compete
when their source-file *basenames* overlap. A real decomposition does not
duplicate filenames across roots — ``cmd/main.go`` and ``internal/api/handler.go``
share nothing — while a duplicate tree does so by definition. That holds in every
language, costs one set intersection, and spends none of a 14b's scarce and
unreliable reasoning on a judgement it has already been observed to get wrong.

Deliberately biased toward false negatives. Leaving a rare genuine duplicate in
place is recoverable; deleting a package a real project needs is not. Hence the
sibling rule below: packages under a shared parent are a deliberate
decomposition, so they need more evidence (two duplicated names, not one) before
they are treated as rivals.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Kept in sync with wiring_contract._SOURCE_SUFFIXES; imported lazily below so
# this module stays importable on its own.
_FALLBACK_SOURCE_SUFFIXES = frozenset({
    ".go", ".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".kt", ".scala", ".sc",
    ".rs", ".rb", ".php", ".cs", ".c", ".cpp", ".h", ".hpp",
    # Web delivery surfaces — a static site's duplicate tree is two index.html
    # files just as surely as a Go one is two manager.go files.
    ".html", ".css", ".svg",
})

# A package must hold at least this many source files before its basenames are
# treated as evidence of a layout. Below it there is not enough signal to tell a
# sketch from a legitimate single-purpose package — except in the fully-subsumed
# case, which is handled explicitly.
_MIN_OVERLAP_FOR_SIBLINGS = 2
_MIN_OVERLAP_RATIO = 0.5


@dataclass
class CompetingPackages:
    """Two packages that declare the same concern under different roots."""

    keep: str
    drop: str
    shared: List[str] = field(default_factory=list)

    def reason(self) -> str:
        names = ", ".join(sorted(self.shared)[:5])
        return (
            f"competing package '{self.drop}' declares the same files as "
            f"'{self.keep}' ({names}) — a project has one layout, so '{self.drop}' "
            f"was dropped from the wiring contract before codegen"
        )


def _source_suffixes() -> frozenset:
    try:
        # local imports avoid a cycle
        from .wiring_contract import _SOURCE_SUFFIXES, _WEB_DELIVERY_SUFFIXES
        return frozenset(_SOURCE_SUFFIXES | _WEB_DELIVERY_SUFFIXES)
    except Exception:  # noqa: BLE001 — coherence must never break the import graph
        return _FALLBACK_SOURCE_SUFFIXES


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").strip().lstrip("/")


def _basenames(files: Any, suffixes: frozenset) -> Set[str]:
    """Source-file basenames declared by a package; non-source files are ignored."""
    out: Set[str] = set()
    if not isinstance(files, (list, tuple, set)):
        return out
    for f in files:
        if not isinstance(f, str):
            continue
        p = _norm(f)
        if not p:
            continue
        if Path(p).suffix.lower() in suffixes:
            out.add(Path(p).name)
    return out


def _is_nested(a: str, b: str) -> bool:
    """True when one package path contains the other (a decomposition, never a rival)."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _parent(pkg: str) -> str:
    return str(Path(pkg).parent).replace("\\", "/")


def _competes(
    a: str,
    b: str,
    files_a: Set[str],
    files_b: Set[str],
    packages: Any = None,
) -> Optional[Set[str]]:
    """Return the shared basenames when *a* and *b* are rival layouts, else None."""
    if not files_a or not files_b or _is_nested(a, b):
        return None

    shared = files_a & files_b
    if not shared:
        return None

    ratio = len(shared) / min(len(files_a), len(files_b))

    # Siblings under a real shared namespace are a deliberate decomposition
    # (internal/sse/writer.go beside internal/log/writer.go), so they need more
    # than one coincidental filename. The top level is NOT such a namespace —
    # every root package has parent "." by construction, and reading that as a
    # decomposition let job 1cec01ad keep `api -> main.py` and `model ->
    # models.py` beside the root package that already declared both.
    if _parent(a) == _parent(b) != ".":
        if len(shared) >= _MIN_OVERLAP_FOR_SIBLINGS and ratio >= _MIN_OVERLAP_RATIO:
            return shared
        return None

    # Several duplicated names is duplication however you read it.
    if len(shared) >= _MIN_OVERLAP_FOR_SIBLINGS and ratio >= _MIN_OVERLAP_RATIO:
        return shared

    # One package wholly contained in the other on a single filename. Real when
    # something separates them — the Java sketch model/Task.java against a
    # three-file Maven tree, or `configuration` standing alone against
    # `internal/config` among four other `internal/` packages. But
    # frontend/index.js against backend/index.js is symmetric in every respect,
    # and dropping either would be a coin flip dressed up as a decision.
    if ratio >= 1.0:
        if len(files_a) != len(files_b):
            return shared
        if _root_dominance(packages, a) != _root_dominance(packages, b):
            return shared
    return None


def _dep_edge_count(contract: dict, pkg: str) -> int:
    """How many dependency edges the contract draws to or from this package.

    Symbol ownership is deliberately **not** counted. ``_guess_package_for_symbol``
    falls back to ``next(iter(sorted(packages)))`` for anything it cannot
    attribute, so a package that happens to sort early collects every orphan.
    In the ``sandbox_full_output`` contract that gave the phantom
    ``configuration`` package 11 symbols — including ``configuration.Fatal``,
    ``configuration.Info`` and ``configuration.ListenAndServe``, which are
    stdlib calls — against 5 for the real ``internal/config``. Ranking on that
    picks the phantom every time.

    Dep edges carry no such fallback: something had to name both endpoints. All
    four in that contract pointed at ``internal/config``, and none at
    ``configuration``.
    """
    count = 0
    for dep in contract.get("deps") or []:
        if not isinstance(dep, dict):
            continue
        if _norm(dep.get("from") or "") == pkg or _norm(dep.get("to") or "") == pkg:
            count += 1
    return count


def _source_files(files: Any, suffixes: frozenset) -> List[str]:
    """Declared source paths only — a README is not evidence of a real layout."""
    if not isinstance(files, (list, tuple, set)):
        return []
    return [
        _norm(f) for f in files
        if isinstance(f, str) and Path(_norm(f)).suffix.lower() in suffixes
    ]


def _on_disk_count(workspace: Optional[Path], files: List[str]) -> int:
    if not workspace:
        return 0
    root = Path(workspace)
    return sum(1 for f in files if (root / f).is_file())


def _top_root(pkg: str) -> str:
    return _norm(pkg).split("/", 1)[0]


def _root_dominance(packages: Any, pkg: str) -> int:
    """How many packages share this one's top-level root.

    The layout the rest of the project already uses is the real one. In the
    ``sandbox_full_output`` contract, ``internal/config`` sits beside
    ``internal/api``, ``internal/sandbox`` and ``internal/util`` while
    ``configuration`` stands alone — and it was ``internal/config`` that
    survived into the finished project.
    """
    if not isinstance(packages, dict):
        return 0
    root = _top_root(pkg)
    return sum(1 for other in packages if isinstance(other, str) and _top_root(other) == root)


def _rank(
    contract: dict,
    pkg: str,
    files: Any,
    workspace: Optional[Path],
    suffixes: frozenset,
) -> Tuple:
    """Sort key for choosing the survivor. Higher wins; last element breaks ties."""
    sources = _source_files(files, suffixes)
    return (
        # 1. What was actually built is ground truth once codegen has run.
        _on_disk_count(workspace, sources),
        # 2. How much of the project this package actually holds. A package
        #    declaring eight files is the layout; one that declares a single
        #    file already covered by it is an alias for part of it.
        len(sources),
        # 3. The layout root the rest of the project is organised around.
        _root_dominance(contract.get("packages"), pkg),
        # 4. What the contract's dependency graph relies on. Below size on
        #    purpose: job 1cec01ad's jq patch gave the aliasing layer packages
        #    (api -> main.py) dep edges the eight-file root package did not
        #    have, and ranking deps first dropped the whole application.
        _dep_edge_count(contract, pkg),
        # 5. Deterministic final tiebreak — negated so the smaller name wins.
        tuple(-ord(c) for c in pkg),
    )


def find_competing_packages(
    contract: Optional[dict],
    workspace: Optional[Path] = None,
) -> List[CompetingPackages]:
    """Report rival layouts in *contract*, resolved to a keep/drop decision.

    Side-effect free: callers that only want to warn can use this directly.
    """
    if not isinstance(contract, dict):
        return []
    packages = contract.get("packages")
    if not isinstance(packages, dict) or len(packages) < 2:
        return []

    suffixes = _source_suffixes()
    names: Dict[str, Set[str]] = {}
    for pkg, data in packages.items():
        if not isinstance(pkg, str) or not isinstance(data, dict):
            continue
        basenames = _basenames(data.get("files"), suffixes)
        if basenames:
            names[_norm(pkg)] = basenames

    groups: List[CompetingPackages] = []
    dropped: Set[str] = set()

    # Sorted so the outcome does not depend on dict ordering.
    ordered = sorted(names)
    for i, a in enumerate(ordered):
        if a in dropped:
            continue
        for b in ordered[i + 1:]:
            if b in dropped:
                continue
            shared = _competes(a, b, names[a], names[b], packages)
            if not shared:
                continue
            rank_a = _rank(contract, a, packages.get(a, {}).get("files"), workspace, suffixes)
            rank_b = _rank(contract, b, packages.get(b, {}).get("files"), workspace, suffixes)
            keep, drop = (a, b) if rank_a >= rank_b else (b, a)
            groups.append(CompetingPackages(keep=keep, drop=drop, shared=sorted(shared)))
            dropped.add(drop)
            if drop == a:
                break  # `a` lost; stop comparing it against the rest

    return groups


def resolve_competing_packages(
    contract: Optional[dict],
    workspace: Optional[Path] = None,
) -> Tuple[Optional[dict], List[str]]:
    """
    Return ``(coherent_contract, drop_reasons)``.

    Drops the losing side of each rival pair along with the symbols and dep
    edges that referenced it — a dangling edge would be rendered into the next
    prompt as though it were fact.

    Never raises: an unparseable contract is returned untouched, because failing
    a job over a coherence check would be a worse outcome than the duplicate
    tree it prevents.
    """
    if not isinstance(contract, dict):
        return contract, []

    try:
        groups = find_competing_packages(contract, workspace)
    except Exception as exc:  # noqa: BLE001 — must never fail a job
        logger.debug("Contract coherence check errored: %s", exc)
        return contract, []

    if not groups:
        return contract, []

    doomed = {g.drop for g in groups}
    contract = copy.deepcopy(contract)
    packages = contract.get("packages") or {}
    contract["packages"] = {
        pkg: data for pkg, data in packages.items() if _norm(pkg) not in doomed
    }

    symbols = contract.get("symbols")
    if isinstance(symbols, dict):
        contract["symbols"] = {
            key: val for key, val in symbols.items()
            if not (isinstance(val, dict) and _norm(val.get("package") or "") in doomed)
        }

    deps = contract.get("deps")
    if isinstance(deps, list):
        contract["deps"] = [
            dep for dep in deps
            if not (
                isinstance(dep, dict)
                and (_norm(dep.get("from") or "") in doomed or _norm(dep.get("to") or "") in doomed)
            )
        ]

    reasons = [g.reason() for g in groups]
    for reason in reasons:
        logger.warning("[wiring] %s", reason)
    return contract, reasons
