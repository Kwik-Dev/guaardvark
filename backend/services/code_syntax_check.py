"""Real, dependency-free syntax checks for the Code Editor.

The editor's Debug button and the code-intelligence /validate route used to call
tools that are not part of the install (flake8, eslint) or a model whose answer
was thrown away, and then report "no problems". This module is what both can
truthfully do on a stock machine: Python and JSON through the standard library,
JavaScript through `node --check` when node is on PATH. Anything else is
reported as unsupported rather than as clean.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile

# Languages the checker can actually parse, and the tool it uses for each.
SUPPORTED = {
    "python": "python-ast",
    "json": "json",
    "javascript": "node --check",
}

_NODE_LOCATION = re.compile(r":(\d+)(?::(\d+))?\s*$")


def _issue(line, column, message, code="syntax"):
    return {
        "line": int(line) if line else 1,
        "column": int(column) if column else 1,
        "code": code,
        "message": message,
        "severity": 2,
    }


def _check_python(code):
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return [_issue(exc.lineno, exc.offset, exc.msg or "invalid syntax")]
    return []


def _check_json(code):
    try:
        json.loads(code)
    except json.JSONDecodeError as exc:
        return [_issue(exc.lineno, exc.colno, exc.msg)]
    return []


def _check_javascript(code, node_path):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False)
    try:
        tmp.write(code)
        tmp.close()
        result = subprocess.run(
            [node_path, "--check", tmp.name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    if result.returncode == 0:
        return []
    return [parse_node_check_error(result.stderr, tmp.name)]


def parse_node_check_error(stderr, file_name=""):
    """Turn `node --check` stderr into one issue with the line it points at.

    node prints `<file>:<line>` first, then the offending source line, a caret
    line and `SyntaxError: <message>`.
    """
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
    line_no = 1
    message = "SyntaxError"
    for raw in lines:
        if file_name and raw.startswith(file_name):
            match = _NODE_LOCATION.search(raw)
            if match:
                line_no = int(match.group(1))
        elif raw.startswith("SyntaxError"):
            message = raw.strip()
    column = 1
    caret = next((ln for ln in lines if set(ln.strip()) == {"^"}), None)
    if caret is not None:
        column = caret.index("^") + 1
    return _issue(line_no, column, message)


def check_syntax(code, language):
    """Check `code` and say which tool did it.

    Returns {"supported", "tool", "errors", "warnings", "note"}. `supported` is
    False when nothing on this machine can parse the language; callers must then
    report "not checked", never "no problems".
    """
    lang = (language or "").lower()
    if lang == "python":
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_python(code), "warnings": [], "note": ""}
    if lang == "json":
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_json(code), "warnings": [], "note": ""}
    if lang == "javascript":
        node_path = shutil.which("node")
        if not node_path:
            return {
                "supported": False,
                "tool": None,
                "errors": [],
                "warnings": [],
                "note": "JavaScript syntax check needs node on PATH; it is not installed here.",
            }
        return {"supported": True, "tool": SUPPORTED[lang], "errors": _check_javascript(code, node_path), "warnings": [], "note": ""}
    return {
        "supported": False,
        "tool": None,
        "errors": [],
        "warnings": [],
        "note": f"No syntax checker for {language or 'this language'} on this machine.",
    }
