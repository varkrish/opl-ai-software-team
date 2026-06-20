"""Epic story loop helpers for sequential Jira story implementation."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are an AI judge for a software development epic.

Evaluate whether the existing user stories are sufficient to implement the epic incrementally.
Stories are sufficient when:
- There is at least one story
- Each story has a clear summary and enough detail for a developer to implement it
- Stories are ordered logically and cover the epic scope without huge gaps

Epic vision:
{vision}

Existing stories (JSON):
{stories_json}

Respond as JSON only, no markdown:
{{
  "sufficient": true|false,
  "reasoning": "one or two sentences",
  "suggested_stories": [
    {{"summary": "...", "description": "..."}}
  ]
}}

If sufficient is true, suggested_stories may be empty.
If sufficient is false, provide 3-8 suggested_stories that decompose the epic.
"""

DECOMPOSE_PROMPT = """Decompose this epic into ordered user stories for incremental implementation.

Epic vision:
{vision}

Technical context (tech stack excerpt):
{tech_stack}

Respond as JSON only, no markdown:
{{
  "stories": [
    {{"summary": "...", "description": "Detailed implementation notes for this story"}}
  ]
}}

Provide 3-8 stories in logical build order.
"""


@dataclass
class JudgeVerdict:
    sufficient: bool
    reasoning: str = ""
    suggested_stories: list[dict] = field(default_factory=list)
    verdict_available: bool = True  # False when judge LLM was unreachable


def parse_jira_stories(metadata: Optional[dict]) -> list[dict]:
    if not metadata:
        return []
    stories = metadata.get("jira_stories")
    if not isinstance(stories, list):
        return []
    return [s for s in stories if isinstance(s, dict) and (s.get("key") or s.get("summary"))]


def stories_are_provisioned(stories: list[dict]) -> bool:
    """Return True when metadata already has implementable child stories.

    If the epic was created with linked JIRA stories (or explicit jira_stories in
    job metadata), we implement those stories as-is.  Decomposition runs only when
    there are no child stories to work from.
    """
    if not stories:
        return False
    for story in stories:
        if not isinstance(story, dict):
            return False
        if not (story.get("summary") or "").strip():
            return False
    return True


def resume_story_index(metadata: Optional[dict]) -> int:
    if not metadata:
        return 0
    last = metadata.get("last_completed_story_index")
    if last is None:
        return 0
    try:
        return int(last) + 1
    except (TypeError, ValueError):
        return 0


def stories_to_process(stories: list[dict], start_index: int = 0) -> list[dict]:
    if start_index <= 0:
        return list(stories)
    return list(stories[start_index:])


def commit_message_for_story(story: dict) -> str:
    key = story.get("key", "STORY")
    summary = (story.get("summary") or "implement story").strip()
    return f"feat({key}): {summary}"


def story_vision(story: dict, epic_vision: str) -> str:
    key = story.get("key", "")
    summary = story.get("summary", "")
    description = story.get("description", "")
    return (
        f"Implement Jira story {key}: {summary}\n\n"
        f"Story description:\n{description}\n\n"
        f"Epic context:\n{epic_vision[:8000]}"
    )


def is_epic_job(metadata: Optional[dict]) -> bool:
    if not metadata:
        return False
    if metadata.get("epic_mode"):
        return True
    return bool(metadata.get("jira_epic_key"))


def project_key_from_epic(epic_key: str) -> str:
    if "-" in epic_key:
        return epic_key.split("-", 1)[0]
    return epic_key


def normalize_story_dicts(
    raw_stories: list[dict],
    epic_key: str = "",
    existing_keys: Optional[set[str]] = None,
) -> list[dict]:
    """Assign stable local keys when JIRA keys are not yet available."""
    existing = existing_keys or set()
    prefix = project_key_from_epic(epic_key) if epic_key else "STORY"
    normalized: list[dict] = []
    counter = 1
    for item in raw_stories:
        if not isinstance(item, dict):
            continue
        summary = (item.get("summary") or "").strip()
        description = (item.get("description") or "").strip()
        if not summary:
            continue
        key = (item.get("key") or "").strip()
        if not key or key in existing:
            while True:
                candidate = f"{prefix}-{counter}"
                counter += 1
                if candidate not in existing:
                    key = candidate
                    break
        existing.add(key)
        normalized.append({
            "key": key,
            "summary": summary,
            "description": description,
            "status": item.get("status") or "To Do",
            "order": len(normalized),
        })
    return normalized


def _parse_json_from_llm(content: str) -> dict:
    text = (content or "").strip()
    if "```" in text:
        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text):
            try:
                return json.loads(block.strip())
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
    return {}


def _call_llm_text(llm: Any, prompt: str) -> str:
    from llama_index.core.llms import ChatMessage, MessageRole

    if hasattr(llm, "chat"):
        response = llm.chat([ChatMessage(role=MessageRole.USER, content=prompt)])
        return str(getattr(response.message, "content", response))
    if callable(llm):
        return str(llm(prompt))
    raise TypeError("llm must expose chat() or be callable")


def judge_stories(
    stories: list[dict],
    vision: str,
    llm: Any,
    *,
    llm_call: Optional[Callable[[str], str]] = None,
) -> JudgeVerdict:
    """LLM judge: are existing stories sufficient to implement the epic?"""
    if not stories:
        return JudgeVerdict(
            sufficient=False,
            reasoning="No child stories linked to the epic.",
            suggested_stories=[],
        )

    prompt = JUDGE_PROMPT.format(
        vision=vision[:12000],
        stories_json=json.dumps(stories, indent=2)[:12000],
    )
    try:
        content = llm_call(prompt) if llm_call else _call_llm_text(llm, prompt)
        data = _parse_json_from_llm(content)
    except Exception as exc:
        logger.warning("judge_stories LLM failed: %s", exc, exc_info=True)
        return JudgeVerdict(
            sufficient=len(stories) >= 1,
            reasoning="Judge unavailable; proceeding with existing stories.",
            verdict_available=False,
        )

    sufficient = bool(data.get("sufficient"))
    reasoning = str(data.get("reasoning") or "").strip()
    suggested = data.get("suggested_stories") or []
    if not isinstance(suggested, list):
        suggested = []
    clean_suggested = [
        {"summary": s.get("summary", ""), "description": s.get("description", "")}
        for s in suggested
        if isinstance(s, dict) and s.get("summary")
    ]
    logger.info(
        "Epic judge: sufficient=%s reasoning=%r suggested=%d",
        sufficient,
        reasoning[:120],
        len(clean_suggested),
    )
    return JudgeVerdict(
        sufficient=sufficient,
        reasoning=reasoning,
        suggested_stories=clean_suggested,
    )


def decompose_epic_to_stories(
    vision: str,
    tech_stack: str,
    llm: Any,
    *,
    llm_call: Optional[Callable[[str], str]] = None,
    suggested_stories: Optional[list[dict]] = None,
) -> list[dict]:
    """Decompose an epic into ordered story dicts."""
    if suggested_stories:
        return normalize_story_dicts(suggested_stories)

    prompt = DECOMPOSE_PROMPT.format(
        vision=vision[:12000],
        tech_stack=(tech_stack or "Not specified")[:8000],
    )
    try:
        content = llm_call(prompt) if llm_call else _call_llm_text(llm, prompt)
        data = _parse_json_from_llm(content)
    except Exception as exc:
        logger.error("decompose_epic_to_stories LLM failed: %s", exc, exc_info=True)
        return []

    raw = data.get("stories") or []
    if not isinstance(raw, list):
        return []
    return normalize_story_dicts(raw)


def has_jira_connection(metadata: Optional[dict]) -> bool:
    if not metadata or not metadata.get("jira_epic_key"):
        return False
    import os

    return bool(
        os.getenv("JIRA_BASE_URL")
        or os.getenv("JIRA_API_TOKEN")
        or os.getenv("JIRA_PERSONAL_ACCESS_TOKEN")
        or (os.getenv("JIRA_USERNAME") and os.getenv("JIRA_PASSWORD"))
    )


def should_auto_approve(metadata: Optional[dict], auto_approve_no_jira: bool = True) -> bool:
    """Standalone epics auto-approve when JIRA is not connected."""
    if has_jira_connection(metadata):
        return False
    return auto_approve_no_jira


# ---------------------------------------------------------------------------
# Story quality assessment — determines whether PO phase can be skipped
# ---------------------------------------------------------------------------

# Minimum character count for a description to be considered non-trivial.
_MIN_DESCRIPTION_LEN = 60

# Patterns that indicate a catch-all / placeholder summary (case-insensitive).
_CATCHALL_PATTERNS = re.compile(
    r"\b(do everything|implement all|implement everything|implement the entire|"
    r"build everything|complete the|handle everything|all features|"
    r"implement all features)\b",
    re.IGNORECASE,
)


def stories_pass_heuristics(stories: list[dict]) -> tuple[bool, str]:
    """Fast (no-LLM) check: do the stories have enough structure to assess with the judge?

    Returns (True, "") when stories look implementable, or (False, reason) when
    obviously deficient so we skip the LLM judge call and go straight to PO.
    """
    if not stories:
        return False, "No child stories linked to the epic."

    for story in stories:
        summary = (story.get("summary") or "").strip()
        description = (story.get("description") or "").strip()

        if not summary:
            return False, f"Story missing summary: {story.get('key', '?')}"

        if _CATCHALL_PATTERNS.search(summary):
            return (
                False,
                f"Story '{summary}' looks like a vague catch-all summary. "
                "Run PO to produce concrete, implementable stories.",
            )

        if len(description) < _MIN_DESCRIPTION_LEN:
            return (
                False,
                f"Story '{summary}' has a short or missing description "
                f"({len(description)} chars, minimum {_MIN_DESCRIPTION_LEN}). "
                "Run PO to add implementation detail.",
            )

    return True, ""


def format_jira_stories_as_markdown(stories: list[dict], epic_vision: str = "") -> str:
    """Render provisioned JIRA stories as user_stories.md content (no LLM).

    Produces a structured markdown document that downstream Designer and TA
    agents can consume directly — equivalent to PO output but sourced from
    the actual JIRA story data instead of an LLM rewrite.
    """
    lines = []
    if epic_vision:
        lines.append(f"# Epic Overview\n\n{epic_vision.strip()}\n")

    lines.append("# User Stories\n")
    for story in stories:
        key = story.get("key", "?")
        summary = (story.get("summary") or "").strip()
        description = (story.get("description") or "").strip()
        status = story.get("status", "To Do")

        lines.append(f"## {key}: {summary}\n")
        lines.append(f"**Status:** {status}\n")
        if description:
            lines.append(f"**Description:**\n\n{description}\n")
        lines.append("---\n")

    return "\n".join(lines)


@dataclass
class StoryAssessment:
    """Result of the two-layer (heuristics + judge) epic story assessment."""
    skip_po: bool                        # True → seed user_stories.md; False → run PO
    stories: list[dict]                  # stories to use downstream
    reasoning: str
    verdict: Optional[JudgeVerdict] = None


def assess_epic_stories(
    stories: list[dict],
    vision: str,
    llm: Any,
    *,
    llm_call: Optional[Callable[[str], str]] = None,
) -> StoryAssessment:
    """Two-layer assessment: heuristics first, then AI judge.

    Layer 1 — fast heuristics (no LLM):
        Fail immediately for empty list, missing/trivially short descriptions,
        or obvious catch-all summaries.

    Layer 2 — AI Judge (existing judge_stories):
        Only runs when heuristics pass.  If sufficient → skip PO.
        If insufficient or judge unavailable → run PO.

    Safe fallback: when in doubt, run PO (quality over speed).
    """
    # Layer 1: fast heuristics
    ok, heuristic_reason = stories_pass_heuristics(stories)
    if not ok:
        logger.info("assess_epic_stories: heuristics fail — running PO. Reason: %s", heuristic_reason)
        return StoryAssessment(
            skip_po=False,
            stories=stories,
            reasoning=heuristic_reason,
            verdict=None,
        )

    # Layer 2: AI judge
    try:
        verdict = judge_stories(stories, vision, llm, llm_call=llm_call)
    except Exception as exc:
        logger.warning(
            "assess_epic_stories: judge LLM failed (%s) — conservatively running PO", exc
        )
        return StoryAssessment(
            skip_po=False,
            stories=stories,
            reasoning=f"Judge unavailable ({exc}); running PO as safe fallback.",
            verdict=None,
        )

    # judge_stories catches its own exceptions and returns verdict_available=False
    if not verdict.verdict_available:
        logger.warning(
            "assess_epic_stories: judge was unavailable — conservatively running PO"
        )
        return StoryAssessment(
            skip_po=False,
            stories=stories,
            reasoning="Judge unavailable; running PO as safe fallback.",
            verdict=verdict,
        )

    if verdict.sufficient:
        logger.info(
            "assess_epic_stories: judge sufficient — PO will be skipped. Reason: %s",
            verdict.reasoning[:120],
        )
        return StoryAssessment(
            skip_po=True,
            stories=stories,
            reasoning=verdict.reasoning,
            verdict=verdict,
        )

    logger.info(
        "assess_epic_stories: judge insufficient — running PO. Reason: %s",
        verdict.reasoning[:120],
    )
    return StoryAssessment(
        skip_po=False,
        stories=stories,
        reasoning=verdict.reasoning,
        verdict=verdict,
    )
