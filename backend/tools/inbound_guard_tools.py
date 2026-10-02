"""Ask the inbound guard about a change before making it.

The code agent calls this on its own edit before proposing it, so a finding
shapes the edit instead of holding it afterwards. An outside agent reviewing a
branch over MCP reads a git range the same way. It judges and reports only:
nothing is recorded and nothing is held, whatever the guard's mode.
"""
from __future__ import annotations

import logging
import re

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.display_paths import display_text

logger = logging.getLogger(__name__)

# A revision or a range of two; never an option, so a value cannot reach git as a flag.
_RANGE = re.compile(r"^[A-Za-z0-9_./~^@{}][A-Za-z0-9_./~^@{}-]*(\.\.\.?[A-Za-z0-9_./~^@{}][A-Za-z0-9_./~^@{}-]*)?$")
MAX_FINDINGS = 25


class CheckInboundChangeTool(BaseTool):
    name = "check_inbound_change"
    read_only = True
    observation_chars = 4000
    description = (
        "Read a code change the way Guaardvark's inbound guard will before it lands, and say whether it "
        "would be allowed, held for a person, or blocked, with each finding (rule, file:line, why). "
        "Give either a git range of this checkout (range, e.g. 'main...my-branch') or one file's change: "
        "filepath plus new_text, and old_text when new_text replaces that exact text in the file (without "
        "old_text, new_text is the whole new file). Reads only; records nothing and holds nothing."
    )
    parameters = {
        "filepath": ToolParameter(
            name="filepath", type="string", required=False,
            description="File the change is for, relative to the checkout root, e.g. 'backend/app.py'.",
        ),
        "new_text": ToolParameter(
            name="new_text", type="string", required=False,
            description="The replacement text, or the whole new file when old_text is not given.",
        ),
        "old_text": ToolParameter(
            name="old_text", type="string", required=False,
            description="Exact text in filepath that new_text replaces.",
        ),
        "range": ToolParameter(
            name="range", type="string", required=False,
            description="A git range of this checkout to read instead, e.g. 'main...feature' or 'HEAD~3..HEAD'.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        from backend.services import inbound_guard_service as guard
        from backend.services.guarded_code_service import GuardedCodeError, resolve_repo_path

        rng = (kwargs.get("range") or "").strip()
        filepath = (kwargs.get("filepath") or "").strip()
        new_text = kwargs.get("new_text")
        old_text = kwargs.get("old_text")
        try:
            engine = guard.engine()
            if rng:
                if not _RANGE.match(rng):
                    return ToolResult(success=False, error="range must be a revision or A..B / A...B")
                verdict = engine.scan_diff(guard.REPO_ROOT, [rng], source="check", subject=rng, mode="observe",
                                           scanners=guard.scanners())
            elif filepath and new_text is not None:
                path, rel = resolve_repo_path(filepath)
                current = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
                if old_text:
                    if current is None or current.count(old_text) != 1:
                        return ToolResult(success=False,
                                          error="old_text must appear exactly once in filepath")
                    change = guard.change_for_replacement(path, current, old_text, new_text)
                else:
                    change = guard.change_for_file(path, current, new_text)
                verdict = engine.scan([change], source="check", subject=rel, mode="observe", repo=guard.REPO_ROOT,
                                      scanners=guard.scanners())
            else:
                return ToolResult(success=False, error="Give range, or filepath with new_text.")
        except GuardedCodeError as exc:
            return ToolResult(success=False, error=display_text(str(exc)))
        except Exception as exc:
            logger.error("check_inbound_change failed: %s", exc, exc_info=True)
            return ToolResult(success=False, error=display_text(f"Inbound check failed: {exc}"))

        lines = [f"Inbound guard: {verdict.verdict.upper()} — {len(verdict.findings)} finding(s) over "
                 f"{verdict.files} file(s), +{verdict.added_lines} line(s)."]
        if verdict.verdict == "allow":
            lines.append("Nothing here would be held.")
        for finding in verdict.findings[:MAX_FINDINGS]:
            where = f"{finding.path}:{finding.line}" if finding.line else finding.path
            lines.append(f"- {finding.severity} {finding.rule} at {where}: {finding.why}")
            if finding.excerpt:
                lines.append(f"    {finding.excerpt}")
        if len(verdict.findings) > MAX_FINDINGS:
            lines.append(f"- and {len(verdict.findings) - MAX_FINDINGS} more")
        return ToolResult(
            success=True,
            output=display_text("\n".join(lines)),
            metadata={"verdict": verdict.verdict, "findings": len(verdict.findings), "digest": verdict.digest},
        )
