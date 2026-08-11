"""
A broken build manifest invalidates an entire job, and the fix loop cannot see it.

The remediation loop attributes each issue to a file and asks the model to fix
that file. That works only when the defect is *in* the file it is attributed to.
A malformed manifest breaks every source file, so the loop rewrites correct code
around a project the build tool cannot even parse.

Observed live (job d32dcaf7): a generated pom.xml used ``${spring.boot.version}``
as the *parent* version. Maven resolves the parent before the child's
``<properties>`` exist, so it asked Maven Central for a literal
``${spring.boot.version}`` and 404'd. The loop then ran 48 → 67 → 52 → 56
issues across four iterations, never touched pom.xml, and the DevAgent
misdiagnosed it as "cannot access internet to download dependencies" and began
reasoning toward stripping the dependency list.

Both halves of that are deterministic: an unexpanded ``${...}`` inside a
resolved coordinate cannot occur in a correct build, and the repair is a lookup
in ``<properties>``. No model required for either.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.utils.manifest_repair import (  # noqa: E402
    ManifestDefect,
    repair_pom_parent_version,
    validate_manifests,
)


def _pom(parent_version="3.1.2", properties="", dependency_version=None):
    dep = ""
    if dependency_version:
        dep = f"""
  <dependencies>
    <dependency>
      <groupId>com.h2database</groupId>
      <artifactId>h2</artifactId>
      <version>{dependency_version}</version>
    </dependency>
  </dependencies>"""
    return f"""<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <properties>{properties}</properties>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>{parent_version}</version>
  </parent>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>1.0</version>{dep}
</project>"""


# ── the live defect ──────────────────────────────────────────────────────────

def test_repairs_unexpanded_parent_version(tmp_path):
    """The exact pom.xml shape that cost job d32dcaf7 four iterations."""
    pom = tmp_path / "pom.xml"
    pom.write_text(
        _pom(
            parent_version="${spring.boot.version}",
            properties="<spring.boot.version>3.1.2</spring.boot.version>",
        ),
        encoding="utf-8",
    )

    repairs = repair_pom_parent_version(pom)

    assert repairs, "the unexpanded parent version should have been repaired"
    text = pom.read_text(encoding="utf-8")
    assert "<version>3.1.2</version>" in text
    assert "${spring.boot.version}" not in text.split("</parent>")[0]


def test_repair_is_idempotent(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        _pom("${v}", properties="<v>3.1.2</v>"), encoding="utf-8"
    )
    repair_pom_parent_version(pom)
    second = repair_pom_parent_version(pom)
    assert second == [], "a repaired pom must not be rewritten again"


# ── the correctness trap: property versions are LEGAL outside <parent> ───────

def test_dependency_versions_using_properties_are_left_alone(tmp_path):
    """
    ``${...}`` in a *dependency* version is idiomatic, valid Maven — the model
    is fully built by the time those resolve. Only <parent> is special, because
    it resolves before the child's own <properties> exist. A repair that
    rewrote every coordinate would corrupt correct POMs.
    """
    pom = tmp_path / "pom.xml"
    pom.write_text(
        _pom(
            parent_version="3.1.2",
            properties="<h2.version>2.2.224</h2.version>",
            dependency_version="${h2.version}",
        ),
        encoding="utf-8",
    )

    repairs = repair_pom_parent_version(pom)

    assert repairs == [], "valid property use outside <parent> must not be touched"
    assert "${h2.version}" in pom.read_text(encoding="utf-8")


def test_literal_parent_version_is_untouched(tmp_path):
    pom = tmp_path / "pom.xml"
    original = _pom("3.1.2")
    pom.write_text(original, encoding="utf-8")

    assert repair_pom_parent_version(pom) == []
    assert pom.read_text(encoding="utf-8") == original


def test_unresolvable_property_is_reported_not_guessed(tmp_path):
    """No matching <properties> entry — escalate rather than invent a version."""
    pom = tmp_path / "pom.xml"
    pom.write_text(_pom("${nowhere.defined}", properties=""), encoding="utf-8")

    repairs = repair_pom_parent_version(pom)

    assert repairs == [], "must not fabricate a version"
    assert "${nowhere.defined}" in pom.read_text(encoding="utf-8")


def test_malformed_xml_does_not_raise(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text("<project><parent>", encoding="utf-8")
    assert repair_pom_parent_version(pom) == []


def test_missing_file_does_not_raise(tmp_path):
    assert repair_pom_parent_version(tmp_path / "nope.xml") == []


# ── manifest validity gate, per ecosystem ────────────────────────────────────

def test_detects_unexpanded_parent_version_as_a_defect(tmp_path):
    (tmp_path / "pom.xml").write_text(
        _pom("${spring.boot.version}", properties="<spring.boot.version>3.1.2</spring.boot.version>"),
        encoding="utf-8",
    )
    defect = validate_manifests(tmp_path)
    assert isinstance(defect, ManifestDefect)
    assert defect.file == "pom.xml"
    assert defect.repairable is True


def test_detects_invalid_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x",,}', encoding="utf-8")
    defect = validate_manifests(tmp_path)
    assert defect and defect.file == "package.json"
    assert defect.repairable is False, "malformed JSON needs the model, not a rule"


def test_detects_invalid_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project\nname = 'x'", encoding="utf-8")
    defect = validate_manifests(tmp_path)
    assert defect and defect.file == "pyproject.toml"


def test_valid_manifests_report_no_defect(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    (tmp_path / "pom.xml").write_text(_pom("3.1.2"), encoding="utf-8")
    assert validate_manifests(tmp_path) is None


def test_workspace_with_no_manifest_is_not_a_defect(tmp_path):
    """Static HTML and script projects have no manifest; that is normal."""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    assert validate_manifests(tmp_path) is None


def test_defect_message_names_the_real_cause(tmp_path):
    """
    The DevAgent misread this as a network outage. The message must state the
    actual cause so a human reading validation_issues is not misled the same way.
    """
    (tmp_path / "pom.xml").write_text(
        _pom("${spring.boot.version}", properties="<spring.boot.version>3.1.2</spring.boot.version>"),
        encoding="utf-8",
    )
    defect = validate_manifests(tmp_path)
    assert "parent" in defect.description.lower()
    assert "network" not in defect.description.lower()


# ── workflow integration ─────────────────────────────────────────────────────

class _FakeDB:
    def __init__(self):
        self.issues = []

    def create_validation_issue(self, *a, **k):
        self.issues.append((a, k))


def _wf(tmp_path):
    from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W

    wf = W.__new__(W)
    wf.job_db = _FakeDB()
    wf.project_id = "job-1"
    wf.workspace_path = tmp_path
    wf._report_progress = lambda *a, **k: None
    return wf


def test_workflow_repairs_and_signals_revalidation(tmp_path):
    (tmp_path / "pom.xml").write_text(
        _pom("${spring.boot.version}", properties="<spring.boot.version>3.1.2</spring.boot.version>"),
        encoding="utf-8",
    )
    wf = _wf(tmp_path)

    assert wf._repair_build_manifest() is True, "must signal that a re-validate is needed"
    assert "<version>3.1.2</version>" in (tmp_path / "pom.xml").read_text(encoding="utf-8")


def test_workflow_repair_is_idempotent_so_the_loop_cannot_spin(tmp_path):
    """The loop `continue`s on True — a second True on unchanged input would loop forever."""
    (tmp_path / "pom.xml").write_text(
        _pom("${v}", properties="<v>3.1.2</v>"), encoding="utf-8"
    )
    wf = _wf(tmp_path)

    assert wf._repair_build_manifest() is True
    assert wf._repair_build_manifest() is False, "second pass must not re-trigger"


def test_workflow_no_defect_is_a_noop(tmp_path):
    (tmp_path / "pom.xml").write_text(_pom("3.1.2"), encoding="utf-8")
    wf = _wf(tmp_path)
    assert wf._repair_build_manifest() is False
    assert wf.job_db.issues == []


def test_workflow_records_unrepairable_defect_with_the_real_cause(tmp_path):
    (tmp_path / "package.json").write_text('{"bad",,}', encoding="utf-8")
    wf = _wf(tmp_path)

    assert wf._repair_build_manifest() is False
    assert wf.job_db.issues, "an unrepairable manifest must be visible to the user"
    assert "package.json" in str(wf.job_db.issues[0])


def test_workflow_survives_a_broken_db(tmp_path):
    (tmp_path / "package.json").write_text("{,,}", encoding="utf-8")
    wf = _wf(tmp_path)

    class Boom:
        def create_validation_issue(self, *a, **k):
            raise RuntimeError("db gone")

    wf.job_db = Boom()
    assert wf._repair_build_manifest() is False  # must not raise
