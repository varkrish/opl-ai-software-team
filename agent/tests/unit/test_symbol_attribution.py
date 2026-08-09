"""
A symbol with no evidence of ownership must not be assigned to a package.

``_guess_package_for_symbol`` ends in::

    if packages:
        return next(iter(sorted(packages.keys())))

so every signature-shaped line the prose scanner finds — including ones inside
illustrative code blocks — is attributed to whichever package happens to sort
first. It is then written into that package's ``owns`` list, which codegen reads
as "this package must define these".

In ``sandbox_full_output/wiring_contract.json`` the package ``configuration``
ended up owning::

    LoadConfig, MustLoadConfig, main, NewProduction, Sync, NewService,
    Fatal, NewRouter, RegisterRoutes, Info, ListenAndServe

Six of those are stdlib or third-party call sites lifted out of a ``func main()``
example — ``log.Fatal``, ``logger.Info``, ``http.ListenAndServe``,
``zap.NewProduction``, ``logger.Sync``, ``chi.NewRouter``. One signature was
stored verbatim as ``Fatal("failed to create service", zap.Error(err))``, which
is a call, not a declaration. The contract instructed the model to implement the
Go standard library inside its config package.

Ranking is contaminated by this too: ``configuration`` collected 11 symbols
against 5 for the real ``internal/config``, so any survivor rule that counts
symbol ownership picks the phantom (see contract_coherence, which counts dep
edges instead).

The fix is the same principle as the package-coherence gate one level down:
**state only what the evidence supports.** A symbol found under a package
heading belongs to it; a boundary symbol belongs to the boundary package; when
there is exactly one package there is no ambiguity. Otherwise the symbol is
dropped, and the contract is honestly weak rather than confidently wrong — which
``_repair_wiring_planned_emit`` already handles by asking for a real answer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.wiring_contract import (  # noqa: E402
    extract_planned_interfaces_from_specs,
)


GO_MAIN_EXAMPLE = """
## Entrypoint

```go
func main() {
    cfg := configuration.Load()
    logger, _ := zap.NewProduction()
    defer logger.Sync()
    r := chi.NewRouter()
    api.RegisterRoutes(r)
    log.Fatal(http.ListenAndServe(":8080", r))
}
```
"""

MULTI_PACKAGES = {
    "configuration": {"files": ["configuration/config.go"], "owns": []},
    "internal/api": {"files": ["internal/api/handler.go"], "owns": []},
    "internal/config": {"files": ["internal/config/config.go"], "owns": []},
}


def test_unattributable_symbol_is_dropped_not_guessed():
    """The live repro: `func main()` became `configuration.main`."""
    planned = extract_planned_interfaces_from_specs(
        GO_MAIN_EXAMPLE, packages=MULTI_PACKAGES
    )

    assert not any(k.endswith(".main") for k in planned["symbols"]), (
        "main() belongs to no package here; guessing one fabricates ownership"
    )
    assert "main" not in (planned["owns_by_package"].get("configuration") or [])


def test_nothing_lands_in_the_alphabetically_first_package_by_default():
    """`configuration` won only because it sorts before `internal/*`."""
    planned = extract_planned_interfaces_from_specs(
        GO_MAIN_EXAMPLE, packages=MULTI_PACKAGES
    )

    assert not (planned["owns_by_package"].get("configuration") or [])


def test_a_package_heading_is_evidence_and_is_honoured():
    text = """
## internal/api
func CreateSandbox(ctx context.Context, image string) (string, error)
"""
    planned = extract_planned_interfaces_from_specs(text, packages=MULTI_PACKAGES)

    assert "internal/api.CreateSandbox" in planned["symbols"]
    assert "CreateSandbox" in planned["owns_by_package"]["internal/api"]


def test_a_boundary_symbol_still_reaches_the_boundary_package():
    """*Handler / *Controller naming is real evidence, not a coin flip."""
    text = "func CreateSandboxHandler(mgr *Manager) http.HandlerFunc\n"
    planned = extract_planned_interfaces_from_specs(text, packages=MULTI_PACKAGES)

    owners = [pkg for pkg, names in planned["owns_by_package"].items()
              if "CreateSandboxHandler" in names]
    assert owners == ["internal/api"]


def test_a_single_package_project_has_no_ambiguity_to_resolve():
    text = "def create_order(user_id: str) -> Order:\n"
    packages = {"app": {"files": ["app/services.py"], "owns": []}}

    planned = extract_planned_interfaces_from_specs(text, packages=packages)

    assert "app.create_order" in planned["symbols"]


def test_headings_scope_only_what_follows_them():
    """A heading must not retroactively adopt symbols declared before it."""
    text = """
func orphan_before_any_heading()

## internal/api
func CreateSandbox(ctx context.Context) error
"""
    planned = extract_planned_interfaces_from_specs(text, packages=MULTI_PACKAGES)

    assert "internal/api.CreateSandbox" in planned["symbols"]
    assert not any("orphan_before_any_heading" in k for k in planned["symbols"])


def test_no_packages_declared_yields_no_symbols():
    planned = extract_planned_interfaces_from_specs(
        "func Whatever()\n", packages={}
    )
    assert planned["symbols"] == {}
    assert planned["owns_by_package"] == {}


def test_call_sites_are_not_recorded_as_declarations():
    """
    `Fatal("failed to create service", zap.Error(err))` was stored verbatim as a
    planned signature. Whatever the heading says, a call is not a declaration.
    """
    text = """
## internal/api
logger.Fatal("failed to create service", zap.Error(err))
logger.Info("starting server", zap.String("addr", cfg.Server.Address))
"""
    planned = extract_planned_interfaces_from_specs(text, packages=MULTI_PACKAGES)

    assert not any(k.endswith((".Fatal", ".Info")) for k in planned["symbols"]), (
        f"call sites leaked in as declarations: {sorted(planned['symbols'])}"
    )
