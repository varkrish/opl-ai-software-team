"""Unit tests for solution critique approval rules."""
from llamaindex_crew.workflows.solutioning_loop import (
    is_critique_approved,
    normalize_critique,
)


def test_approved_with_empty_must_fix():
    critique = {"approved": True, "score": 9, "must_fix": [], "issues": []}
    assert is_critique_approved(critique) is True
    assert normalize_critique(critique)["approved"] is True


def test_approved_with_must_fix_is_not_approved():
    critique = {
        "approved": True,
        "score": 9,
        "must_fix": ["Add explicit Podman --label flag"],
        "issues": ["Missing label in create_sandbox"],
    }
    assert is_critique_approved(critique) is False
    assert normalize_critique(critique)["approved"] is False


def test_rejected_stays_rejected():
    critique = {
        "approved": False,
        "score": 4,
        "must_fix": ["Rewrite security section"],
        "issues": [],
    }
    assert is_critique_approved(critique) is False


def test_whitespace_only_must_fix_does_not_block():
    critique = {"approved": True, "must_fix": ["", "   "], "issues": []}
    assert is_critique_approved(critique) is True
