"""Author untrusted Bash artifacts; parsing and saving never execute generated code."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import unicodedata

MAX_SCRIPT_BYTES = 32 * 1024
MAX_REQUEST_CHARS = 4000
SHEBANG = "#!/usr/bin/env bash\n"


def _contains_controls(value: str) -> bool:
    return any(unicodedata.category(char).startswith("C") and char not in "\n\t"
               for char in value)


@dataclass(frozen=True)
class ScriptDraft:
    code: str
    explanation: str
    source: str


def validate_syntax(code: str) -> None:
    """Run Bash's parser only, with no inherited shell startup hooks.

    Syntax validity is not a security review. Even valid scripts may destroy data,
    contact the network, or request elevated privileges if a user runs them later.
    """
    if type(code) is not str or not code or _contains_controls(code):
        raise ValueError("Script must be nonempty text without terminal control characters.")
    try:
        size = len(code.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("Script must be valid UTF-8 text.") from exc
    if size > MAX_SCRIPT_BYTES:
        raise ValueError("Script exceeds the 32 KiB limit.")
    try:
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-n"], input=code,
            capture_output=True, text=True, timeout=5,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Bash syntax validation is unavailable or timed out.") from exc
    if result.returncode:
        raise ValueError("Generated Bash has invalid syntax; the script was not saved.")


def _draft(code: str, explanation: str, source: str) -> ScriptDraft:
    if type(code) is not str or not code.strip() or type(explanation) is not str or not explanation.strip():
        raise ValueError("Script response must contain code and an explanation.")
    if len(explanation) > 4000 or _contains_controls(explanation):
        raise ValueError("Script explanation is invalid or too long.")
    if code.startswith("#!"):
        code = code.split("\n", 1)[1] if "\n" in code else ""
    if not code.strip():
        raise ValueError("Script response has no Bash body.")
    code = SHEBANG + code.rstrip("\n") + "\n"
    validate_syntax(code)
    return ScriptDraft(code, explanation.strip(), source)


def _offline(request: str) -> ScriptDraft | None:
    request = re.sub(r"^(?:please\s+)?(?:write|create|generate)\s+(?:me\s+)?(?:a\s+)?(?:(?:bash|shell)\s+)?script(?:\s+to|\s+that)?\s+",
                     "", request.strip(), flags=re.IGNORECASE)
    phrase = " ".join(request.lower().strip().rstrip(".?!").split())
    if phrase in {"system report", "system information report", "storage report", "check storage", "checking storage",
                  "show storage", "show disk usage", "report storage usage", "show system information"}:
        return _draft("set -euo pipefail\nprintf '%s\\n' 'System:'\nuname -a\n"
                      "printf '\\n%s\\n' 'Filesystem storage:'\ndf -h -- .\n"
                      "printf '\\n%s\\n' 'Current directory size:'\ndu -sh -- .\n",
                      "Print system details, filesystem capacity and current-directory size. "
                      "The report reads metadata and does not request sudo.", "offline-template")
    try:
        words = shlex.split(request)
    except ValueError as exc:
        raise ValueError("Unbalanced quotes in the script request.") from exc
    lower = [word.lower() for word in words]
    if lower[:2] == ["backup", "directory"] or phrase in {"back up a directory", "backup a directory"}:
        if lower in (["backup", "directory"], ["back", "up", "a", "directory"], ["backup", "a", "directory"]):
            assignments = ('source_dir=${1:?Usage: script.sh SOURCE_DIRECTORY ARCHIVE.tar.gz}\n'
                           'archive_path=${2:?Usage: script.sh SOURCE_DIRECTORY ARCHIVE.tar.gz}\n')
        elif len(words) == 5 and lower[:2] == ["backup", "directory"] and lower[3] == "to":
            assignments = f"source_dir={shlex.quote(words[2])}\narchive_path={shlex.quote(words[4])}\n"
        else:
            raise ValueError("Use: backup directory, or backup directory 'SOURCE' to 'ARCHIVE.tar.gz'.")
        return _draft("set -euo pipefail\nset -o noclobber\n" + assignments +
                      'source_dir=$(realpath -- "$source_dir")\n'
                      'archive_path=$(realpath -m -- "$archive_path")\n'
                      '[[ -d "$source_dir" ]] || { printf \'%s\\n\' \'Source must be a directory.\' >&2; exit 1; }\n'
                      'case "$archive_path" in "$source_dir"|"$source_dir"/*) '
                      'printf \'%s\\n\' \'Archive must be outside the source directory.\' >&2; exit 1;; esac\n'
                      'tar -C "$source_dir" -czf - -- . > "$archive_path"\n'
                      'printf \'Created archive: %s\\n\' "$archive_path"\n',
                      "Create a gzip-compressed tar archive of a directory. Existing output files are "
                      "not overwritten; the archive must be outside the source directory. "
                      "With no paths in the request, supply source and archive as script arguments. "
                      "This script writes an archive only if you separately choose to run it.", "offline-template")
    if lower[:2] == ["find", "files"]:
        if len(words) == 2:
            pattern = "*"
        elif len(words) == 4 and lower[2] in {"named", "matching"}:
            pattern = words[3]
        else:
            raise ValueError("Use: find files, or find files named '*.txt'.")
        return _draft("set -euo pipefail\n" + f"pattern={shlex.quote(pattern)}\n" +
                      'find -P . -type f -name "$pattern" -print\n',
                      "List regular files below the current directory matching a literal filename "
                      "glob. Directory symlinks are not followed. No files are modified.", "offline-template")
    return None


def generate_script(request: str, offline: bool = True) -> ScriptDraft:
    """Create a previewable artifact, never a command eligible for direct execution."""
    if type(request) is not str or not request.strip() or len(request) > MAX_REQUEST_CHARS or "\0" in request:
        raise ValueError("Script request must be nonempty text up to 4000 characters.")
    local = _offline(request)
    if local is not None:
        return local
    if offline:
        raise ValueError("Offline script templates support storage/system reports, backup directory, "
                         "and find files named 'PATTERN'. Use online mode for other script requests.")
    key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_MODEL")
    if not key or not model:
        raise ValueError("Online script authoring requires GEMINI_API_KEY and GEMINI_MODEL.")
    try:
        from google import genai
        from google.genai import types
        with genai.Client(api_key=key) as client:
            response = client.models.generate_content(
                model=model, contents=request,
                config=types.GenerateContentConfig(
                    system_instruction=("Write a Bash script satisfying the user's request. Return only "
                        "a JSON object with exactly code and explanation string fields, without Markdown. "
                        "Code must be at most 32768 UTF-8 bytes. Explain every operation, file mutation, "
                        "network operation, destructive action and elevated permission requested. "
                        "Use quoted variable expansions and explain required script arguments. "
                        "This is an untrusted draft for human review, never executed by this application."),
                    response_mime_type="application/json", max_output_tokens=8192))
        if type(response.text) is not str or len(response.text.encode("utf-8")) > 128 * 1024:
            raise ValueError("Invalid response size.")
        data = json.loads(response.text)
        if type(data) is not dict or set(data) != {"code", "explanation"}:
            raise ValueError("Invalid script response fields.")
        return _draft(data["code"], data["explanation"], "online-model")
    except Exception:
        # Provider errors can include secrets or request content; do not surface them.
        raise ValueError("Online script generation failed or returned an invalid draft; nothing was saved.") from None


def save_script(draft: ScriptDraft, destination, workspace) -> Path:
    """Save an approved draft exclusively within workspace, without following links.

    The caller must obtain approval for these exact bytes before calling. This
    function never executes them. Existing directories are required; all path
    components are opened with O_NOFOLLOW and held via directory descriptors.
    """
    if (type(draft) is not ScriptDraft or type(draft.code) is not str
            or not draft.code.startswith(SHEBANG) or type(draft.explanation) is not str
            or not draft.explanation.strip() or len(draft.explanation) > 4000
            or _contains_controls(draft.explanation) or type(draft.source) is not str
            or draft.source not in {"offline-template", "online-model"}):
        raise ValueError("Expected a Bash ScriptDraft with the standard shebang.")
    validate_syntax(draft.code)
    base = Path(os.path.abspath(os.fspath(workspace)))
    requested = Path(destination)
    if ".." in requested.parts:
        raise ValueError("Script destination cannot contain '..'.")
    target = requested if requested.is_absolute() else base / requested
    try:
        relative = target.relative_to(base)
    except ValueError as exc:
        raise ValueError("Script destination must be inside the approved workspace.") from exc
    if not relative.parts:
        raise ValueError("Script destination must name a new file.")
    descriptors = []
    output_fd = None
    try:
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory_fd)
        for component in target.parent.parts[1:]:
            directory_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=directory_fd)
            descriptors.append(directory_fd)
        output_fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o700, dir_fd=directory_fd)
        os.fchmod(output_fd, 0o700)
        with os.fdopen(output_fd, "wb") as stream:
            output_fd = None
            stream.write(draft.code.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory_fd)
        return target
    except OSError as exc:
        raise ValueError("Cannot save script: destination must be a new file in existing, "
                         "non-symlink workspace directories.") from exc
    finally:
        if output_fd is not None:
            os.close(output_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
