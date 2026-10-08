#!/usr/bin/env python3
"""
Generation Tools
Executable tools for bulk and batch file generation operations.
Wraps existing generation services for agent system integration.
"""

import csv
import io
import logging
import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import is_mcp_transport

from backend.utils.path_safety import safe_join
logger = logging.getLogger(__name__)

_FENCE = "```"

# File types whose own content can hold fenced code blocks, so a fence in the
# model's reply is not proof of a wrapper; and the fence tags that do mark one.
_PROSE_EXTENSIONS = {"", ".md", ".markdown", ".mdx", ".txt", ".rst"}
_PROSE_FENCE_TAGS = {"markdown", "md", "mdx", "text", "txt", "plaintext", "rst"}


def _reply_text(response) -> str:
    """The text of an LLM chat response, whether content or blocks carry it."""
    if not response.message:
        return ""
    try:
        content = response.message.content
    except (ValueError, AttributeError):
        blocks = getattr(response.message, 'blocks', [])
        return next((getattr(b, 'text', str(b)) for b in blocks if getattr(b, 'text', None)), "").strip()
    return str(content).strip() if content is not None else ""


def _file_body(reply: str, filename: str) -> str:
    """The file content inside a model reply that wraps it in a code fence,
    with or without a sentence before or after the fence."""
    lines = reply.split("\n")
    fences = [i for i, line in enumerate(lines) if line.strip().startswith(_FENCE)]
    if not fences:
        return reply
    first, last = fences[0], fences[-1]
    closed = last > first and lines[last].strip() == _FENCE
    before = any(line.strip() for line in lines[:first])
    after = any(line.strip() for line in lines[last + 1:])
    prose = os.path.splitext(str(filename or ""))[1].lower() in _PROSE_EXTENSIONS

    if not before and len(fences) == 1:
        # An opening fence that never closed.
        body = lines[first + 1:]
    elif not before and not after and closed and (prose or len(fences) == 2):
        # The whole reply is one fenced block. In a Markdown or text file that
        # wrapper may hold code blocks of its own.
        body = lines[first + 1:last]
    elif prose:
        if lines[first].strip()[len(_FENCE):].strip().lower() not in _PROSE_FENCE_TAGS:
            # A code block inside a Markdown or text file is the file's own content.
            return reply
        # A ```markdown wrapper can hold code blocks of its own: it ends at the last fence.
        body = lines[first + 1:last if closed else len(lines)]
    elif len(fences) == 1:
        # One fence line: an opener with the file after it, or a stray closer
        # with the file before it.
        body = lines[first + 1:] if after else lines[:first]
    else:
        body = lines[first + 1:fences[1]]
    return "\n".join(body).strip("\n")


def _fenced_blocks(text: str) -> List[str]:
    """Bodies of the fenced code blocks in a model reply, in order. An opening
    fence with no closing one runs to the end of the reply."""
    blocks: List[str] = []
    body: Optional[List[str]] = None
    for line in text.split("\n"):
        if line.strip().startswith(_FENCE):
            if body is None:
                body = []
            else:
                blocks.append("\n".join(body))
                body = None
        elif body is not None:
            body.append(line)
    if body is not None:
        blocks.append("\n".join(body))
    return blocks


def _read_records(text: str) -> List[List[str]]:
    # strict: a stray quote inside a quoted field is an error, not a silently
    # mangled field. skipinitialspace: models write '"a", "b"'.
    return list(csv.reader(io.StringIO(text), strict=True, skipinitialspace=True))


def _is_blank(record: List[str]) -> bool:
    return len(record) <= 1 and not "".join(record).strip()


def _table_in(records: List[List[str]]) -> Tuple[List[List[str]], int, Optional[str]]:
    """(rows, column count, problem) for one run of records with no blank line in it.

    The column count is the one most rows share. A line of prose at either end
    (a single field beside a wider table, or a leading line ending in ':') is
    dropped. Any other row of a different width is a problem: dropping it could
    turn a data row into the header.
    """
    rows = list(records)
    while len(rows) > 1 and rows[0][-1].rstrip().endswith(":"):
        rows = rows[1:]
    counts = Counter(len(row) for row in rows)
    most = max(counts.values())
    width = max(n for n, seen in counts.items() if seen == most)
    while rows and width > 1 and len(rows[0]) == 1:
        rows = rows[1:]
    while rows and width > 1 and len(rows[-1]) == 1:
        rows = rows[:-1]
    for number, row in enumerate(rows, 1):
        if len(row) != width:
            return rows, width, f"row {number} has {len(row)} column(s) where most rows have {width}"
    return rows, width, None


def _csv_table(reply: str) -> Tuple[List[List[str]], int, Optional[str]]:
    """The table in a model's reply, as (rows, column count, problem).

    Looks inside code fences when the reply has any, and otherwise at the text
    itself, where blank lines separate a table from prose around it. Of several
    candidates the one with the most rows wins, a table of two or more columns
    before a single column (which lines of prose also look like).
    """
    candidates = [block for block in _fenced_blocks(reply) if block.strip()]
    if not candidates:
        candidates = ["\n".join(
            line for line in reply.split("\n") if not line.strip().startswith(_FENCE)
        )]

    runs: List[List[List[str]]] = []
    parse_error = None
    for text in candidates:
        try:
            pieces = [_read_records(text)]
        except csv.Error as e:
            # One unparsable line (prose that opens with a quote, say) must not
            # hide a table elsewhere in the reply: read each paragraph on its own.
            parse_error = parse_error or str(e)
            pieces = []
            for paragraph in re.split(r"\n\s*\n", text):
                try:
                    pieces.append(_read_records(paragraph))
                except csv.Error:
                    continue
        for records in pieces:
            run: List[List[str]] = []
            for record in records + [[]]:
                if not _is_blank(record):
                    run.append(record)
                elif run:
                    runs.append(run)
                    run = []

    best = None
    for run in runs:
        rows, width, problem = _table_in(run)
        rank = (width >= 2, len(rows))
        if rows and (best is None or rank > best[0]):
            best = (rank, rows, width, problem)
    if parse_error and (best is None or best[0] < (True, 2)):
        # What did parse is a line of prose; the table is the part that did not.
        return [], 0, f"it is not valid CSV ({parse_error})"
    if best is None:
        return [], 0, "it holds no rows"
    return best[1], best[2], best[3]


class BulkCSVGeneratorTool(BaseTool):
    """Start a Studio bulk job that writes a WordPress import CSV, one page per row."""

    name = "generate_bulk_csv"
    read_only = False
    # Adds a new file under a name that no file and no running bulk job holds.
    destructive = False
    description = (
        "Start a background job (the Studio's Bulk Generation job) that writes a WordPress import CSV "
        "with Guaardvark's local LLM: a header row, then one page per row with ID, Title, Content "
        "(HTML), Excerpt, Category, Tags, slug. Row N is about '<topic> - Part N' (with quantity 1, the "
        "topic itself). When client matches a client saved in Guaardvark, its saved details go into "
        "the prompt. Returns at once with job_id and the file's path in the outputs folder; the name "
        "gets a -001 style suffix if a file or a running bulk job already has it. Rows are generated "
        "one at a time and the file is written when the job ends; each model call can take up to "
        "180 s, a failing row is tried up to 4 times and then left out, and a job where fewer than a "
        "quarter of the rows succeed (30% above 10 rows) fails and leaves no file. Over MCP the "
        "Guaardvark backend must be running. Poll get_generation_status with job_id; 'complete' "
        "reports how many rows the file holds. For one page returned as text use "
        "generate_wordpress_content or generate_enhanced_wordpress_content; for a table of arbitrary "
        "data, generate_csv."
    )

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Plain file name for the CSV, e.g. 'spring-pages.csv' (no folders). Unsafe characters are replaced; if a file or a running bulk job has the name, a -001 style suffix is added and the name used is returned."
        ),
        "quantity": ToolParameter(
            name="quantity",
            type="int",
            required=True,
            minimum=1,
            maximum=5000,
            description="How many pages (rows) to ask for, 1-5000; a row that keeps failing is left out. With 1 the topic is used as is; otherwise row N is about '<topic> - Part N'."
        ),
        "topic": ToolParameter(
            name="topic",
            type="string",
            required=True,
            description="What the pages are about, e.g. 'gutter maintenance'."
        ),
        "client": ToolParameter(
            name="client",
            type="string",
            required=False,
            description="Company the pages are for (default 'Professional Services'). Matched case-insensitively to clients saved in Guaardvark; a match adds its saved details to the prompt.",
            default=""
        ),
        "website": ToolParameter(
            name="website",
            type="string",
            required=False,
            description="The company's website, e.g. 'example.com' (default 'website.com'); given to the model and used in the row IDs.",
            default=""
        ),
        "project": ToolParameter(
            name="project",
            type="string",
            required=False,
            description="Project name given to the model (default 'Content Generation').",
            default=""
        ),
        "word_count": ToolParameter(
            name="word_count",
            type="int",
            required=False,
            minimum=100,
            description="Words of HTML content the model is asked for per page (default 600). An instruction, not enforced: only pages under about 30 words are regenerated.",
            default=600
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        filename = str(kwargs.get("filename") or "").strip()
        topic = str(kwargs.get("topic") or "").strip()
        try:
            quantity = int(kwargs.get("quantity"))
        except (TypeError, ValueError):
            return ToolResult(success=False, error="quantity must be a whole number from 1 to 5000")
        if not 1 <= quantity <= 5000:
            return ToolResult(success=False, error="quantity must be from 1 to 5000")
        if not filename or not topic:
            return ToolResult(success=False, error="filename and topic are required")
        if "/" in filename or "\\" in filename or filename.startswith("."):
            return ToolResult(success=False, error=f"filename must be a plain file name like 'pages.csv', not '{filename}'")

        payload = {
            "output_filename": filename,
            "num_items": quantity,
            "topics": [topic] if quantity == 1 else [f"{topic} - Part {i}" for i in range(1, quantity + 1)],
            "target_word_count": int(kwargs.get("word_count") or 600),
        }
        for key in ("client", "website", "project"):
            value = str(kwargs.get(key) or "").strip()
            if value:
                payload[key] = value

        from flask import has_app_context
        if is_mcp_transport(self) or not has_app_context():
            from backend.utils.backend_http import BackendError, request_json
            try:
                reply = request_json("POST", "/api/bulk-generate/csv", payload=payload)
            except BackendError as e:
                return ToolResult(success=False, error=str(e))
            body, status = reply.body or {}, reply.status
        else:
            from backend.api.bulk_generation_api import start_bulk_csv_job
            response = start_bulk_csv_job(payload)
            flask_response, status = response if isinstance(response, tuple) else (response, response.status_code)
            body = flask_response.get_json(silent=True) or {}
        if status >= 400 or not body.get("job_id"):
            return ToolResult(success=False, error=body.get("error") or f"The bulk job did not start (HTTP {status})")

        output_name = body.get("output_filename") or filename
        from backend.config import GUAARDVARK_ROOT, OUTPUT_DIR
        out_dir = Path(OUTPUT_DIR).resolve()
        root = Path(GUAARDVARK_ROOT).resolve()
        shown_dir = out_dir.relative_to(root).as_posix() if out_dir.is_relative_to(root) else str(out_dir)
        return ToolResult(
            success=True,
            output={
                "job_id": body["job_id"],
                "status": "processing",
                "rows_requested": quantity,
                "output_file": f"{shown_dir}/{output_name}",
                "next": "Poll get_generation_status with this job_id; the file is complete when it reports complete.",
            },
            metadata={"quantity": quantity, "topic": topic, "filename": output_name},
        )


class FileGeneratorTool(BaseTool):
    """
    General file generator for any file type.
    Converted from /createfile command rule (rule ID: 2).

    Generates file content based on user instructions without explanations.
    """

    name = "generate_file"
    read_only = False
    # Writes to the filename it is given, replacing a file of that name.
    destructive = True
    description = (
        "Create a brand-NEW output file from a description, written under data/outputs/files "
        "(a file already at that name there is replaced; an empty model reply is an error and "
        "writes nothing). "
        "It generates from the description ALONE and never reads any existing file. "
        "Do NOT use it to improve, refactor, modify, or produce a new version of an existing or "
        "uploaded file — it cannot see that file and would fabricate. For that, use `codegen` "
        "with input_file=<path> (grounded copy), or `edit_code` in Guaardvark's chat for an "
        "in-place repo change (not offered over MCP). For a CSV table use generate_csv; for "
        "a WordPress import file with a page per row, generate_bulk_csv."
    )

    # Verbs that signal "change something that already exists" rather than
    # "make a new file". Used to refuse ungrounded modify-existing requests.
    _MODIFY_VERBS = (
        "improve", "enhance", "optimize", "optimise", "refactor", "modify",
        "rewrite", "clean up", "cleanup", "improved version", "better version",
        "fix the", "update the", "based on the existing", "based on the uploaded",
    )
    # Wording that points at a file the caller already has.
    _EXISTING_REFERENCES = (
        "the existing", "the uploaded", "the attached", "the current", "the original",
        "my existing", "my current", "my uploaded", "our existing",
    )
    # How many words before a file's mention may hold the verb or reference
    # that targets it ("improve the uploaded quality_gate.py" is two apart).
    _TARGET_WINDOW_WORDS = 6
    # A modify verb aimed at the output file without naming it: "improve it".
    _PRONOUN_TARGET = re.compile(r"\s*(?:the |this |that )?(?:it|this|that|file|code)\b")

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Relative output filename with extension; nested paths are allowed (e.g., 'frontend/src/page.jsx')"
        ),
        "content_description": ToolParameter(
            name="content_description",
            type="string",
            required=True,
            description="Description of what the file should contain"
        ),
        "file_type": ToolParameter(
            name="file_type",
            type="string",
            required=False,
            description="File type hint (code, document, data, config)",
            default="auto"
        ),
        "save_to_disk": ToolParameter(
            name="save_to_disk",
            type="bool",
            required=False,
            description="Whether to save the file to disk",
            default=True
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm
            self._llm = get_default_llm()
        return self._llm

    def _detect_file_type(self, filename: str) -> str:
        """Detect file type from extension"""
        ext = os.path.splitext(filename)[1].lower()
        code_extensions = {'.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.cpp', '.c', '.h', '.go', '.rs', '.rb', '.php'}
        data_extensions = {'.json', '.csv', '.xml', '.yaml', '.yml'}
        doc_extensions = {'.md', '.txt', '.rst', '.html'}
        config_extensions = {'.ini', '.cfg', '.conf', '.env', '.toml'}

        if ext in code_extensions:
            return "code"
        elif ext in data_extensions:
            return "data"
        elif ext in doc_extensions:
            return "document"
        elif ext in config_extensions:
            return "config"
        return "unknown"

    def _targets(self, desc_l: str, mention: str) -> bool:
        """True when a modify verb or a reference to an existing file sits in
        the few words just before a mention of ``mention`` in the description."""
        cues = self._MODIFY_VERBS + self._EXISTING_REFERENCES
        pattern = r"(?<![\w.-])" + re.escape(mention.lower()) + r"(?![\w-])"
        for m in re.finditer(pattern, desc_l):
            window = " ".join(desc_l[:m.start()].split()[-self._TARGET_WINDOW_WORDS:])
            if any(cue in window for cue in cues):
                return True
        return False

    def _verb_on_pronoun(self, desc_l: str) -> bool:
        """True for "improve it", "refactor this file" and the like."""
        for verb in self._MODIFY_VERBS:
            for m in re.finditer(re.escape(verb), desc_l):
                if self._PRONOUN_TARGET.match(desc_l, m.end()):
                    return True
        return False

    @staticmethod
    def _resolves(name: str) -> bool:
        """True when ``name`` is real content this tool would not read: an
        uploaded or indexed document, or a file in the Guaardvark checkout."""
        try:
            from backend.utils.uploaded_file_resolver import find_uploaded_file
            if find_uploaded_file(name):
                return True
        except Exception:
            pass
        try:
            from backend.services.guarded_code_service import read_repo_file
            read_repo_file(name)
            return True
        except Exception:
            return False

    def _detect_modify_existing(self, filename, content_description):
        """Detect a request to improve/modify a file that already exists.

        generate_file builds its output from the description alone and never
        reads source, so honoring such a request would fabricate a "version"
        of a file it never saw. When that's what's being asked, return the
        referenced filename so the caller can refuse and redirect. Returns
        None when this is a legitimate new-file request.

        A name that merely exists somewhere is not a request to change it:
        every install has a README.md, LICENSE and start.sh at its root, and
        a new file of that name is written to the outputs folder, not over
        them. A file counts as targeted only when the description aims a
        modify verb or an existing-file reference at it.
        """
        desc = content_description or ""
        desc_l = desc.lower()

        # Files named in the description, then the output file itself, which
        # the description may name by its stem ("improve the README").
        named = [c.strip() for c in re.findall(r"[\w./-]+\.[A-Za-z0-9]+", desc) if c.strip()]
        for cand in named:
            if self._targets(desc_l, cand) and self._resolves(cand):
                return cand

        if filename:
            base = os.path.basename(str(filename).strip())
            stem = os.path.splitext(base)[0]
            aimed = (
                (base and self._targets(desc_l, base))
                # A one- or two-letter stem would match ordinary words.
                or (len(stem) >= 3 and self._targets(desc_l, stem))
                or self._verb_on_pronoun(desc_l)
            )
            if base and aimed and self._resolves(base):
                return base

        # Weaker signal: the wording explicitly targets an existing file even
        # if we can't resolve it right now. Still ungrounded here.
        has_verb = any(v in desc_l for v in self._MODIFY_VERBS)
        if has_verb and re.search(r"\b(this|the existing|the uploaded|the current)\b[\w\s]*\bfile\b", desc_l):
            return next(iter(named), filename)

        return None

    def _resolve_output_path(self, output_dir: str, filename: str) -> str:
        """Resolve a relative output filename safely beneath the output directory."""
        if not filename or not str(filename).strip():
            raise ValueError("Filename is required")

        raw_filename = str(filename).strip().replace("\\", "/")
        relative_path = Path(raw_filename)

        if relative_path.is_absolute():
            raise ValueError("Filename must be a relative path inside the output directory")

        if any(part in ("", ".", "..") for part in relative_path.parts):
            raise ValueError("Filename cannot contain empty, current-directory, or parent-directory segments")

        output_root = Path(output_dir).resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        output_path = (output_root / relative_path).resolve()
        try:
            output_path.relative_to(output_root)
        except ValueError:
            raise ValueError("Filename resolves outside the output directory")

        if output_path == output_root:
            raise ValueError("Filename must point to a file, not the output directory")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        return str(output_path)

    def execute(self, **kwargs) -> ToolResult:
        """Generate file content"""
        filename = kwargs.get("filename")
        content_description = kwargs.get("content_description")
        file_type = kwargs.get("file_type", "auto")
        save_to_disk = kwargs.get("save_to_disk", True)

        try:
            # Refuse ungrounded "improve an existing file" requests. This tool
            # never reads source, so producing an "improved version" of a real
            # file would be fabrication. Redirect to the grounded tools.
            referenced = self._detect_modify_existing(filename, content_description)
            if referenced:
                return ToolResult(
                    success=False,
                    error=(
                        f"generate_file cannot improve or modify an existing file. It writes a "
                        f"brand-new file from your description and never reads '{referenced}', so "
                        f"any 'improved version' would be fabricated. Use `codegen` with "
                        f"input_file='{referenced}' to generate a grounded modified copy, or "
                        f"`edit_code` in Guaardvark's chat to change the file in place."
                    ),
                )

            # Detect file type if auto
            if file_type == "auto":
                file_type = self._detect_file_type(filename)

            output_path = None
            if save_to_disk:
                from backend.config import OUTPUT_DIR
                output_dir = os.path.join(OUTPUT_DIR, "files")
                output_path = self._resolve_output_path(output_dir, filename)

            llm = self._get_llm()

            # Build generation prompt
            prompt = f"""You are a file generator. Generate ONLY the file content requested.
Do not include any explanations, meta-text, or markdown code fences.
Output the actual file content that should be saved.

Filename: {filename}
File Type: {file_type}
Request: {content_description}

Generate the file content now:"""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            reply = _reply_text(llm.chat(messages))
            if not reply:
                return ToolResult(
                    success=False,
                    error="The model returned an empty reply, so nothing was written. Try again.",
                )
            file_content = _file_body(reply, filename)
            if not file_content.strip():
                return ToolResult(
                    success=False,
                    error=(
                        "The model returned no file content (an empty code block), so nothing "
                        f"was written. Its reply began: {reply[:200]!r}"
                    ),
                )

            if save_to_disk:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(file_content)

            return ToolResult(
                success=True,
                output={
                    "content": file_content,
                    "output_path": output_path,
                    "filename": filename,
                    "file_type": file_type,
                    "content_length": len(file_content)
                },
                metadata={
                    "filename": filename,
                    "file_type": file_type,
                    "saved": save_to_disk,
                    "destination": "output_dir"
                }
            )

        except Exception as e:
            logger.error(f"File generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"File generation failed: {str(e)}"
            )


class CSVGeneratorTool(BaseTool):
    """
    Single CSV file generator.
    Converted from /createcsv command rule (rule ID: 3).

    Generates CSV data based on user specifications.
    """

    name = "generate_csv"
    read_only = False
    # Writes to the filename it is given, replacing a file of that name.
    destructive = True
    description = (
        "Write one CSV table with Guaardvark's local LLM from a description of the data: which "
        "columns it has and what the rows hold. The model invents the rows from the description "
        "alone (it reads no file and no indexed document) and writes the whole table in one reply, "
        "cut off after 180 s. The reply is parsed as CSV and saved in the outputs folder under "
        "csv/<filename>, replacing a file of that name. Returns the path, the column count and "
        "names, and row_count: the data rows written, header not counted, next to rows_requested. "
        "Nothing is saved and the call is an error when the reply is empty, holds no table, has "
        "only a header, or has rows of different widths; text and code fences around the table "
        "are left out. For a WordPress import file with a generated page per row use "
        "generate_bulk_csv (a background job polled with get_generation_status); for any other "
        "kind of file, generate_file."
    )

    parameters = {
        "filename": ToolParameter(
            name="filename",
            type="string",
            required=True,
            description="Name for the file, e.g. 'fruit-prices.csv'; '.csv' is added when the name has no extension, and another extension is refused. A relative path such as 'reports/q3.csv' creates the folders under csv/. Checked before the model runs."
        ),
        "data_description": ToolParameter(
            name="data_description",
            type="string",
            required=True,
            description="What the table holds: its columns and the kind of rows, e.g. 'name, country and founding year of European football clubs'."
        ),
        "include_headers": ToolParameter(
            name="include_headers",
            type="bool",
            required=False,
            description="true (default): the first row names the columns. false: data rows only.",
            default=True
        ),
        "row_count": ToolParameter(
            name="row_count",
            type="int",
            required=False,
            minimum=1,
            description="Data rows to ask the model for (default 10). An instruction, not enforced: the result reports how many it wrote.",
            default=10
        )
    }

    def __init__(self):
        super().__init__()
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from backend.utils.llm_service import get_default_llm
            self._llm = get_default_llm()
        return self._llm

    @staticmethod
    def _target(output_dir: str, filename: Any) -> Tuple[str, str]:
        """(absolute path, name relative to the csv folder) for a requested file
        name. Raises ValueError with the reason when the name cannot be used.
        Creates nothing, so a refused name costs no model call and leaves no folder."""
        name = str(filename or "").strip().replace("\\", "/")
        if not name:
            raise ValueError("filename is required, e.g. 'fruit-prices.csv'")
        relative = PurePosixPath(name)
        if relative.is_absolute() or name.endswith("/") or any(
            part in ("", ".", "..") for part in relative.parts
        ):
            raise ValueError(
                f"filename must be a file name or a relative path inside the csv outputs "
                f"folder, without '.' or '..' parts, not '{name}'"
            )
        if relative.name.startswith("."):
            raise ValueError(f"filename must not start with a dot: '{relative.name}'")
        if not relative.suffix:
            relative = relative.with_name(relative.name + ".csv")
        elif relative.suffix.lower() != ".csv":
            raise ValueError(
                f"generate_csv writes CSV; give a name ending in .csv, not '{relative.suffix}'"
            )
        root = Path(output_dir).resolve()
        try:
            target = Path(safe_join(str(root), *relative.parts)).resolve()
            target.relative_to(root)
        except ValueError:
            raise ValueError(f"filename leaves the csv outputs folder: '{name}'")
        if target.is_dir():
            raise ValueError(f"'{relative}' is a folder in the csv outputs folder, not a file")
        return str(target), str(relative)

    def execute(self, **kwargs) -> ToolResult:
        """Generate CSV file"""
        data_description = str(kwargs.get("data_description") or "").strip()
        include_headers = kwargs.get("include_headers", True)
        if isinstance(include_headers, str):
            include_headers = include_headers.strip().lower() not in ("false", "no", "0", "off")
        asked = kwargs.get("row_count")
        try:
            row_count = 10 if asked is None else int(asked)
        except (TypeError, ValueError):
            return ToolResult(success=False, error="row_count must be a whole number of 1 or more")
        if row_count < 1:
            return ToolResult(success=False, error="row_count must be 1 or more")
        if not data_description:
            return ToolResult(success=False, error="data_description is required: say what the table holds")

        try:
            from backend.config import OUTPUT_DIR
            try:
                output_path, filename = self._target(os.path.join(OUTPUT_DIR, "csv"), kwargs.get("filename"))
            except ValueError as e:
                return ToolResult(success=False, error=str(e))

            llm = self._get_llm()

            prompt = f"""Generate a CSV file based on these specifications.
Output ONLY valid CSV data, no explanations.

Filename: {filename}
Description: {data_description}
Include Headers: {include_headers}
Number of Rows: {row_count}

Requirements:
- Use proper CSV formatting with quoted strings where needed
- Ensure consistent column count across all rows
- Generate realistic, varied data

Generate the CSV content now:"""

            from backend.utils.llm_service import ChatMessage, MessageRole
            messages = [ChatMessage(role=MessageRole.USER, content=prompt)]
            reply = _reply_text(llm.chat(messages))
            if not reply:
                return ToolResult(
                    success=False,
                    error="The model returned an empty reply, so no CSV was written. Try again.",
                )

            rows, column_count, problem = _csv_table(reply)
            if not problem and include_headers and len(rows) < 2:
                problem = "it holds a single row, where a header and at least one data row are needed"
            if problem:
                return ToolResult(
                    success=False,
                    error=(
                        f"The model's reply is not a usable CSV table: {problem}. Nothing was "
                        f"written. Its reply began: {reply[:200]!r}"
                    ),
                )

            # Written from the parsed rows, not the reply text, so quoting is the
            # csv module's and the file holds the table and nothing else.
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            csv_content = buffer.getvalue()
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8', newline='') as f:
                f.write(csv_content)

            data_rows = len(rows) - (1 if include_headers else 0)
            output = {
                "output_path": output_path,
                "filename": filename,
                "row_count": data_rows,
                "rows_requested": row_count,
                "column_count": column_count,
                "content_preview": csv_content[:500] + "..." if len(csv_content) > 500 else csv_content
            }
            if include_headers:
                output["columns"] = rows[0]
            if data_rows != row_count:
                output["note"] = f"The model wrote {data_rows} data row(s), not the {row_count} asked for."

            return ToolResult(
                success=True,
                output=output,
                metadata={
                    "filename": filename,
                    "rows_generated": data_rows,
                    "rows_requested": row_count,
                    "columns": column_count,
                    "has_headers": include_headers
                }
            )

        except Exception as e:
            logger.error(f"CSV generation failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"CSV generation failed: {str(e)}"
            )
