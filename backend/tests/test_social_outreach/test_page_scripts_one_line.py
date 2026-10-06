"""Page scripts built from joined Python strings must not use // comments.

The posters build their browser scripts as adjacent string literals with no
newlines between them, so a // comment comments out everything after it and the
script fails to parse. Block comments are fine.
"""
import re
from pathlib import Path

import pytest

POSTERS = [
    "backend/services/social_outreach/reddit_outreach.py",
    "backend/services/social_outreach/youtube_outreach.py",
    "backend/services/social_outreach/general_poster.py",
    "backend/services/social_outreach/self_share.py",
]
ROOT = Path(__file__).resolve().parents[3]
LINE_COMMENT_IN_LITERAL = re.compile(r'^\s*"[^"]*?(?<![:\'"])//')


@pytest.mark.parametrize("path", POSTERS)
def test_no_line_comment_inside_a_joined_script(path):
    offenders = [
        (n, line.strip())
        for n, line in enumerate((ROOT / path).read_text().splitlines(), 1)
        if LINE_COMMENT_IN_LITERAL.search(line) and "://" not in line
    ]
    assert not offenders, f"// comment inside a joined script string in {path}: {offenders[:3]}"
