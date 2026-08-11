"""
Tell infrastructure failures apart from code failures.

The remediation loop has exactly one recovery strategy: hand the failure to
DevAgent and ask it to rewrite the code. That is right for a genuine bug and
useless for "this container has no network" — the code was never wrong, so
every iteration rewrites correct source, burns budget, and the job still ends
up ``partially_completed`` with an issue count that reads like a code-quality
problem.

Seen live: a Java job reported "42 issues" that were really one Maven error
(``Could not create local repository`` on the read-only sandbox root) fed back
through the loop repeatedly.

**Bias: false negatives over false positives.** Misreading a real compile error
as "infrastructure" would silently suppress a bug the loop should have fixed.
Misreading infrastructure as code merely wastes iterations, which is the status
quo. So every pattern here must be unambiguous — a phrase that a correct
program cannot produce. Anything arguable is deliberately left out.
"""
from __future__ import annotations

import re
from typing import List, NamedTuple, Optional

# Each entry: (regex, short operator-facing reason).
# Only signatures that cannot be emitted by broken *code* belong here.
_INFRA_PATTERNS: List[tuple] = [
    # ── Sandbox / platform wiring ────────────────────────────────────────────
    (r"No container image configured for project type",
     "No container image mapped for this project type"),
    (r"Sandbox API (?:error|smoke test error)",
     "Sandbox API returned an error"),
    (r"SANDBOX_API_URL is not set",
     "Sandbox API URL not configured"),
    (r"could not retrieve logs",
     "Sandbox logs unavailable"),

    # ── Read-only root / unwritable cache dirs ───────────────────────────────
    (r"Could not create local repository",
     "Maven cannot create its local repository (read-only sandbox root)"),
    (r"LocalRepositoryNotAccessibleException",
     "Maven local repository not accessible"),
    (r"Read-only file system",
     "Write attempted on a read-only filesystem"),
    (r"Could not create parent directories",
     "Cannot create directories (read-only filesystem)"),

    # ── Network egress blocked (--net none) ──────────────────────────────────
    (r"Name or service not known",
     "DNS resolution failed (no network in sandbox)"),
    (r"Temporary failure in name resolution",
     "DNS resolution failed (no network in sandbox)"),
    (r"Network is unreachable",
     "Network unreachable (no egress from sandbox)"),
    (r"Could not transfer artifact",
     "Cannot reach the package registry (no egress from sandbox)"),
    (r"Could not resolve dependencies",
     "Dependency download failed (no egress from sandbox)"),
    (r"ENOTFOUND|EAI_AGAIN",
     "npm cannot reach the registry (no egress from sandbox)"),
    (r"dial tcp .*: (?:connect: )?(?:connection refused|i/o timeout)",
     "Go module proxy unreachable (no egress from sandbox)"),
    (r"proxy\.golang\.org.*(?:timeout|refused|unreachable)",
     "Go module proxy unreachable (no egress from sandbox)"),
    (r"Connection refused",
     "Connection refused reaching a required service"),

    # ── Toolchain missing entirely ───────────────────────────────────────────
    (r"(?:gradle|mvn|npm|go|python3?): (?:command )?not found",
     "Required build tool is missing from the container image"),
    (r"command not found: (?:gradle|mvn|npm|go)",
     "Required build tool is missing from the container image"),
]

_COMPILED: List[tuple] = [(re.compile(p, re.IGNORECASE), reason)
                          for p, reason in _INFRA_PATTERNS]


class FailureKind(NamedTuple):
    """Result of classifying one failure message."""

    is_infrastructure: bool
    reason: Optional[str] = None

    @property
    def is_code(self) -> bool:
        return not self.is_infrastructure


def classify_failure(output: str) -> FailureKind:
    """
    Classify a build/test failure as infrastructure or code.

    Returns ``is_infrastructure=True`` only on an unambiguous platform
    signature; everything else — including empty or unrecognised output — is
    treated as a code failure so the normal fix loop still runs.
    """
    if not output or not isinstance(output, str):
        return FailureKind(False)

    for pattern, reason in _COMPILED:
        if pattern.search(output):
            return FailureKind(True, reason)

    return FailureKind(False)


def is_infrastructure_failure(output: str) -> bool:
    """Convenience wrapper for call sites that only need the boolean."""
    return classify_failure(output).is_infrastructure
