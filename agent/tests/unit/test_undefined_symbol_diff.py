"""A contract can be fully satisfied and the build still broken.

``reconcile_workspace_against_contract`` checks contract-vs-disk in both
directions plus import-prefix correctness.  All three ask whether the contract
was SATISFIED.  None asks whether it was SUFFICIENT.  Job b13dde92 is the proof:
nothing declared Status.java, so nothing was reported missing, and reconciliation
passed while javac could not compile a line.

This adds the missing direction: referenced AND NOT defined.

Why it matters beyond Java — Python's smoke test is ``compileall``, which checks
syntax only.  ``from .schemas import Status`` where schemas.py never defines
Status is perfectly valid syntax; it is a runtime ImportError that compileall
structurally cannot see.  The javac-based scaffolding cannot fire for Python.
This can.

Nothing in the implementation names a language.  Coverage follows the evidence:
a file is checked when the payload carries named imports on one side and a
target that parsed to at least one symbol on the other.  File extensions and
directory-module conventions (``__init__``, ``index``, ``mod``) are read off the
workspace's own files, so Rust and Go resolve by the same rule as Python and
TypeScript without appearing anywhere in the code.

That also handles extractor quality without a denylist.  Probing tldr 1.2.2:

    python      imports with names, classes, functions   all present
    typescript  imports with names, classes, functions   all present
    java        imports [], classes []                   no evidence

Java therefore yields nothing here and stays covered by the javac parser — but
because there is nothing to act on, not because it was excluded.  When an
extractor improves, this starts working on its own.  The guarantee the tests
pin is that degradation produces SILENCE, never a flood of phantoms.

The dangerous direction is a FALSE POSITIVE.  A missed symbol costs one
iteration; a phantom one sends the fix loop editing correct code, and on a 14b
that is the whole budget.  So every ambiguity resolves toward silence:

  - a module that does not resolve to a workspace file is third-party, skipped
  - a target that parsed to nothing is a parser gap, skipped
  - ``import *`` tells us nothing about names, skipped
  - a name a target merely RE-EXPORTS counts as defined
  - if a module resolves to several files, the name must be missing from ALL

tldr strips leading dots from relative imports — ``.helpers`` and ``..helpers``
both arrive as ``helpers`` — so resolution cannot be exact and must tolerate
several candidate locations.  That is why the multiple-match rule exists.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.wiring_contract import (
    _structure_symbol_issues,
    collect_undefined_referenced_symbols,
)


def _py(files):
    """Build a tldr structure payload for a Python workspace."""
    return {"root": ".", "language": "python", "files": files}


def _f(path, *, classes=(), functions=(), imports=()):
    """Build one file entry as `tldr structure` emits it."""
    return {
        "path": path,
        "classes": list(classes),
        "functions": list(functions),
        "imports": [
            {"module": m, "names": list(n), "is_from": True} for m, n in imports
        ],
    }


class TestTheRegressionCase(unittest.TestCase):
    """The exact shape of job b13dde92, transposed to Python."""

    def test_reports_symbol_referenced_but_never_defined(self):
        data = _py([
            _f("app/service.py", functions=["make"],
               imports=[("schemas", ["TaskDto", "Status", "Priority"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        issues = _structure_symbol_issues(data)
        names = {i["symbol"] for i in issues}
        self.assertEqual(names, {"Status", "Priority"})

    def test_defined_symbol_is_not_reported(self):
        data = _py([
            _f("app/service.py", imports=[("schemas", ["TaskDto"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_issue_points_at_the_file_that_must_change(self):
        """
        The fix is to DEFINE the symbol, so the issue must name the target
        file, not the referencing one.  The fix loop dispatches per file;
        pointing it at the referencing file reproduces exactly the Java bug
        this check exists to catch, where DevAgent was told to fix the file
        that was already correct.
        """
        data = _py([
            _f("app/service.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        issue = _structure_symbol_issues(data)[0]
        self.assertEqual(issue["file"], "app/schemas.py")
        self.assertIn("app/service.py", issue["description"])
        self.assertIn("Status", issue["description"])

    def test_functions_count_as_definitions(self):
        data = _py([
            _f("app/api.py", imports=[("helpers", ["build_task"])]),
            _f("app/helpers.py", functions=["build_task"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])


class TestFalsePositiveGuards(unittest.TestCase):
    """Every ambiguity resolves toward silence."""

    def test_third_party_module_is_skipped(self):
        """fastapi is not in the workspace; it is not ours to check."""
        data = _py([
            _f("app/service.py", imports=[("fastapi", ["FastAPI", "Depends"])]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_stdlib_module_is_skipped(self):
        data = _py([
            _f("app/service.py", imports=[("typing", ["List", "Optional"])]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_star_import_is_skipped(self):
        """`import *` says nothing about which names exist."""
        data = _py([
            _f("app/service.py", imports=[("schemas", ["*"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_reexported_name_counts_as_defined(self):
        """
        A package __init__ or a facade module that re-imports a name makes that
        name importable from itself.  Treating imported names as defined handles
        both, and chains through several hops for free.
        """
        data = _py([
            _f("app/uses.py", imports=[("facade", ["TaskDto"])]),
            _f("app/facade.py", imports=[("schemas", ["TaskDto"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_package_init_reexport(self):
        data = _py([
            _f("app/main.py", imports=[("models", ["Task"])]),
            _f("app/models/__init__.py", imports=[("task", ["Task"])]),
            _f("app/models/task.py", classes=["Task"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_target_that_parsed_to_nothing_is_skipped(self):
        """
        An empty symbol set means the parser gave up on that file, not that the
        file is genuinely empty of definitions. Reporting every name against it
        would bury the loop in phantoms.
        """
        data = _py([
            _f("app/service.py", imports=[("schemas", ["TaskDto", "Status"])]),
            _f("app/schemas.py"),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_ambiguous_module_needs_missing_from_all_candidates(self):
        """
        tldr strips leading dots, so `helpers` may mean any of several files.
        When more than one candidate matches, a name is only missing if it is
        missing from every one of them.
        """
        data = _py([
            _f("app/api/routes.py", imports=[("helpers", ["build"])]),
            _f("app/api/helpers.py", functions=["other"]),
            _f("app/helpers.py", functions=["build"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_ambiguous_module_reports_when_missing_from_every_candidate(self):
        data = _py([
            _f("app/api/routes.py", imports=[("helpers", ["build"])]),
            _f("app/api/helpers.py", functions=["other"]),
            _f("app/helpers.py", functions=["different"]),
        ])
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"build"})

    def test_test_files_are_skipped(self):
        """Reconciliation ignores test trees; this must agree with it."""
        data = _py([
            _f("tests/test_service.py", imports=[("schemas", ["Ghost"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_no_duplicate_issue_per_symbol_and_file(self):
        data = _py([
            _f("app/a.py", imports=[("schemas", ["Status"])]),
            _f("app/b.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        issues = _structure_symbol_issues(data)
        self.assertEqual(len(issues), 1)


class TestLanguageAgnostic(unittest.TestCase):
    """Nothing here names a language. Coverage follows the evidence.

    The check runs wherever the payload carries named imports on one side and a
    parsed target on the other.  Extensions and directory-module conventions are
    read off the workspace's own files, so a language nobody anticipated works
    with no code change, and a weak extractor goes quiet on its own.
    """

    def test_typescript(self):
        data = {
            "root": ".", "language": "typescript",
            "files": [
                _f("src/app.ts", imports=[("./models", ["Foo", "Bar"])]),
                _f("src/models.ts", classes=["Foo"]),
            ],
        }
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"Bar"})

    def test_typescript_directory_module_uses_index(self):
        data = {
            "root": ".", "language": "typescript",
            "files": [
                _f("src/app.ts", imports=[("./models", ["Foo"])]),
                _f("src/models/index.ts", classes=["Foo"]),
            ],
        }
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_rust_works_without_being_named_anywhere(self):
        """
        Rust was never in scope and is not mentioned in the implementation.
        It works because `.rs` is read off the file list and `mod` is one of the
        conventional directory-module names — the same rule that resolves
        Python's `__init__` and TypeScript's `index`.
        """
        data = {"root": ".", "language": "rust", "files": [
            _f("src/main.rs", imports=[("models", ["Task", "Ghost"])]),
            _f("src/models/mod.rs", classes=["Task"]),
        ]}
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"Ghost"})

    def test_go_package_directory(self):
        data = {"root": ".", "language": "go", "files": [
            _f("cmd/api/main.go", imports=[("internal/store", ["Task"])]),
            _f("internal/store/store.go", classes=["Other"], functions=["New"]),
        ]}
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"Task"})

    def test_weak_extractor_goes_quiet_without_being_named(self):
        """
        tldr 1.2.2 returns imports:[] and classes:[] for Java. That produces no
        findings here — not because Java is on a denylist, but because a payload
        with no named imports carries no evidence to act on. Java stays covered
        by the javac 'cannot find symbol' parser.

        The guarantee this pins: an extractor that degrades produces SILENCE,
        never a flood of phantoms.
        """
        data = {
            "root": ".", "language": "java",
            "files": [
                {"path": "src/main/java/com/example/TaskService.java",
                 "classes": [], "functions": ["private final public create"],
                 "imports": []},
                {"path": "src/main/java/com/example/dto/Other.java",
                 "classes": [], "functions": [], "imports": []},
            ],
        }
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_modifier_keywords_are_stripped_from_defined_names(self):
        """
        Extractors leak modifiers into the symbol name, and which ones depends on
        the tool version, not the language. Measured on the same sources:

            tldr 1.2.2   "export makeTask"  /  "private final public create"
            tldr 1.5.2   "makeTask"         /  "create"

        A mangled name matches no import, so without normalisation a symbol that
        is defined right there gets reported as undefined — a phantom generated
        purely by the extractor's formatting. Taking the last whitespace token
        recovers the name in every observed case and needs no keyword list.
        """
        data = {"files": [
            {"path": "src/app.ts", "language": "typescript",
             "classes": [], "functions": [],
             "imports": [{"module": "./models", "names": ["makeTask"]}]},
            {"path": "src/models.ts", "language": "typescript",
             "classes": [], "functions": ["export makeTask"], "imports": []},
        ]}
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_methods_field_from_newer_payloads_counts_as_defined(self):
        """tldr 1.5.2 adds a `methods` list that 1.2.2 does not emit."""
        data = {"files": [
            {"path": "src/app.ts", "language": "typescript",
             "classes": [], "functions": [], "methods": [],
             "imports": [{"module": "./models", "names": ["add"]}]},
            {"path": "src/models.ts", "language": "typescript",
             "classes": ["TaskService"], "functions": [], "methods": ["add"],
             "imports": []},
        ]}
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_newer_plural_languages_schema_is_tolerated(self):
        """
        tldr 1.5.2 replaced `"language": "python"` with `"languages": [...]` and
        returns every language's files from a single call, tagging none of them.
        Language then comes from the file extension, which both versions supply.
        """
        data = {
            "root": "/abs/path",
            "languages": ["python", "typescript"],
            "files": [
                {"path": "backend/app/service.py", "classes": [], "functions": [],
                 "imports": [{"module": "schemas", "names": ["TaskDto", "Status"]}]},
                {"path": "backend/app/schemas.py", "classes": ["TaskDto"],
                 "functions": [], "imports": []},
                # Same stem in the other half of the project: must not be
                # mistaken for the Python target.
                {"path": "frontend/src/schemas.ts", "classes": ["Other"],
                 "functions": [], "imports": []},
            ],
        }
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"Status"})
        self.assertEqual(issues[0]["file"], "backend/app/schemas.py")

    def test_partially_parsed_target_cannot_flood(self):
        """
        The riskier degradation: imports parse but the target's definitions do
        not. The "target must have parsed to at least one symbol" guard is what
        stops every name in the file being reported at once.
        """
        data = _py([
            _f("app/service.py",
               imports=[("schemas", ["A", "B", "C", "D", "E"])]),
            _f("app/schemas.py"),
        ])
        self.assertEqual(_structure_symbol_issues(data), [])


class TestPolyglotWorkspace(unittest.TestCase):
    """One language per workspace is not an assumption this system can make.

    Every job here builds a backend AND a frontend — the test critique literally
    has a "Backend:" line and a "Frontend:" line.  ``tldr structure`` without
    --lang auto-detects and returns exactly ONE language's files, so a single
    call leaves the other half of the project invisible: verified against tldr
    1.2.2 on a FastAPI + TypeScript tree, where the call returned the two Python
    files and neither TypeScript file.

    So languages are enumerated from the workspace and merged before diffing.
    Resolution stays WITHIN a language group, because a Python ``from .models
    import Task`` must never resolve to ``frontend/src/models.ts`` and report a
    missing symbol against it.
    """

    def test_both_halves_of_a_full_stack_project_are_checked(self):
        data = {"files": [
            {"path": "backend/app/service.py", "language": "python",
             "classes": [], "functions": ["make"],
             "imports": [{"module": "schemas", "names": ["TaskDto", "Status"]}]},
            {"path": "backend/app/schemas.py", "language": "python",
             "classes": ["TaskDto"], "functions": [], "imports": []},
            {"path": "frontend/src/app.ts", "language": "typescript",
             "classes": [], "functions": [],
             "imports": [{"module": "./models", "names": ["Foo", "Bar"]}]},
            {"path": "frontend/src/models.ts", "language": "typescript",
             "classes": ["Foo"], "functions": [], "imports": []},
        ]}
        issues = _structure_symbol_issues(data)
        self.assertEqual({i["symbol"] for i in issues}, {"Status", "Bar"})

    def test_resolution_does_not_cross_language_boundaries(self):
        """
        A same-named module in the other half of the project must not be
        mistaken for the target. Here Python imports `models`, has no Python
        models file, and a TypeScript models.ts exists — that is an unresolved
        third-party import, not a defect in the TypeScript file.
        """
        data = {"files": [
            {"path": "backend/app/service.py", "language": "python",
             "classes": [], "functions": [],
             "imports": [{"module": "models", "names": ["Task"]}]},
            {"path": "frontend/src/models.ts", "language": "typescript",
             "classes": ["Foo"], "functions": [], "imports": []},
        ]}
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_top_level_language_still_works(self):
        """A single-language payload has no per-file tag; the top-level one applies."""
        data = _py([
            _f("app/service.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        self.assertEqual(len(_structure_symbol_issues(data)), 1)


class TestImportsFallbackUnlocksFrontendCoverage(unittest.TestCase):
    """`tldr structure` cannot supply the referenced side for TypeScript.

    Measured on the same file, both versions:

        structure 1.2.2   imports: []
        structure 1.5.2   imports: [{module: '{ Foo, Bar } from "./models"',
                                     names: []}]        <- clause, not a module
        imports   1.2.2   [{module: "./models", names: ["Foo","Bar"]}]   correct
        imports   1.5.2   [{module: "./models", names: ["Foo","Bar"]}]   correct

    So the per-file `tldr imports` command is the only usable reference source
    for TS, in every version. Without it the frontend half of every job — and
    every job here builds one — has no symbol coverage at all.

    The catch is the DEFINED side, which is version-dependent:

        structure 1.2.2   classes: []                  unusable
        structure 1.5.2   classes: ["TaskService"]     good

    Pairing reliable references against unusable definitions reports every
    imported name as undefined. So the fallback is gated on the extractor
    demonstrating it can parse definitions for that language group at all —
    at least one file with a non-empty `classes` list. That is a property of
    the payload, not a version number or a language name.
    """

    def _ts(self, *, classes_on_target):
        """A TS project whose references only `tldr imports` can supply."""
        return (
            {"files": [
                # structure's view: no usable named imports either way
                {"path": "src/app.ts", "classes": [], "functions": [],
                 "imports": [{"module": '{ Foo, Bar } from "./models"', "names": []}]},
                {"path": "src/models.ts",
                 "classes": ["Foo"] if classes_on_target else [],
                 "functions": ["render"], "imports": []},
            ]},
            # what `tldr imports src/app.ts` returns
            {"src/app.ts": [{"module": "./models", "names": ["Foo", "Bar"]}]},
        )

    def test_detects_undefined_symbol_when_definitions_are_parseable(self):
        """The 1.5.2 case: Bar is genuinely missing from models.ts."""
        data, imports = self._ts(classes_on_target=True)
        issues = _structure_symbol_issues(data, imports_by_path=imports)
        self.assertEqual({i["symbol"] for i in issues}, {"Bar"})
        self.assertEqual(issues[0]["file"], "src/models.ts")

    def test_stays_silent_when_definitions_are_not_parseable(self):
        """
        The 1.2.2 case: classes:[] everywhere means Foo AND Bar would both be
        reported, and Foo is defined. Two phantoms out of two names — the exact
        flood the gate exists to prevent.
        """
        data, imports = self._ts(classes_on_target=False)
        self.assertEqual(_structure_symbol_issues(data, imports_by_path=imports), [])

    def test_structure_supplied_references_do_not_need_the_gate(self):
        """
        Python's references come from structure itself, so evidence is
        symmetric and the class gate must not apply — a module of pure
        functions has no classes and must still be checked.
        """
        data = _py([
            _f("app/service.py", imports=[("helpers", ["build", "missing_fn"])]),
            _f("app/helpers.py", functions=["build"]),
        ])
        issues = _structure_symbol_issues(data, imports_by_path={})
        self.assertEqual({i["symbol"] for i in issues}, {"missing_fn"})

    def test_fallback_is_ignored_when_structure_already_had_names(self):
        """Never override the symmetric source with the asymmetric one."""
        data = _py([
            _f("app/service.py", imports=[("schemas", ["TaskDto"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        bogus = {"app/service.py": [{"module": "schemas", "names": ["Ghost"]}]}
        self.assertEqual(_structure_symbol_issues(data, imports_by_path=bogus), [])

    def test_absent_fallback_map_behaves_as_before(self):
        data, _ = self._ts(classes_on_target=True)
        self.assertEqual(_structure_symbol_issues(data), [])


class TestNeverRaises(unittest.TestCase):
    """A detector that crashes the validator is worse than no detector."""

    def test_empty_payload(self):
        self.assertEqual(_structure_symbol_issues({}), [])

    def test_none_payload(self):
        self.assertEqual(_structure_symbol_issues(None), [])

    def test_malformed_entries_are_tolerated(self):
        data = {
            "language": "python",
            "files": [
                {"path": "app/a.py", "imports": [{"module": None, "names": None}]},
                {"path": None},
                "not a dict",
                {"imports": "not a list"},
            ],
        }
        self.assertEqual(_structure_symbol_issues(data), [])

    def test_missing_tldr_binary_yields_no_issues(self, ):
        """A workspace tldr cannot read is not evidence of a defect."""
        from unittest.mock import patch

        with patch(
            "llamaindex_crew.tools.tldr_tools._resolve_tldr_bin", return_value=None,
        ):
            self.assertEqual(
                collect_undefined_referenced_symbols(Path("/nonexistent")), [],
            )


class TestIssueShape(unittest.TestCase):
    """Issues must slot into the existing reconciliation issue stream."""

    def test_severity_is_warning_by_default(self):
        """
        Landing as a warning means it is reported but never blocks a job and
        never feeds the fix loop, so a false-positive rate can be observed on
        real jobs before promotion. _collect_fixable_issues already drops
        warnings, so this is the whole safety mechanism.
        """
        data = _py([
            _f("app/service.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        issue = _structure_symbol_issues(data)[0]
        self.assertEqual(issue["severity"], "warning")

    def test_severity_is_promotable_by_env(self):
        from unittest.mock import patch

        data = _py([
            _f("app/service.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        with patch.dict(
            "os.environ", {"WIRING_UNDEFINED_SYMBOL_SEVERITY": "error"}, clear=False,
        ):
            issue = _structure_symbol_issues(data)[0]
        self.assertEqual(issue["severity"], "error")

    def test_carries_reconciliation_type_and_symbol(self):
        data = _py([
            _f("app/service.py", imports=[("schemas", ["Status"])]),
            _f("app/schemas.py", classes=["TaskDto"]),
        ])
        issue = _structure_symbol_issues(data)[0]
        self.assertEqual(issue["type"], "wiring_reconciliation")
        self.assertEqual(issue["symbol"], "Status")
        self.assertTrue(issue["description"])


if __name__ == "__main__":
    unittest.main()
