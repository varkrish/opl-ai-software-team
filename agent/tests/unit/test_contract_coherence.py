"""
A wiring contract must describe ONE project layout, not the union of every
layout any spec ever sketched.

``_build_path_only_contract_from_specs`` harvests path-looking tokens from
solution_spec.md + design_spec.md + tech_stack.md and makes a package out of
every parent directory it finds. It is a union with no conflict resolution, so
when an early spec sketches one layout and the final one uses another, the
contract declares BOTH. Everything downstream then faithfully amplifies it:
reconciliation reports the phantom files as "declared source file missing", the
fix loop creates them, and the job ships a duplicate tree that is
contract-compliant by construction.

Three real contracts on disk show this is neither language- nor producer-
specific — the Go one below came from a jq-patch, not the path seed:

  sandbox_full_output/wiring_contract.json  (module my-sandbox-api, jq-patch)
      internal/sandbox -> janitor.go, manager.go, podman.go
      internal/service -> janitor.go, manager.go, podman.go   <- same file set

  Python job 25539373 (live)
      app              -> main.py, models.py, schemas.py, services.py, routers.py
      expense_tracker  -> api.py, db.py, main.py, models.py    <- never built

  Java job d32dcaf7
      src/main/java/com/example/task -> Task.java, TaskController.java, ...
      model/, controller/, service/  -> the same types, at the workspace root

So the check belongs at the contract WRITE boundary, where every producer
converges, rather than in any one of them.

The discriminator is structural, not a keyword list: two packages compete when
their source-file BASENAMES overlap. Real decompositions do not duplicate
filenames across roots (cmd/main.go + internal/api/handler.go), while duplicate
trees do so by definition. That is deterministic and needs no model, which
matters at the 14b ceiling.

Biased toward false negatives: leaving a rare genuine duplicate is recoverable,
but deleting a package a real project needs is not.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.contract_coherence import (  # noqa: E402
    CompetingPackages,
    find_competing_packages,
    resolve_competing_packages,
)


def _contract(packages, *, module="demo", language="python", **extra):
    data = {
        "version": 1,
        "module": module,
        "language": language,
        "packages": {
            name: {"files": list(files), "owns": []} for name, files in packages.items()
        },
        "symbols": {},
        "deps": [],
    }
    data.update(extra)
    return data


def _pkg_names(contract):
    return set((contract.get("packages") or {}).keys())


# ── the three live failures ─────────────────────────────────────────────────

def test_python_phantom_package_is_dropped():
    """Job 25539373: dev built app/, the contract also demanded expense_tracker/."""
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/schemas.py",
                "app/services.py", "app/routers.py"],
        "expense_tracker": ["expense_tracker/api.py", "expense_tracker/db.py",
                            "expense_tracker/main.py", "expense_tracker/models.py"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert "expense_tracker" not in _pkg_names(resolved)
    assert "app" in _pkg_names(resolved), "the layout that was actually built must survive"
    assert dropped, "dropping a package silently is how this went unnoticed for so long"


def test_go_identical_sibling_packages_collapse_to_one():
    """Verbatim from sandbox_full_output/wiring_contract.json (source=jq-patch)."""
    contract = _contract(
        {
            "cmd/server": ["cmd/server/main.go"],
            "internal/api": ["internal/api/handler.go", "internal/api/models.go",
                             "internal/api/sse.go"],
            "internal/sandbox": ["internal/sandbox/janitor.go",
                                 "internal/sandbox/manager.go",
                                 "internal/sandbox/podman.go"],
            "internal/service": ["internal/service/janitor.go",
                                 "internal/service/manager.go",
                                 "internal/service/podman.go"],
        },
        module="my-sandbox-api",
        language="go",
    )

    resolved, _dropped = resolve_competing_packages(contract)
    survivors = _pkg_names(resolved)

    assert ("internal/sandbox" in survivors) != ("internal/service" in survivors), (
        "exactly one of the two identical packages must remain"
    )
    assert "cmd/server" in survivors and "internal/api" in survivors, (
        "unrelated packages must not be touched"
    )


def test_the_dominant_layout_root_wins_over_a_lone_outlier():
    """
    Also from sandbox_full_output: ``configuration/config.go`` beside
    ``internal/config/config.go``. The finished project (podman-sandbox-api2)
    kept ``internal/config`` — it belongs to the root the rest of the packages
    already use, while ``configuration`` stands alone.

    Size alone picks the wrong one here: ``configuration`` also declares a
    README, so it looks larger while holding the same single source file.
    """
    contract = _contract(
        {
            "configuration": ["configuration/config.go", "configuration/README.md"],
            "internal/api": ["internal/api/handler.go"],
            "internal/config": ["internal/config/config.go"],
            "internal/util": ["internal/util/errors.go"],
        },
        module="my-sandbox-api",
        language="go",
    )

    resolved, _dropped = resolve_competing_packages(contract)

    assert "internal/config" in _pkg_names(resolved)
    assert "configuration" not in _pkg_names(resolved)


def test_bulk_attributed_symbols_do_not_decide_the_winner():
    """
    The same real contract, with its symbol table. ``_guess_package_for_symbol``
    assigns anything it cannot place to ``next(iter(sorted(packages)))``, so the
    phantom ``configuration`` accumulated 11 symbols — among them
    ``configuration.Fatal`` and ``configuration.ListenAndServe``, which are
    stdlib calls — while the real ``internal/config`` had 5.

    Dep edges have no such fallback, and all four name ``internal/config``.
    """
    contract = _contract(
        {
            "cmd/server": ["cmd/server/main.go"],
            "configuration": ["configuration/config.go"],
            "internal/config": ["internal/config/config.go"],
            "internal/sandbox": ["internal/sandbox/manager.go"],
        },
        module="my-sandbox-api",
        language="go",
    )
    contract["symbols"] = {
        f"configuration.{n}": {"package": "configuration", "signature": f"func {n}()"}
        for n in ("LoadConfig", "MustLoadConfig", "main", "NewProduction", "Sync",
                  "NewService", "Fatal", "NewRouter", "RegisterRoutes", "Info",
                  "ListenAndServe")
    }
    contract["symbols"]["internal/config.Load"] = {
        "package": "internal/config", "signature": "func Load()",
    }
    contract["deps"] = [
        {"from": "cmd/server", "to": "internal/config"},
        {"from": "internal/sandbox", "to": "internal/config"},
    ]

    resolved, _dropped = resolve_competing_packages(contract)

    assert "internal/config" in _pkg_names(resolved)
    assert "configuration" not in _pkg_names(resolved)


def test_java_scattered_sketch_roots_are_dropped():
    """
    Job d32dcaf7. The sketch spreads one type per root-level package, so each
    competitor holds a single file — fully subsumed by the real Maven tree.
    """
    real = "src/main/java/com/example/task"
    contract = _contract(
        {
            real: [f"{real}/Task.java", f"{real}/TaskController.java",
                   f"{real}/TaskService.java"],
            "model": ["model/Task.java"],
            "controller": ["controller/TaskController.java"],
            "service": ["service/TaskService.java"],
        },
        module="com.example.task",
        language="java",
    )

    resolved, _dropped = resolve_competing_packages(contract)
    survivors = _pkg_names(resolved)

    assert real in survivors
    assert not ({"model", "controller", "service"} & survivors), (
        "root-level sketch packages duplicate the Maven tree and break the build"
    )


# ── false positives would be worse than the bug ─────────────────────────────

def test_monorepo_services_sharing_only_an_entrypoint_are_kept():
    """Every service has a main.py. One shared generic name is not duplication."""
    contract = _contract({
        "service-a": ["service-a/main.py", "service-a/orders.py", "service-a/db.py"],
        "service-b": ["service-b/main.py", "service-b/billing.py", "service-b/api.py"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == {"service-a", "service-b"}
    assert dropped == []


def test_tests_tree_is_not_a_competitor():
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "tests": ["tests/test_main.py", "tests/test_models.py", "tests/test_services.py"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == {"app", "tests"}
    assert dropped == []


def test_conventional_go_layout_survives_intact():
    """The podman-sandbox-api2 contract — a real, coherent project."""
    contract = _contract(
        {
            "cmd/sandbox-api": ["cmd/sandbox-api/main.go"],
            "internal/api": ["internal/api/create.go", "internal/api/delete.go",
                             "internal/api/execute.go"],
            "internal/config": ["internal/config/config.go"],
            "internal/janitor": ["internal/janitor/janitor.go"],
            "internal/podman": ["internal/podman/client.go", "internal/podman/types.go"],
            "internal/sse": ["internal/sse/writer.go"],
            "test/e2e": ["test/e2e/e2e_test.go"],
            "test/integration": ["test/integration/create_test.go"],
        },
        module="github.com/example/sandbox-api",
        language="go",
    )
    before = _pkg_names(contract)

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == before
    assert dropped == []


def test_siblings_sharing_one_generic_filename_are_kept():
    """internal/sse/writer.go and internal/log/writer.go are both legitimate."""
    contract = _contract(
        {
            "internal/sse": ["internal/sse/writer.go"],
            "internal/log": ["internal/log/writer.go"],
        },
        language="go",
    )

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == {"internal/sse", "internal/log"}
    assert dropped == []


def test_a_package_never_competes_with_its_own_subpackage():
    contract = _contract({
        "app": ["app/main.py", "app/models.py"],
        "app/api": ["app/api/main.py", "app/api/models.py"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == {"app", "app/api"}
    assert dropped == []


def test_packages_with_no_source_files_are_ignored():
    """Docs/config packages have no basenames to compare and must not be dropped."""
    contract = _contract({
        "docs": ["docs/README.md"],
        "deploy": ["deploy/README.md"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert _pkg_names(resolved) == {"docs", "deploy"}
    assert dropped == []


# ── resolution order ────────────────────────────────────────────────────────

def test_on_disk_evidence_beats_declared_size(tmp_path):
    """
    Post-codegen the workspace is ground truth: whatever was really built wins,
    even when the phantom declares more files.
    """
    (tmp_path / "app").mkdir()
    for name in ("main.py", "models.py"):
        (tmp_path / "app" / name).write_text("x = 1", encoding="utf-8")

    contract = _contract({
        "app": ["app/main.py", "app/models.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py",
                            "expense_tracker/api.py", "expense_tracker/db.py"],
    })

    resolved, _dropped = resolve_competing_packages(contract, workspace=tmp_path)

    assert "app" in _pkg_names(resolved)
    assert "expense_tracker" not in _pkg_names(resolved)


def test_resolution_is_deterministic_on_a_perfect_tie():
    contract = _contract({
        "alpha": ["alpha/a.py", "alpha/b.py"],
        "beta": ["beta/a.py", "beta/b.py"],
    })

    first, _ = resolve_competing_packages(json.loads(json.dumps(contract)))
    second, _ = resolve_competing_packages(json.loads(json.dumps(contract)))

    assert _pkg_names(first) == _pkg_names(second)
    assert len(_pkg_names(first)) == 1


def test_symbols_and_deps_for_a_dropped_package_are_pruned():
    """A dangling dep edge would be rendered into the next prompt as fact."""
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    })
    contract["symbols"] = {
        "app.create_user": {"package": "app", "signature": "def create_user(x)",
                            "exports": ["create_user"]},
        "expense_tracker.create_user": {"package": "expense_tracker",
                                        "signature": "def create_user(x)",
                                        "exports": ["create_user"]},
    }
    contract["deps"] = [
        {"from": "app", "to": "expense_tracker"},
        {"from": "expense_tracker", "to": "app"},
    ]

    resolved, _dropped = resolve_competing_packages(contract)

    assert "expense_tracker.create_user" not in resolved["symbols"]
    assert "app.create_user" in resolved["symbols"]
    assert resolved["deps"] == []


def test_drop_reason_names_both_sides_and_the_overlap():
    """validation_issues is read by humans; "removed a package" is not enough."""
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    })

    _resolved, dropped = resolve_competing_packages(contract)

    assert len(dropped) == 1
    reason = dropped[0]
    assert "expense_tracker" in reason and "app" in reason
    assert "main.py" in reason or "models.py" in reason


# ── invariants ──────────────────────────────────────────────────────────────

def test_already_coherent_contract_is_returned_unchanged():
    contract = _contract({
        "app": ["app/main.py"],
        "app/api": ["app/api/routes.py"],
        "tests": ["tests/test_main.py"],
    })
    before = json.loads(json.dumps(contract))

    resolved, dropped = resolve_competing_packages(contract)

    assert resolved["packages"] == before["packages"]
    assert dropped == []


def test_resolution_is_idempotent():
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    })

    once, _ = resolve_competing_packages(contract)
    twice, dropped_again = resolve_competing_packages(json.loads(json.dumps(once)))

    assert _pkg_names(twice) == _pkg_names(once)
    assert dropped_again == []


def test_a_three_way_collision_keeps_exactly_one():
    contract = _contract({
        "app": ["app/main.py", "app/models.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
        "tracker": ["tracker/main.py", "tracker/models.py"],
    })

    resolved, dropped = resolve_competing_packages(contract)

    assert len(_pkg_names(resolved)) == 1
    assert len(dropped) == 2


def test_empty_and_malformed_contracts_do_not_raise():
    for bad in (None, {}, {"packages": None}, {"packages": {"x": None}},
                {"packages": {"x": {"files": "notalist"}}}):
        resolved, dropped = resolve_competing_packages(bad)  # must not raise
        assert dropped == []


def test_find_reports_pairs_without_mutating():
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    })
    before = json.loads(json.dumps(contract))

    groups = find_competing_packages(contract)

    assert groups and isinstance(groups[0], CompetingPackages)
    assert groups[0].keep == "app"
    assert groups[0].drop == "expense_tracker"
    assert contract == before, "detection must be side-effect free"


# ── the write boundary is where every producer converges ────────────────────

def test_write_wiring_contract_persists_a_coherent_contract(tmp_path):
    from llamaindex_crew.utils.wiring_contract import write_wiring_contract

    write_wiring_contract(tmp_path, _contract({
        "app": ["app/main.py", "app/models.py", "app/schemas.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/api.py", "expense_tracker/db.py",
                            "expense_tracker/main.py", "expense_tracker/models.py"],
    }))

    persisted = json.loads((tmp_path / "wiring_contract.json").read_text(encoding="utf-8"))

    assert "expense_tracker" not in persisted["packages"], (
        "no downstream consumer should ever see two layouts for one project"
    )
    assert "app" in persisted["packages"]


def test_write_records_the_repair_in_meta(tmp_path):
    """Silent repair is how the manifest bug hid for four iterations."""
    from llamaindex_crew.utils.wiring_contract import write_wiring_contract

    write_wiring_contract(tmp_path, _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    }))

    persisted = json.loads((tmp_path / "wiring_contract.json").read_text(encoding="utf-8"))
    dropped = (persisted.get("_meta") or {}).get("dropped_competing_packages") or []

    assert any("expense_tracker" in str(d) for d in dropped)


def test_load_repairs_a_legacy_incoherent_contract(tmp_path):
    """Contracts written before this gate existed are read back on resume."""
    from llamaindex_crew.utils.wiring_contract import load_wiring_contract

    (tmp_path / "wiring_contract.json").write_text(
        json.dumps(_contract({
            "app": ["app/main.py", "app/models.py", "app/services.py"],
            "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
        })),
        encoding="utf-8",
    )

    loaded = load_wiring_contract(tmp_path)

    assert "expense_tracker" not in loaded["packages"]
    assert "app" in loaded["packages"]


def test_resolve_does_not_mutate_its_input():
    contract = _contract({
        "app": ["app/main.py", "app/models.py", "app/services.py"],
        "expense_tracker": ["expense_tracker/main.py", "expense_tracker/models.py"],
    })
    before = json.loads(json.dumps(contract))

    resolve_competing_packages(contract)

    assert contract == before


def test_write_leaves_a_coherent_contract_alone(tmp_path):
    from llamaindex_crew.utils.wiring_contract import write_wiring_contract

    contract = _contract(
        {
            "cmd/server": ["cmd/server/main.go"],
            "internal/api": ["internal/api/handler.go"],
            "internal/config": ["internal/config/config.go"],
        },
        module="github.com/example/api",
        language="go",
    )
    write_wiring_contract(tmp_path, contract)

    persisted = json.loads((tmp_path / "wiring_contract.json").read_text(encoding="utf-8"))

    assert set(persisted["packages"]) == {"cmd/server", "internal/api", "internal/config"}
    assert "dropped_competing_packages" not in (persisted.get("_meta") or {})
