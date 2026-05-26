#!/usr/bin/env python3
"""
LlamaIndex Code Manipulation Tools for LLM Self-Improvement
Provides FunctionTools compatible with ReActAgent for code reading, searching, and editing.

These tools enable an LLM agent to:
1. Read source code files
2. Search for patterns across the codebase
3. Edit code files with automatic backups
4. List project structure
5. Run tests to verify changes

Milestone Goal: Enable LLM to remove "Snibbly Nips" button from SettingsPage.jsx
"""

import os
import logging
import subprocess
from pathlib import Path
from typing import List, Optional
import re

from backend.services.guarded_code_service import (
    GuardedCodeError,
    apply_exact_replacement,
    read_repo_file,
)

logger = logging.getLogger(__name__)

# Define the project root - 2 levels up from backend/tools
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_code(filepath: str) -> str:
    """
    Read the complete contents of a source code file.

    Args:
        filepath: Relative path from project root (e.g., "frontend/src/pages/SettingsPage.jsx")

    Returns:
        The file contents with metadata, or error message

    Example:
        content = read_code("frontend/src/pages/SettingsPage.jsx")
    """
    try:
        try:
            file_data = read_repo_file(filepath, repo_root=PROJECT_ROOT, allow_external=True)
        except GuardedCodeError as e:
            # Maybe the user just uploaded this to chat — those land under
            # data/uploads/, not PROJECT_ROOT, and have a Document row.
            if e.code == "FILE_NOT_FOUND":
                from backend.utils.uploaded_file_resolver import find_uploaded_file
                uploaded = find_uploaded_file(filepath)
                if uploaded:
                    content, on_disk = uploaded
                    if content is None and on_disk:
                        with open(on_disk, 'r', encoding='utf-8') as f:
                            content = f.read()
                    if content is not None:
                        line_count = len(content.split('\n'))
                        char_count = len(content)
                        logger.info(f"Read {line_count} lines from uploaded file {filepath}")
                        return f"""✓ Successfully read uploaded file: {filepath}
Lines: {line_count} | Characters: {char_count}

========== FILE CONTENT START ==========
{content}
========== FILE CONTENT END =========="""
            return f"ERROR reading '{filepath}': {e}"

        content = file_data["content"]
        line_count = len(content.split('\n'))
        char_count = len(content)
        display_path = file_data.get("relative_path") or filepath

        result = f"""✓ Successfully read: {display_path}
Lines: {line_count} | Characters: {char_count}

========== FILE CONTENT START ==========
{content}
========== FILE CONTENT END =========="""

        logger.info(f"Read {line_count} lines from {display_path}")
        return result

    except Exception as e:
        error_msg = f"ERROR reading '{filepath}': {str(e)}"
        logger.error(error_msg)
        return error_msg


def search_code(pattern: str, file_glob: str = "**/*.{py,jsx,js,tsx,ts}") -> str:
    """
    Search for a code pattern across files using case-insensitive regex.

    Args:
        pattern: Text or regex pattern to search for (e.g., "Snibbly Nips", "Button.*onClick")
        file_glob: Glob pattern for files to search (default: all Python/React files)

    Returns:
        Formatted string with all matches, including file paths and line numbers

    Example:
        results = search_code("Snibbly Nips")
        results = search_code("Button", "frontend/**/*.jsx")
    """
    try:
        matches = []

        # Convert glob pattern to list of patterns for common extensions
        if '{' in file_glob and '}' in file_glob:
            # Expand {py,jsx,js} syntax
            base_pattern = file_glob.split('{')[0]
            extensions = file_glob.split('{')[1].split('}')[0].split(',')
            file_patterns = [base_pattern + ext for ext in extensions]
        else:
            file_patterns = [file_glob]

        # Collect all matching files
        all_files = []
        for pattern_str in file_patterns:
            all_files.extend(PROJECT_ROOT.glob(pattern_str))

        for filepath in all_files:
            # Skip common directories
            path_str = str(filepath)
            if any(skip in path_str for skip in ['venv', 'node_modules', '.git', 'dist', '__pycache__', 'htmlcov']):
                continue

            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, start=1):
                        if re.search(pattern, line, re.IGNORECASE):
                            relative_path = filepath.relative_to(PROJECT_ROOT)
                            matches.append({
                                'file': str(relative_path),
                                'line': line_num,
                                'content': line.rstrip()
                            })
            except Exception as e:
                logger.debug(f"Skipping {filepath}: {e}")
                continue

        if not matches:
            return f"No matches found for pattern '{pattern}' in {file_glob}"

        # Format results
        result = f"✓ Found {len(matches)} matches for '{pattern}':\n\n"
        for i, match in enumerate(matches[:100], 1):  # Limit to 100 results
            result += f"{i}. {match['file']}:{match['line']}\n"
            result += f"   {match['content']}\n\n"

        if len(matches) > 100:
            result += f"... and {len(matches) - 100} more matches (showing first 100)\n"

        logger.info(f"Search for '{pattern}' found {len(matches)} matches")
        return result

    except Exception as e:
        error_msg = f"ERROR searching for '{pattern}': {str(e)}"
        logger.error(error_msg)
        return error_msg


def edit_code(filepath: str, old_text: str, new_text: str) -> str:
    """
    Edit a source code file by replacing exact text. CRITICAL: Creates automatic backup.

    Args:
        filepath: Relative path from project root
        old_text: The EXACT text to replace (must be unique in file)
        new_text: The new text to insert (can be empty string for deletion)

    Returns:
        Success message with backup info, or error message

    Security:
        - Only edits files within project root
        - Creates .backup file before any changes
        - Verifies exact match before editing
        - Rolls back on verification failure

    Example:
        # To remove the Snibbly Nips button:
        result = edit_code(
            "frontend/src/pages/SettingsPage.jsx",
            "      <Button variant=\"contained\" color=\"primary\">\n        Snibbly Nips\n      </Button>",
            ""
        )
    """
    try:
        edit_result = apply_exact_replacement(
            filepath,
            old_text,
            new_text,
            repo_root=PROJECT_ROOT,
            allow_external=True,
        )
        old_lines = len(old_text.split('\n'))
        new_lines = len(new_text.split('\n'))
        lines_diff = new_lines - old_lines

        result = f"""✓ Successfully edited '{edit_result.relative_path}'

Backup: {edit_result.backup_path}
Changes: {"Removed" if lines_diff < 0 else "Added" if lines_diff > 0 else "Modified"} {abs(lines_diff)} lines
Old text length: {len(old_text)} chars
New text length: {len(new_text)} chars

The file has been updated. You can verify by reading it with read_code().
"""

        logger.info(f"Successfully edited {edit_result.relative_path}: {lines_diff:+d} lines")
        return result
    except GuardedCodeError as e:
        error_msg = f"ERROR editing '{filepath}': {e}"
        if e.code in {"TEXT_NOT_FOUND", "TEXT_NOT_UNIQUE"}:
            error_msg += (
                "\n\nTIPS:\n"
                "1. Use read_code() first to get the exact text including whitespace\n"
                "2. Include enough surrounding context to make the match unique\n"
                f"\nSEARCHED FOR:\n{old_text[:200]}..."
            )
        logger.error(error_msg)
        return error_msg

    except Exception as e:
        error_msg = f"ERROR editing '{filepath}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def list_files(directory: str = "frontend/src/pages", max_depth: int = 5) -> str:
    """
    List files and directories to help understand project structure.

    Args:
        directory: Relative path from project root (default: "frontend/src/pages")
        max_depth: Maximum directory depth to show (default: 2)

    Returns:
        Formatted directory tree structure

    Example:
        structure = list_files("frontend/src/pages")
        structure = list_files("backend/api", max_depth=1)
    """
    try:
        full_path = PROJECT_ROOT / directory

        if not str(full_path.resolve()).startswith(str(PROJECT_ROOT)):
            return f"ERROR: Path '{directory}' is outside project root"

        if not full_path.exists():
            return f"ERROR: Path '{directory}' does not exist"

        result = f"✓ Directory structure: {directory}\n\n"

        def build_tree(dir_path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return ""

            tree = ""
            try:
                items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name))

                # Filter out common skip directories
                items = [item for item in items if item.name not in [
                    'venv', 'node_modules', '.git', '__pycache__', 'dist',
                    '.pytest_cache', 'htmlcov', '.coverage', 'build', 'logs'
                ]]

                for i, item in enumerate(items):
                    is_last = i == len(items) - 1
                    current_prefix = "└── " if is_last else "├── "

                    if item.is_dir():
                        tree += f"{prefix}{current_prefix}{item.name}/\n"
                        extension = "    " if is_last else "│   "
                        tree += build_tree(item, prefix + extension, depth + 1)
                    else:
                        # Show file with size
                        size = item.stat().st_size
                        size_str = f"{size:,}B" if size < 1024 else f"{size/1024:.1f}KB"
                        tree += f"{prefix}{current_prefix}{item.name} ({size_str})\n"

            except PermissionError:
                tree += f"{prefix}[Permission Denied]\n"

            return tree

        result += build_tree(full_path)
        logger.info(f"Listed structure for '{directory}' (depth: {max_depth})")
        return result

    except Exception as e:
        error_msg = f"ERROR listing '{directory}': {str(e)}"
        logger.error(error_msg)
        return error_msg


def verify_change(filepath: str, expected_text: str, should_exist: bool = True) -> str:
    """
    Verify that a code change was successful by checking if text exists in file.

    Args:
        filepath: Relative path from project root
        expected_text: Text that should (or shouldn't) exist after the edit
        should_exist: True if text should exist, False if it should be gone

    Returns:
        Verification result message

    Example:
        # After removing Snibbly Nips, verify it's gone:
        result = verify_change("frontend/src/pages/SettingsPage.jsx", "Snibbly Nips", should_exist=False)
    """
    try:
        file_data = read_repo_file(filepath, repo_root=PROJECT_ROOT, allow_external=True)
        content = file_data["content"]
        display_path = file_data.get("relative_path") or filepath

        text_found = expected_text in content

        if should_exist:
            if text_found:
                return f"✓ VERIFIED: Text '{expected_text[:50]}...' exists in '{display_path}'"
            else:
                return f"✗ VERIFICATION FAILED: Expected text not found in '{display_path}'"
        else:
            if not text_found:
                return f"✓ VERIFIED: Text '{expected_text[:50]}...' successfully removed from '{display_path}'"
            else:
                return f"✗ VERIFICATION FAILED: Text still exists in '{display_path}' (should have been removed)"
    except GuardedCodeError as e:
        return f"ERROR during verification: {e}"

    except Exception as e:
        return f"ERROR during verification: {str(e)}"


# Export functions for LlamaIndex FunctionTool creation
__all__ = [
    'read_code',
    'search_code',
    'edit_code',
    'list_files',
    'verify_change'
]


if __name__ == "__main__":
    # Test the tools
    print("=== Testing Code Tools for LLM Self-Improvement ===\n")

    # Test 1: Search for Snibbly Nips
    print("1. Searching for 'Snibbly Nips':")
    result = search_code("Snibbly.*Nips")
    print(result[:500])
    print()

    # Test 2: List Settings pages
    print("2. Frontend pages:")
    result = list_files("frontend/src/pages", max_depth=1)
    print(result[:500])
    print()

    print("✓ Code tools ready for ReActAgent integration")
