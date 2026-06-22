import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app/agent/src")
sys.path.insert(0, "/app/crew_studio")

from artifact_assertions import assert_or_exit, validate_po_artifacts  # noqa: E402
from llamaindex_crew.agents.product_owner_agent import ProductOwnerAgent  # noqa: E402
from llamaindex_crew.utils.llm_config import get_supports_react  # noqa: E402


def _print_file_preview(path: Path, max_lines: int = 30) -> None:
    if not path.exists():
        print(f"  (missing) {path.name}")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"  --- {path.relative_to(path.parent.parent)} ({len(lines)} lines) ---")
    for line in lines[:max_lines]:
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  ... ({len(lines) - max_lines} more lines)")


def main() -> None:
    test_workspace = Path("/app/workspace/job-test-product-owner-123")
    if test_workspace.exists():
        shutil.rmtree(test_workspace)
    test_workspace.mkdir(parents=True, exist_ok=True)

    supports_react = get_supports_react("manager")
    print("Testing Product Owner Agent isolated...")
    print(f"supports_react (manager model): {supports_react}")

    agent = ProductOwnerAgent(workspace_path=test_workspace)

    vision = (
        "Develop a CLI tool for data processing. "
        "It should read CSV/JSON files, support filter/aggregate/map transforms, "
        "and output results to stdout or a file."
    )
    context_digest = (
        "Greenfield project. Target users: data professionals and analysts. "
        "Priority: lightweight, scriptable, no GUI."
    )

    result = agent.create_user_stories(vision, context_digest=context_digest)

    print("\n\n=== PRODUCT OWNER RESULT (preview) ===")
    preview = str(result)
    print(preview[:2000])
    if len(preview) > 2000:
        print(f"\n... ({len(preview) - 2000} more chars)")

    print("\n\n=== FILES CREATED IN WORKSPACE ===")
    for path in sorted(p for p in test_workspace.rglob("*") if p.is_file()):
        print(path)

    feature_dir = test_workspace / "features"
    feature_files = sorted(feature_dir.glob("*.feature")) if feature_dir.exists() else []

    print("\n\n=== CONTENT ASSERTIONS ===")
    assert_or_exit(validate_po_artifacts(test_workspace, vision), "Product Owner")

    print("\n\n=== requirements.md ===")
    _print_file_preview(test_workspace / "requirements.md")

    print("\n\n=== user_stories.md ===")
    _print_file_preview(test_workspace / "user_stories.md")

    if feature_files:
        print("\n\n=== first feature file ===")
        _print_file_preview(feature_files[0])

    print("\n\nTEST PASSED: Product Owner artifacts valid (existence + content)")


if __name__ == "__main__":
    main()
