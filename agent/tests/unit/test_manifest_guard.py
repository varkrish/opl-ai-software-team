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
