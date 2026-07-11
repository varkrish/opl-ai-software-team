"""Unit tests for TECH_STACK_MANIFEST_GUARD feature toggle."""
import os
from pathlib import Path
from unittest import mock

import pytest


class TestManifestGuardMode:
    def test_defaults_to_relaxed(self):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            get_manifest_guard_mode,
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TECH_STACK_MANIFEST_GUARD", None)
            assert get_manifest_guard_mode() == ManifestGuardMode.RELAXED

    def test_invalid_value_falls_back_to_relaxed(self):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            get_manifest_guard_mode,
        )

        with mock.patch.dict(os.environ, {"TECH_STACK_MANIFEST_GUARD": "bogus"}):
            assert get_manifest_guard_mode() == ManifestGuardMode.RELAXED


class TestManifestGuardPaths:
    def test_companion_test_paths_for_middleware(self):
        from src.llamaindex_crew.utils.manifest_guard import companion_test_paths

        companions = companion_test_paths("src/middleware/channel.middleware.ts")
        assert "src/middleware/__tests__/channel.middleware.test.ts" in companions

    def test_remediation_strict_uses_registered_only(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            remediation_write_allowlist,
        )

        registered = {"src/middleware/channel.middleware.ts"}
        allowed = remediation_write_allowlist(
            registered, tmp_path, ManifestGuardMode.STRICT
        )
        assert allowed == registered

    def test_remediation_relaxed_expands(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            remediation_write_allowlist,
        )

        registered = {"src/middleware/channel.middleware.ts"}
        allowed = remediation_write_allowlist(
            registered, tmp_path, ManifestGuardMode.RELAXED
        )
        assert "src/middleware/__tests__/channel.middleware.test.ts" in allowed

    def test_remediation_off_disables_guard(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            remediation_write_allowlist,
        )

        allowed = remediation_write_allowlist(
            {"src/app.ts"}, tmp_path, ManifestGuardMode.OFF
        )
        assert allowed is None

    def test_validation_off_skips_unauthorized(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            is_path_manifest_authorized,
        )

        registered = {"src/app.ts"}
        assert is_path_manifest_authorized(
            "src/extra/orphan.ts", registered, tmp_path, ManifestGuardMode.OFF
        )

    def test_validation_strict_rejects_orphan(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            is_path_manifest_authorized,
        )

        registered = {"src/app.ts"}
        assert not is_path_manifest_authorized(
            "src/extra/orphan.ts", registered, tmp_path, ManifestGuardMode.STRICT
        )

    def test_validation_relaxed_allows_companion_test(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            is_path_manifest_authorized,
        )

        registered = {"src/middleware/channel.middleware.ts"}
        assert is_path_manifest_authorized(
            "src/middleware/__tests__/channel.middleware.test.ts",
            registered,
            tmp_path,
            ManifestGuardMode.RELAXED,
        )

    def test_dev_phase_guard_only_when_strict(self):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            dev_phase_write_guard_enabled,
        )

        assert dev_phase_write_guard_enabled(ManifestGuardMode.STRICT)
        assert not dev_phase_write_guard_enabled(ManifestGuardMode.RELAXED)
        assert not dev_phase_write_guard_enabled(ManifestGuardMode.OFF)


class TestPythonPackageInits:
    def test_expand_adds_package_init(self):
        from src.llamaindex_crew.utils.manifest_guard import expand_python_package_inits

        registered = {"notebooks/churn_visualization.py"}
        expanded = expand_python_package_inits(registered)
        assert "notebooks/__init__.py" in expanded
        assert "notebooks/churn_visualization.py" in expanded

    def test_expand_nested_packages(self):
        from src.llamaindex_crew.utils.manifest_guard import expand_python_package_inits

        registered = {"src/services/billing/invoice.py"}
        expanded = expand_python_package_inits(registered)
        assert "src/__init__.py" in expanded
        assert "src/services/__init__.py" in expanded
        assert "src/services/billing/__init__.py" in expanded

    def test_expand_skips_flat_module_conflict(self):
        from src.llamaindex_crew.utils.manifest_guard import expand_python_package_inits

        registered = {"app/models.py", "app/models/user.py"}
        expanded = expand_python_package_inits(registered)
        assert "app/__init__.py" in expanded
        assert "app/models/__init__.py" not in expanded

    def test_is_companion_python_init(self):
        from src.llamaindex_crew.utils.manifest_guard import is_companion_python_init

        allowed = {"notebooks/churn_visualization.py"}
        assert is_companion_python_init("notebooks/__init__.py", allowed)
        assert not is_companion_python_init("mlflow/__init__.py", allowed)
        assert not is_companion_python_init("notebooks/churn_visualization.py", allowed)

    def test_dev_phase_allowlist_includes_inits(self):
        from src.llamaindex_crew.utils.manifest_guard import dev_phase_write_allowlist

        allowed = dev_phase_write_allowlist({"notebooks/churn_visualization.py"})
        assert "notebooks/__init__.py" in allowed

    def test_strict_remediation_includes_python_inits(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            remediation_write_allowlist,
        )

        registered = {"notebooks/churn_visualization.py"}
        allowed = remediation_write_allowlist(
            registered, tmp_path, ManifestGuardMode.STRICT
        )
        assert "notebooks/__init__.py" in allowed

    def test_strict_validation_allows_package_init(self, tmp_path):
        from src.llamaindex_crew.utils.manifest_guard import (
            ManifestGuardMode,
            is_path_manifest_authorized,
        )

        registered = {"notebooks/churn_visualization.py"}
        assert is_path_manifest_authorized(
            "notebooks/__init__.py",
            registered,
            tmp_path,
            ManifestGuardMode.STRICT,
        )
