"""
Structured output for simple-mode agents (supports_react=False).

Single contract for all agents that cannot use tools:

  LLM response  →  extract_files_from_response()  →  write_files_from_response()

Supported extract formats (in order):
  1. JSON array   — [{"file_path": "...", "content": "..."}]
  2. XML blocks   — <file path="...">...</file>
  3. Path fences  — ```src/app.py\\n...```
  4. Code fences  — ```python\\n...``` (single-file tasks only; maps to target path)

Callers should NOT implement their own parse/write fallbacks.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path validation — reject LLM reasoning text mistaken for file paths
# ---------------------------------------------------------------------------

_MAX_PATH_LEN = 260
_MAX_SEGMENT_LEN = 100
_MAX_SEGMENTS = 12
_MAX_BASENAME_STEM_LEN = 80


def _normalize_file_path(path: str) -> str:
    """Normalize an LLM-generated file path: strip, unify slashes, replace spaces."""
    path = path.strip().replace("\\", "/")
    # Replace spaces with underscores segment-by-segment so 'My File.py' → 'My_File.py'
    parts = path.split("/")
    parts = [p.replace(" ", "_") for p in parts]
    return "/".join(parts)


def is_valid_file_path(path: str) -> bool:
    """Return True if *path* looks like a real relative file path.

    Spaces are accepted because they are normalized by :func:`_normalize_file_path`
    before this check runs in :func:`_filter_valid_entries`.
    """
    if not path or not path.strip():
        return False

    path = _normalize_file_path(path)
    if len(path) > _MAX_PATH_LEN:
        return False
    # Reject remaining control characters and newlines (spaces already replaced above)
    if any(ch in path for ch in ("\n", "\r", "\t")):
        return False
    if "_n_" in path:
        return False
    if ".." in path.split("/"):
        return False

    segments = [s for s in path.split("/") if s]
    if not segments or len(segments) > _MAX_SEGMENTS:
        return False

    for seg in segments:
        if len(seg) > _MAX_SEGMENT_LEN:
            return False

    basename = segments[-1]
    if "." not in basename or basename.endswith("."):
        return False

    stem = basename.rsplit(".", 1)[0]
    if not stem or len(stem) > _MAX_BASENAME_STEM_LEN:
        return False

    return True


def _filter_valid_entries(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    valid: List[Dict[str, str]] = []
    for entry in entries:
        raw_path = entry.get("file_path", "")
        normalized = _normalize_file_path(raw_path)
        if is_valid_file_path(normalized):
            if normalized != raw_path:
                logger.info(
                    "output_parser: normalized file_path %r → %r",
                    raw_path, normalized,
                )
            valid.append({**entry, "file_path": normalized})
        else:
            logger.warning(
                "output_parser: rejecting invalid file_path (len=%d): %.120r",
                len(raw_path),
                raw_path,
            )
    return valid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SIMPLE_MODE_FORMAT_INSTRUCTION = """\

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — IMPORTANT (no tools available)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You do NOT have tools. You MUST output ALL files as a single JSON array.
Each element must have exactly two keys: "file_path" and "content".

Example:
[
  {"file_path": "src/app.py", "content": "# full file content here\\nprint('hello')"},
  {"file_path": "requirements.txt", "content": "flask==3.0.0\\n"}
]

RULES:
- Output ONLY the JSON array — no explanation text before or after.
- Escape all newlines inside "content" as \\n (JSON string rules).
- Do NOT truncate file contents. Include the complete implementation — especially for large files.
- Do NOT use Thought/Action/Observation format. Just output the JSON array.
- Use short, domain-specific file names derived from the project (e.g. features/asset_lifecycle.feature for an IT asset app).
- NEVER copy example file names from these instructions — name files after THIS project's features.
"""

_PRODUCT_OWNER_FORMAT_INSTRUCTION = """\

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — IMPORTANT (no tools available)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You do NOT have tools. You MUST output ALL files as a single JSON array.
Each element must have exactly two keys: "file_path" and "content".

Required files (minimum):
  requirements.md
  user_stories.md
  one or more features/<domain_specific_name>.feature files

Example shape (use YOUR project domain — do NOT copy these names literally):
[
  {{"file_path": "requirements.md", "content": "# Requirements\\n\\n- ...\\n"}},
  {{"file_path": "user_stories.md", "content": "# User Stories\\n\\nAs a ...\\n"}},
  {{"file_path": "features/asset_tracking.feature", "content": "Feature: Asset tracking\\n  Scenario: Register new asset\\n    Given an IT administrator\\n    When they create an asset record\\n    Then the asset appears in inventory\\n"}}
]

RULES:
- Output ONLY the JSON array — no explanation text before or after.
- Escape all newlines inside "content" as \\n (JSON string rules).
- Do NOT truncate file contents. Each .feature file must be complete Gherkin (Feature + Scenario + Given/When/Then).
- Do NOT use Thought/Action/Observation format. Just output the JSON array.
- Feature file names MUST match this project's domain (IT assets, invoicing, etc.) — never use generic placeholder names.
"""

_SINGLE_FILE_FORMAT_INSTRUCTION = """\

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — IMPORTANT (no tools available)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You do NOT have tools. Output EXACTLY ONE of the following (no other text):

OPTION A (preferred) — JSON array with one object:
[{{"file_path": "{path}", "content": "<complete file body with \\\\n for newlines>"}}]

OPTION B — if JSON is difficult, a single fenced code block with the full file:
```{lang}
<complete file contents here>
```

RULES:
- NO reasoning, NO "assistant:" preamble, NO Thought/Action format.
- The file must be COMPLETE — do not truncate large implementations.
- Path must be exactly: {path}
"""


def simple_mode_format_instruction(target_file_path: Optional[str] = None) -> str:
    """Return format instructions for simple-mode tasks.

    Single-file dev tasks get a shorter, stricter prompt with a code-fence fallback.
    Multi-file tasks (e.g. product owner) get the JSON-array prompt.
    """
    if target_file_path:
        ext = Path(target_file_path).suffix.lower()
        lang = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "tsx", ".jsx": "jsx", ".java": "java", ".go": "go",
            ".rs": "rust", ".feature": "gherkin", ".md": "markdown",
        }.get(ext, "")
        return _SINGLE_FILE_FORMAT_INSTRUCTION.format(path=target_file_path, lang=lang)
    return _SIMPLE_MODE_FORMAT_INSTRUCTION


def product_owner_format_instruction() -> str:
    """Format instructions for Product Owner multi-file output."""
    return _PRODUCT_OWNER_FORMAT_INSTRUCTION


def sanitize_gherkin_content(content: str) -> str:
    """Strip markdown wrappers from Gherkin content emitted by LLMs.

    LLMs often wrap feature file content in markdown documentation with:
    - Section headers like ``### 3.3 `features/foo.feature` ``
    - Code fences: `` ```gherkin ... ``` ``

    Extract the innermost Gherkin block when present; otherwise return
    the original content unchanged so valid plain Gherkin is not mangled.
    """
    if not content:
        return content
    text = content.strip()
    # Try to extract a gherkin (or generic) code fence
    fence_match = re.search(
        r"```(?:gherkin|cucumber)?\s*\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        inner = fence_match.group(1).strip()
        # Only use the inner block if it looks like Gherkin
        if re.search(r"^Feature:", inner, re.MULTILINE | re.IGNORECASE):
            return inner
    return text


def is_valid_gherkin_feature(content: str, *, min_chars: int = 40) -> bool:
    """Return True if *content* looks like a real Gherkin feature file.

    Accepts ``Scenario:``, ``Scenario Outline:``, ``Scenario Template:``,
    and ``Background:`` — all are valid Gherkin constructs that LLMs may emit.
    """
    if not content or len(content.strip()) < min_chars:
        return False
    text = content.strip()
    if looks_like_raw_agent_dump(text):
        return False
    if not re.search(r"^Feature:\s*\S", text, re.MULTILINE | re.IGNORECASE):
        return False
    # Accept any standard Gherkin scenario keyword
    has_scenario = bool(re.search(
        r"^\s*(?:Scenario(?:\s+(?:Outline|Template))?|Background|Rule)\s*:",
        text, re.MULTILINE | re.IGNORECASE,
    ))
    has_steps = bool(
        re.search(r"^\s*(Given|When|Then|And|But)\s+", text, re.MULTILINE | re.IGNORECASE)
    )
    return has_scenario and has_steps


def looks_like_raw_agent_dump(content: str) -> bool:
    """True when text is an unparsed LLM/tool transcript, not a real artifact."""
    if not content:
        return False
    head = content.lstrip()[:500]
    if re.match(r"^(?:assistant|user|system)\s*:", head, re.IGNORECASE):
        return True
    if re.search(r"file_writer\s*\(", head, re.IGNORECASE):
        return True
    if re.search(r"Thought\s*:", head, re.IGNORECASE) and "Action" in head:
        return True
    # DeepSeek R1 channel tokens — internal multi-turn dispatch leaked into response
    if re.match(r"<\|(?:channel|start)\|>", head):
        return True
    return False


def is_valid_markdown_artifact(
    content: str,
    *,
    min_chars: int = 120,
    min_lines: int = 4,
) -> bool:
    """Return True if *content* looks like a real markdown planning artifact."""
    if not content or looks_like_raw_agent_dump(content):
        return False
    text = content.strip()
    if len(text) < min_chars or text.count("\n") + 1 < min_lines:
        return False
    if not re.search(r"^#{1,3}\s+\S", text, re.MULTILINE):
        return False
    # Reject obvious mid-word truncation (e.g. "...depre" with no following chars)
    if re.search(r"\w{3,}$", text.splitlines()[-1]) and not text.endswith((".", ":", ")", "`")):
        last = text.splitlines()[-1].strip()
        if len(last) < 20 and not last.endswith((".", ":", ")")):
            return False
    return True


def is_valid_design_spec(content: str) -> bool:
    """Return True if design_spec.md is substantive markdown, not a tool-call dump."""
    if not is_valid_markdown_artifact(content, min_chars=200, min_lines=6):
        return False
    lower = content.lower()
    markers = ("component", "module", "doctype", "architecture", "design", "data model", "ui", "api")
    return any(m in lower for m in markers)


def is_valid_tech_stack(content: str) -> bool:
    """Return True if tech_stack.md includes an implementable file structure."""
    if not is_valid_markdown_artifact(content, min_chars=200, min_lines=5):
        return False
    indicators = ("```", "[SOURCE]", ".py", ".json", ".ts", ".tsx", "├──", "└──", "src/")
    return any(ind in content for ind in indicators)


# Matches DeepSeek R1 internal channel tokens that bleed into simple-mode responses.
# Patterns observed in production:
#   <|channel|>commentary<|message|>...<|end|>
#   <|start|>assistant<|channel|>commentary to=X <|constrain|>json<|message|>...<|call|>
#   <|start|>assistant<|channel|>analysis to=X code<|message|>...<|call|>
_DS_CHANNEL_TOKEN_RE = re.compile(
    r"<\|(?:start\|>assistant<\|)?channel\|>.*?(?:<\|(?:end|call)\|>|$)",
    re.DOTALL,
)
# Matches bare <|start|> / <|end|> / <|call|> markers left after block stripping
_DS_BARE_MARKER_RE = re.compile(r"<\|(?:start|end|call|constrain|message)\|>")


def _strip_deepseek_tokens(text: str) -> str:
    """Remove DeepSeek multi-turn channel tokens from *text*.

    DeepSeek R1 (and derivatives) use special tokens like ``<|channel|>`` and
    ``<|start|>assistant<|channel|>`` for internal tool dispatch.  In simple
    mode these tokens are never executed but DO appear in the raw response,
    preventing the JSON parser from finding the file array.
    """
    cleaned = _DS_CHANNEL_TOKEN_RE.sub("", text)
    cleaned = _DS_BARE_MARKER_RE.sub("", cleaned)
    return cleaned


def _clean_response(text: str) -> str:
    """Strip role prefixes and DeepSeek channel tokens before parsing."""
    cleaned = text.strip()
    # Remove DeepSeek internal multi-turn tokens first (they can wrap the JSON)
    cleaned = _strip_deepseek_tokens(cleaned)
    cleaned = re.sub(r"^(?:assistant|user|system)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _fix_trailing_commas(json_text: str) -> str:
    """Remove trailing commas before ``}`` or ``]`` (common LLM mistake)."""
    return re.sub(r",(\s*[}\]])", r"\1", json_text)


def _escape_raw_chars_in_json_strings(json_text: str) -> str:
    """Escape raw newlines/tabs inside JSON string literals (common LLM mistake)."""
    out: List[str] = []
    in_string = False
    escape = False

    for ch in json_text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch == "\n":
            out.append("\\n")
            continue
        if in_string and ch == "\r":
            out.append("\\r")
            continue
        if in_string and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)

    return "".join(out)


def _json_loads_lenient(json_text: str) -> object | None:
    """Parse JSON, applying common LLM-output repairs on failure."""
    attempts = (
        json_text,
        _fix_trailing_commas(json_text),
        _escape_raw_chars_in_json_strings(json_text),
        _fix_trailing_commas(_escape_raw_chars_in_json_strings(json_text)),
    )
    seen: set[str] = set()
    for candidate in attempts:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _iter_balanced_json_objects(text: str) -> List[str]:
    """Yield complete top-level ``{...}`` substrings from *text*."""
    objects: List[str] = []
    i = 0
    while i < len(text):
        obj_start = text.find("{", i)
        if obj_start == -1:
            break

        depth = 0
        in_string = False
        escape = False
        closed = False

        for j in range(obj_start, len(text)):
            ch = text[j]
            if escape:
                escape = False
                continue
            if ch == "\\":
                if in_string:
                    escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    objects.append(text[obj_start : j + 1])
                    i = j + 1
                    closed = True
                    break

        if not closed:
            break

    return objects


def _try_salvage_json_objects(text: str) -> List[Dict[str, str]] | None:
    """Extract complete file objects from a truncated or malformed JSON array."""
    if '"file_path"' not in text and '"path"' not in text and '"filename"' not in text:
        return None

    salvaged: List[Dict[str, str]] = []
    for obj_str in _iter_balanced_json_objects(text):
        if not any(key in obj_str for key in ('"file_path"', '"path"', '"filename"')):
            continue
        try:
            obj = json.loads(obj_str)
        except json.JSONDecodeError:
            obj = _json_loads_lenient(obj_str)
            if obj is None:
                continue
        salvaged.extend(_normalise_json([obj]))

    if salvaged:
        logger.info(
            "output_parser: salvaged %d file object(s) from partial JSON response",
            len(salvaged),
        )
        return salvaged
    return None


def _try_repair_json_array(text: str) -> List[Dict[str, str]] | None:
    """Attempt to close a truncated ``[...]`` block and parse it."""
    start = text.find("[")
    if start == -1:
        return None

    fragment = text[start:].strip()
    candidates = [
        fragment,
        _fix_trailing_commas(fragment),
    ]
    # Common truncation points — close open string / object / array
    for suffix in ('"]', '"}', '"}]', '}]', '}]', '"]}]', '\n  }]', '\n}]'):
        candidates.append(fragment + suffix)
        candidates.append(_fix_trailing_commas(fragment + suffix))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            data = json.loads(candidate)
            normalised = _normalise_json(data)
            if normalised:
                logger.info(
                    "output_parser: repaired truncated JSON array (%d file(s))",
                    len(normalised),
                )
                return normalised
        except json.JSONDecodeError:
            data = _json_loads_lenient(candidate)
            if data is not None:
                normalised = _normalise_json(data)
                if normalised:
                    logger.info(
                        "output_parser: repaired truncated JSON array (%d file(s))",
                        len(normalised),
                    )
                    return normalised
            continue
    return None


def parse_file_list(response: str) -> List[Dict[str, str]]:
    """Parse a file list from an LLM response (JSON / XML / path-header fences only).

    Prefer :func:`extract_files_from_response` — it also handles code-fence recovery.
    """
    if not response or not response.strip():
        return []

    text = _clean_response(response)

    # 1. JSON array
    result = _try_json(text)
    if result is not None:
        return _filter_valid_entries(result)

    # 2. XML <file path="...">...</file>
    result = _try_xml(text)
    if result:
        return _filter_valid_entries(result)

    # 3. Fenced code blocks with a file-path header
    result = _try_fenced(text)
    if result:
        return _filter_valid_entries(result)

    return []


# ---------------------------------------------------------------------------
# Unified extract + write API (use these from agents / workflow)
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(
    r"```(?:json|python|py|typescript|ts|tsx|javascript|js|java|go|rust|"
    r"yaml|yml|toml|feature|gherkin|markdown|md)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _try_code_fence_for_target(text: str, target_path: str) -> List[Dict[str, str]]:
    """Recover a single file from ```lang ... ``` when the model skipped JSON."""
    candidates: List[str] = []
    for match in _CODE_FENCE_RE.finditer(text):
        content = match.group(1).strip()
        if len(content) < 5:
            continue
        if content.startswith("[") and '"file_path"' in content[:800]:
            continue
        candidates.append(content)
    if not candidates:
        return []
    best = max(candidates, key=len)
    logger.info(
        "output_parser: recovered %r from code fence (%d chars)",
        target_path, len(best),
    )
    return [{"file_path": target_path, "content": best}]


def _read_python_quoted_string(text: str, pos: int) -> tuple[str, int] | None:
    """Decode a Python single/double-quoted string starting at *pos*."""
    if pos >= len(text):
        return None
    quote = text[pos]
    if quote not in ("'", '"'):
        return None
    i = pos + 1
    chunks: List[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", quote: quote}
            chunks.append(mapping.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            return "".join(chunks), i + 1
        chunks.append(ch)
        i += 1
    return None


def _try_file_writer_calls(text: str) -> List[Dict[str, str]]:
    """Extract files from pseudo tool calls like file_writer(file_path='x', content='...')."""
    entries: List[Dict[str, str]] = []
    for match in re.finditer(r"file_writer\s*\(", text, re.IGNORECASE):
        i = match.end()
        while i < len(text) and text[i].isspace():
            i += 1
        path_m = re.match(r"file_path\s*=\s*", text[i:], re.IGNORECASE)
        if not path_m:
            continue
        i += path_m.end()
        path_result = _read_python_quoted_string(text, i)
        if not path_result:
            continue
        file_path, i = path_result
        while i < len(text) and (text[i].isspace() or text[i] == ","):
            i += 1
        content_m = re.match(r"content\s*=\s*", text[i:], re.IGNORECASE)
        if not content_m:
            continue
        i += content_m.end()
        content_result = _read_python_quoted_string(text, i)
        if not content_result:
            continue
        content, _ = content_result
        if is_valid_file_path(file_path) and content.strip():
            entries.append({"file_path": file_path.strip(), "content": content})
    if entries:
        logger.info("output_parser: extracted %d file(s) from file_writer pseudo-calls", len(entries))
    return entries


def _narrow_to_target(
    entries: List[Dict[str, str]], target_file_path: Optional[str],
) -> List[Dict[str, str]]:
    if not target_file_path or not entries:
        return entries
    exact = [e for e in entries if e.get("file_path") == target_file_path]
    if exact:
        return exact
    if len(entries) == 1:
        return entries
    by_name = [
        e for e in entries
        if Path(e.get("file_path", "")).name == Path(target_file_path).name
    ]
    return by_name or entries[:1]


def extract_files_from_response(
    response: str,
    *,
    target_file_path: Optional[str] = None,
) -> tuple[List[Dict[str, str]], str]:
    """Extract files from an LLM response.

    Returns ``(entries, strategy)`` where strategy is one of:
    ``json``, ``xml``, ``path_fence``, ``file_writer``, ``code_fence``, ``none``.
    """
    if not response or not response.strip():
        return [], "none"

    text = _clean_response(response)
    entries = parse_file_list(response)
    strategy = "json" if entries else "none"

    if not entries:
        entries = _try_file_writer_calls(text)
        if entries:
            strategy = "file_writer"

    if not entries and target_file_path:
        entries = _try_code_fence_for_target(text, target_file_path)
        if entries:
            strategy = "code_fence"

    entries = _narrow_to_target(entries, target_file_path)

    if not entries:
        logger.warning(
            "output_parser: could not extract files (len=%d). Preview: %.200r",
            len(response), response,
        )
    return entries, strategy


@dataclass
class WriteResult:
    """Outcome of :func:`write_files_from_response`."""
    written_paths: List[str] = field(default_factory=list)
    parse_strategy: str = "none"
    used_raw_fallback: bool = False


def write_files_from_response(
    response: str,
    workspace_path: Path | str,
    *,
    target_file_path: Optional[str] = None,
    raw_fallback_path: Optional[str] = None,
    label: str = "",
) -> WriteResult:
    """Extract files from *response* and write them to *workspace_path*.

  * *target_file_path* — single-file dev tasks; enables code-fence recovery.
  * *raw_fallback_path* — if extract/write fails, dump raw response here
    (product owner uses ``user_stories.md``).
    """
    from ..tools.file_tools import file_writer

    ws = Path(workspace_path)
    prefix = f"[{label}] " if label else ""
    entries, strategy = extract_files_from_response(
        response, target_file_path=target_file_path,
    )
    result = WriteResult(parse_strategy=strategy)

    if entries:
        logger.info("%ssimple mode: writing %d file(s) via %s", prefix, len(entries), strategy)
        for entry in entries:
            file_path = entry["file_path"]
            content = entry.get("content", "")
            if file_path.endswith(".feature"):
                content = sanitize_gherkin_content(content)
            if file_path.endswith(".feature") and not is_valid_gherkin_feature(content):
                logger.warning(
                    "%sskipping invalid Gherkin for %r (%d chars)",
                    prefix, file_path, len(content),
                )
                continue
            try:
                file_writer(
                    file_path=file_path,
                    content=content,
                    workspace_path=str(ws),
                )
                result.written_paths.append(file_path)
                logger.info("%ssimple write: %s", prefix, file_path)
            except OSError as exc:
                logger.warning(
                    "%ssimple write failed for %r: %s",
                    prefix, file_path, exc,
                )
        if result.written_paths:
            return result

    if raw_fallback_path:
        if looks_like_raw_agent_dump(response):
            logger.warning(
                "%sskipping raw fallback for %s — response looks like unparsed agent output",
                prefix, raw_fallback_path,
            )
        else:
            out = ws / raw_fallback_path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(response, encoding="utf-8")
            result.used_raw_fallback = True
            result.written_paths.append(raw_fallback_path)
            logger.info("%ssimple mode: wrote raw response to %s", prefix, raw_fallback_path)

    return result


# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------

# Regex to find a JSON array anywhere in the response
_JSON_ARRAY_RE = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def _try_json(text: str) -> List[Dict[str, str]] | None:
    """Try to extract a JSON array from *text*.

    Searches for the outermost ``[...]`` block and attempts to parse it.
    Returns None (not empty list) when there is no JSON at all, so the
    caller can fall through to the next format.
    """
    # First try: whole stripped text
    stripped = text.strip()
    if stripped.startswith("["):
        data = _json_loads_lenient(stripped)
        if data is not None:
            return _normalise_json(data)

    # Second try: extract the largest [...] block
    # Walk brackets to find balanced outer array
    start = text.find("[")
    if start == -1:
        return _try_salvage_json_objects(text)

    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                data = _json_loads_lenient(candidate)
                if data is not None:
                    return _normalise_json(data)
                break

    # Third try: strip markdown json fence
    fence_match = re.search(r"```(?:json)?\s*\n(\[.*?\])\s*\n```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        fenced = fence_match.group(1)
        data = _json_loads_lenient(fenced)
        if data is not None:
            return _normalise_json(data)

    # Fourth try: repair truncated array, then salvage complete objects
    repaired = _try_repair_json_array(text)
    if repaired:
        return repaired

    salvaged = _try_salvage_json_objects(text)
    if salvaged:
        return salvaged

    return None


def _normalise_json(data: object) -> List[Dict[str, str]]:
    """Validate and normalise parsed JSON into the canonical file-list format."""
    if not isinstance(data, list):
        logger.debug("output_parser JSON: root is not a list, skipping")
        return []

    result: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        file_path = item.get("file_path") or item.get("path") or item.get("filename")
        content = item.get("content") or item.get("code") or item.get("text", "")
        if not file_path:
            logger.debug("output_parser JSON: entry missing file_path, skipping: %r", item)
            continue
        if not isinstance(content, str):
            content = str(content)
        result.append({"file_path": str(file_path).strip(), "content": content})

    logger.debug("output_parser JSON: extracted %d file(s)", len(result))
    return result


# XML pattern:  <file path="...">content</file>  or  <file name="...">
_XML_FILE_RE = re.compile(
    r'<file\s+(?:path|name)=["\']([^"\']+)["\'][^>]*>(.*?)</file>',
    re.DOTALL | re.IGNORECASE,
)


def _try_xml(text: str) -> List[Dict[str, str]]:
    matches = _XML_FILE_RE.findall(text)
    if not matches:
        return []
    result = [{"file_path": fp.strip(), "content": content} for fp, content in matches]
    logger.debug("output_parser XML: extracted %d file(s)", len(result))
    return result


# Fenced code block whose info string looks like a file path
# e.g.  ```src/app.py  or  ```python src/app.py
# Header is capped at 120 chars and must not contain spaces.
_FENCE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]* )?([a-zA-Z0-9_./\-]{1,120}\.[a-zA-Z0-9]{1,10})\n(.*?)```",
    re.DOTALL,
)


def _try_fenced(text: str) -> List[Dict[str, str]]:
    matches = _FENCE_RE.findall(text)
    if not matches:
        return []
    result = []
    for header, content in matches:
        file_path = header.strip()
        if not file_path:
            continue
        result.append({"file_path": file_path, "content": content})
    logger.debug("output_parser fenced: extracted %d file(s)", len(result))
    return result
