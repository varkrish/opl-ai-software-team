"""
TDD tests for crew_studio.migration.mta_parser.

Tests written BEFORE implementation to ensure >=90% coverage.
"""
import json
import pytest
from pathlib import Path
import tempfile
import sys

# Add project root to path
root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root))

from crew_studio.migration.mta_parser import is_mta_issues_json, parse_mta_issues_json, _resolve_file_path


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_mta_json():
    """Minimal valid MTA issues.json structure."""
    return [
        {
            "applicationId": "",
            "issues": {
                "mandatory": [
                    {
                        "id": "issue-001",
                        "name": "Replace Java EE with Jakarta",
                        "ruleId": "javaee-to-jakarta-001",
                        "effort": {"type": "Trivial", "points": 1, "description": "Simple change"},
                        "totalIncidents": 3,
                        "totalStoryPoints": 3,
                        "links": [],
                        "affectedFiles": [
                            {
                                "description": "Replace javax with jakarta",
                                "files": [
                                    {"fileId": "123", "fileName": "src/main/java/App.java", "occurrences": 1}
                                ]
                            }
                        ],
                        "sourceTechnologies": [],
                        "targetTechnologies": ["eap:[8]"]
                    }
                ],
                "information": [
                    {
                        "id": "info-001",
                        "name": "Maven POM found",
                        "ruleId": "discover-pom",
                        "effort": {"type": "Info", "points": 0, "description": "Info"},
                        "totalIncidents": 1,
                        "totalStoryPoints": 0,
                        "links": [],
                        "affectedFiles": [{"description": "POM file", "files": [{"fileId": "1", "fileName": "pom.xml", "occurrences": 1}]}],
                        "sourceTechnologies": [],
                        "targetTechnologies": []
                    }
                ]
            }
        }
    ]


@pytest.fixture
def duplicate_issues_mta_json():
    """MTA JSON with duplicate issues across applicationIds."""
    return [
        {
            "applicationId": "",
            "issues": {
                "mandatory": [
                    {
                        "id": "dup-001-a",
                        "name": "Replace Java EE",
                        "ruleId": "javaee-to-jakarta-001",
                        "effort": {"type": "Trivial", "points": 1, "description": "Simple"},
                        "totalIncidents": 2,
                        "totalStoryPoints": 2,
                        "links": [],
                        "affectedFiles": [
                            {"description": "Replace javax", "files": [{"fileId": "1", "fileName": "src/App.java", "occurrences": 1}]}
                        ],
                        "sourceTechnologies": [],
                        "targetTechnologies": []
                    }
                ]
            }
        },
        {
            "applicationId": "app-123",
            "issues": {
                "mandatory": [
                    {
                        "id": "dup-001-b",
                        "name": "Replace Java EE",
                        "ruleId": "javaee-to-jakarta-001",  # SAME ruleId
                        "effort": {"type": "Trivial", "points": 1, "description": "Simple"},
                        "totalIncidents": 2,
                        "totalStoryPoints": 2,
                        "links": [],
                        "affectedFiles": [
                            {"description": "Replace javax", "files": [{"fileId": "1", "fileName": "src/App.java", "occurrences": 1}]}
                        ],
                        "sourceTechnologies": [],
                        "targetTechnologies": []
                    }
                ]
            }
        }
    ]


@pytest.fixture
def class_name_files_mta_json():
    """MTA JSON with Java class names instead of file paths."""
    return [
        {
            "applicationId": "",
            "issues": {
                "mandatory": [
                    {
                        "id": "class-001",
                        "name": "Fix imports",
                        "ruleId": "fix-imports-001",
                        "effort": {"type": "Trivial", "points": 1, "description": "Easy"},
                        "totalIncidents": 1,
                        "totalStoryPoints": 1,
                        "links": [],
                        "affectedFiles": [
                            {
                                "description": "Update this class",
                                "files": [
                                    {"fileId": "100", "fileName": "com.acmecorp.inventory.util.DatabaseConnectionManager", "occurrences": 1}
                                ]
                            }
                        ],
                        "sourceTechnologies": [],
                        "targetTechnologies": []
                    }
                ]
            }
        }
    ]


# ── Test: Format Detection ───────────────────────────────────────────────────

class TestFormatDetection:
    """Test is_mta_issues_json() correctly identifies MTA format."""

    def test_valid_mta_array_returns_true(self, valid_mta_json):
        """Valid MTA issues.json (array of objects with applicationId) returns True."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(valid_mta_json, f)
            path = Path(f.name)
        
        try:
            assert is_mta_issues_json(path) is True
        finally:
            path.unlink()

    def test_non_array_json_returns_false(self):
        """JSON object (not array) returns False."""
        data = {"foo": "bar"}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            assert is_mta_issues_json(path) is False
        finally:
            path.unlink()

    def test_empty_array_returns_false(self):
        """Empty array returns False."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([], f)
            path = Path(f.name)
        
        try:
            assert is_mta_issues_json(path) is False
        finally:
            path.unlink()

    def test_non_json_file_returns_false(self):
        """Non-JSON text file returns False."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("This is not JSON")
            path = Path(f.name)
        
        try:
            assert is_mta_issues_json(path) is False
        finally:
            path.unlink()

    def test_missing_file_returns_false(self):
        """Non-existent file returns False."""
        path = Path("/nonexistent/file.json")
        assert is_mta_issues_json(path) is False


# ── Test: Deduplication ──────────────────────────────────────────────────────

class TestDeduplication:
    """Test deduplication by ruleId across applicationIds."""

    def test_deduplicates_by_rule_id(self, duplicate_issues_mta_json):
        """Same ruleId across 2 applicationIds collapses to 1 issue."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(duplicate_issues_mta_json, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            # Should have only 1 issue, not 2
            assert len(issues) == 1
            assert issues[0]["id"] == "javaee-to-jakarta-001"
        finally:
            path.unlink()

    def test_keeps_unique_rule_ids(self, valid_mta_json):
        """Different ruleIds are all kept."""
        # Add a second unique mandatory issue
        valid_mta_json[0]["issues"]["mandatory"].append({
            "id": "unique-002",
            "name": "Another change",
            "ruleId": "unique-rule-002",
            "effort": {"type": "Trivial", "points": 1, "description": "Simple"},
            "totalIncidents": 1,
            "totalStoryPoints": 1,
            "links": [],
            "affectedFiles": [{"description": "Do this", "files": [{"fileId": "2", "fileName": "src/Foo.java", "occurrences": 1}]}],
            "sourceTechnologies": [],
            "targetTechnologies": []
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(valid_mta_json, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            # Both unique ruleIds should be present
            assert len(issues) == 2
            rule_ids = {issue["id"] for issue in issues}
            assert "javaee-to-jakarta-001" in rule_ids
            assert "unique-rule-002" in rule_ids
        finally:
            path.unlink()


# ── Test: Category Filtering ─────────────────────────────────────────────────

class TestCategoryFiltering:
    """Test that information category is skipped."""

    def test_skips_information_category(self, valid_mta_json):
        """Information issues (Maven POM found, etc.) are not included."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(valid_mta_json, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            # Only mandatory issue, information should be skipped
            assert len(issues) == 1
            assert issues[0]["id"] == "javaee-to-jakarta-001"
        finally:
            path.unlink()

    def test_includes_all_actionable_categories(self):
        """Mandatory, potential, cloud-mandatory all included."""
        data = [{
            "applicationId": "",
            "issues": {
                "mandatory": [{"id": "m1", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}],
                "potential": [{"id": "p1", "name": "P", "ruleId": "r2", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "2", "fileName": "b.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}],
                "cloud-mandatory": [{"id": "c1", "name": "C", "ruleId": "r3", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "3", "fileName": "c.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}],
                "information": [{"id": "i1", "name": "I", "ruleId": "r4", "effort": {"type": "Info", "points": 0, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 0, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "4", "fileName": "d.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]
            }
        }]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            # 3 actionable, 1 information skipped
            assert len(issues) == 3
            rule_ids = {issue["id"] for issue in issues}
            assert "r1" in rule_ids
            assert "r2" in rule_ids
            assert "r3" in rule_ids
            assert "r4" not in rule_ids  # information skipped
        finally:
            path.unlink()


# ── Test: Severity Mapping ───────────────────────────────────────────────────

class TestSeverityMapping:
    """Test severity mapping from MTA categories to DB values."""

    def test_mandatory_maps_to_mandatory(self):
        """Issues in 'mandatory' category get severity='mandatory'."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["severity"] == "mandatory"
        finally:
            path.unlink()

    def test_cloud_mandatory_maps_to_mandatory(self):
        """Issues in 'cloud-mandatory' category get severity='mandatory'."""
        data = [{"applicationId": "", "issues": {"cloud-mandatory": [{"id": "c", "name": "C", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["severity"] == "mandatory"
        finally:
            path.unlink()

    def test_potential_maps_to_potential(self):
        """Issues in 'potential' category get severity='potential'."""
        data = [{"applicationId": "", "issues": {"potential": [{"id": "p", "name": "P", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["severity"] == "potential"
        finally:
            path.unlink()


# ── Test: Effort Mapping ─────────────────────────────────────────────────────

class TestEffortMapping:
    """Test effort mapping from MTA effort.type to DB values."""

    def test_trivial_maps_to_low(self):
        """Effort type 'Trivial' maps to 'low'."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["effort"] == "low"
        finally:
            path.unlink()

    def test_architectural_maps_to_high(self):
        """Effort type 'Architectural' maps to 'high'."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Architectural", "points": 7, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 7, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["effort"] == "high"
        finally:
            path.unlink()

    def test_unknown_effort_maps_to_medium(self):
        """Unknown effort type defaults to 'medium'."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "UnknownType", "points": 3, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 3, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["effort"] == "medium"
        finally:
            path.unlink()


# ── Test: File Path Resolution ───────────────────────────────────────────────

class TestFilePathResolution:
    """Test converting MTA fileName to actual file paths."""

    def test_real_path_passes_through(self):
        """Actual file paths (src/...) pass through unchanged."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "src/main/java/App.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert "src/main/java/App.java" in issues[0]["files"]
        finally:
            path.unlink()

    def test_class_name_converts_to_path(self, class_name_files_mta_json):
        """Java class name (com.foo.Bar) converts to src/main/java/com/foo/Bar.java."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(class_name_files_mta_json, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            # Class name should convert to path
            expected = "src/main/java/com/acmecorp/inventory/util/DatabaseConnectionManager.java"
            assert expected in issues[0]["files"]
        finally:
            path.unlink()

    def test_files_json_app_prefix_stripped(self):
        """When files.json has fullPath 'app/pom.xml', parser returns 'pom.xml'."""
        issues_json = Path(tempfile.gettempdir()) / "issues_app_prefix.json"
        files_json = Path(tempfile.gettempdir()) / "files_app_prefix.json"
        try:
            issues_data = [{
                "applicationId": "",
                "issues": {
                    "mandatory": [{
                        "id": "m1",
                        "name": "POM update",
                        "ruleId": "pom-rule",
                        "effort": {"type": "Trivial", "points": 1, "description": ""},
                        "totalIncidents": 1,
                        "totalStoryPoints": 1,
                        "links": [],
                        "affectedFiles": [{
                            "description": "Update pom",
                            "files": [{"fileId": "32904", "fileName": "pom.xml", "occurrences": 1}]
                        }],
                        "sourceTechnologies": [],
                        "targetTechnologies": []
                    }]
                }
            }]
            files_data = [{"id": "32904", "fullPath": "app/pom.xml"}]
            issues_json.write_text(json.dumps(issues_data))
            files_json.write_text(json.dumps(files_data))
            issues = parse_mta_issues_json(issues_json, files_json_path=files_json)
            assert len(issues) == 1
            assert "pom.xml" in issues[0]["files"]
            assert "app/pom.xml" not in issues[0]["files"]
        finally:
            if issues_json.exists():
                issues_json.unlink()
            if files_json.exists():
                files_json.unlink()


# ── Test: Migration Hint Extraction ──────────────────────────────────────────

class TestMigrationHintExtraction:
    """Test extracting migration_hint from affectedFiles descriptions."""

    def test_uses_first_affected_files_description(self):
        """migration_hint comes from affectedFiles[0].description."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "This is the hint", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues[0]["migration_hint"] == "This is the hint"
        finally:
            path.unlink()

    def test_multi_variant_concatenates_hints(self):
        """Multiple affectedFiles entries (e.g. different hard-coded IPs) concatenate hints."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "Hard-coded IP", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 2, "totalStoryPoints": 2, "links": [], "affectedFiles": [{"description": "IP: 127.0.0.1", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}, {"description": "IP: 192.168.1.1", "files": [{"fileId": "2", "fileName": "b.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            hint = issues[0]["migration_hint"]
            assert "IP: 127.0.0.1" in hint
            assert "IP: 192.168.1.1" in hint
        finally:
            path.unlink()


# ── Test: Edge Cases ─────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_issues_returns_empty_list(self):
        """MTA JSON with no issues returns empty list."""
        data = [{"applicationId": "", "issues": {}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues == []
        finally:
            path.unlink()

    def test_missing_affected_files_skips_issue(self):
        """Issue with no affectedFiles is skipped."""
        data = [{"applicationId": "", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 0, "totalStoryPoints": 0, "links": [], "affectedFiles": [], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert issues == []
        finally:
            path.unlink()

    def test_single_application_id_works(self):
        """MTA JSON with only one applicationId works."""
        data = [{"applicationId": "app-123", "issues": {"mandatory": [{"id": "m", "name": "M", "ruleId": "r1", "effort": {"type": "Trivial", "points": 1, "description": ""}, "totalIncidents": 1, "totalStoryPoints": 1, "links": [], "affectedFiles": [{"description": "d", "files": [{"fileId": "1", "fileName": "a.java", "occurrences": 1}]}], "sourceTechnologies": [], "targetTechnologies": []}]}}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            assert len(issues) == 1
        finally:
            path.unlink()

    def test_corrupt_json_raises_error(self):
        """Corrupt JSON raises a clear error."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{corrupt json")
            path = Path(f.name)
        
        try:
            with pytest.raises((json.JSONDecodeError, ValueError)):
                parse_mta_issues_json(path)
        finally:
            path.unlink()


# ── Test: Output Contract ────────────────────────────────────────────────────

class TestOutputContract:
    """Test that output matches create_migration_issue() parameters."""

    def test_all_required_keys_present(self, valid_mta_json):
        """Every returned dict has all keys required by create_migration_issue()."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(valid_mta_json, f)
            path = Path(f.name)
        
        try:
            issues = parse_mta_issues_json(path)
            required_keys = {"id", "title", "severity", "effort", "files", "description", "migration_hint"}
            
            for issue in issues:
                assert required_keys.issubset(issue.keys())
                assert isinstance(issue["files"], list)
                assert len(issue["files"]) > 0
        finally:
            path.unlink()


# ── Test: File path resolution (pom.xml, known extensions) ───────────────────

class TestFilePathResolutionExtended:
    """Test _resolve_file_path handles known file extensions correctly."""

    def test_pom_xml_not_treated_as_class(self):
        """pom.xml must NOT become src/main/java/pom/xml.java."""
        result = _resolve_file_path("pom.xml")
        assert result == "pom.xml"
        assert "java" not in result

    def test_application_properties_not_treated_as_class(self):
        """application.properties must stay as-is."""
        result = _resolve_file_path("application.properties")
        assert result == "application.properties"

    def test_build_gradle_not_treated_as_class(self):
        """build.gradle stays as-is."""
        result = _resolve_file_path("build.gradle")
        assert result == "build.gradle"

    def test_web_xml_not_treated_as_class(self):
        result = _resolve_file_path("web.xml")
        assert result == "web.xml"

    def test_persistence_xml_not_treated_as_class(self):
        result = _resolve_file_path("persistence.xml")
        assert result == "persistence.xml"

    def test_java_class_still_converted(self):
        """Java class names (com.foo.Bar) must still be converted."""
        result = _resolve_file_path("com.acmecorp.Foo")
        assert result == "src/main/java/com/acmecorp/Foo.java"

    def test_path_with_slashes_passes_through(self):
        """Paths with / are passed through unchanged."""
        result = _resolve_file_path("src/main/resources/persistence.xml")
        assert result == "src/main/resources/persistence.xml"


# ── Test: Deduplication merges files across apps ─────────────────────────────

class TestDeduplicationMergesFiles:
    """When same ruleId appears in multiple apps, files should be merged."""

    def test_merges_files_from_duplicate_rule_ids(self):
        """Same ruleId in two apps with different files → merged file list."""
        data = [
            {
                "applicationId": "app1",
                "issues": {
                    "mandatory": [{
                        "id": "i1", "name": "Replace imports",
                        "ruleId": "javax-to-jakarta-import-00001",
                        "effort": {"type": "Trivial", "points": 1, "description": ""},
                        "totalIncidents": 1, "totalStoryPoints": 1, "links": [],
                        "affectedFiles": [{"description": "Replace", "files": [
                            {"fileId": "1", "fileName": "com.app.Foo", "occurrences": 1}
                        ]}],
                        "sourceTechnologies": [], "targetTechnologies": [],
                    }]
                }
            },
            {
                "applicationId": "app2",
                "issues": {
                    "mandatory": [{
                        "id": "i2", "name": "Replace imports",
                        "ruleId": "javax-to-jakarta-import-00001",
                        "effort": {"type": "Trivial", "points": 1, "description": ""},
                        "totalIncidents": 1, "totalStoryPoints": 1, "links": [],
                        "affectedFiles": [{"description": "Replace", "files": [
                            {"fileId": "2", "fileName": "com.app.Bar", "occurrences": 1}
                        ]}],
                        "sourceTechnologies": [], "targetTechnologies": [],
                    }]
                }
            },
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            issues = parse_mta_issues_json(path)
            assert len(issues) == 1, "Should deduplicate to 1 issue"
            files = issues[0]["files"]
            assert len(files) == 2, f"Should have 2 files merged, got {files}"
            assert "src/main/java/com/app/Foo.java" in files
            assert "src/main/java/com/app/Bar.java" in files
        finally:
            path.unlink()
