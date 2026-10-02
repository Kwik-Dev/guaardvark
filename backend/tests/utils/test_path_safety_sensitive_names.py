"""Which file names path_safety.is_sensitive refuses, and which it must not."""

import subprocess
from pathlib import Path

import pytest

from backend.utils.path_safety import SENSITIVE_PATTERNS, is_sensitive

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("path", [
    ".env", ".env.local", "prod.env", "deploy/staging.env",
    "cert.pem", "server.key", "a.p12", "a.pfx", "vault.kdbx",
    "putty.ppk", "release.jks", "android.keystore",
    "id_rsa", "id_dsa.old", "id_ed25519.pub", "id_ecdsa",
    ".netrc", ".pgpass", ".npmrc", "repo/.pypirc",
    "credentials", "credentials.json", "mcp_servers.json", ".git-credentials",
    "data/.swarm_internal_secret", ".secret", ".secrets.toml",
    "vault.secret",
    "client_secret.json", "client_secret_1234-abc.apps.googleusercontent.com.json",
])
def test_credential_and_key_names_are_sensitive(path):
    assert is_sensitive(path)
    # The match ignores case and a trailing slash.
    assert is_sensitive(path.upper())
    assert is_sensitive(path + "/")


@pytest.mark.parametrize("path", [
    # Source files about secrets: the code tools must keep reading them.
    "backend/utils/plugin_secrets.py",
    "backend/tests/unit/test_plugin_secrets.py",
    "secrets.py", "secret_manager.js", "docs/secrets.md", "my_secret_notes.txt",
    "client_secret.py", "client_secrets_helper.py", "client_secret_readme.md",
    # Near misses of the other patterns.
    "env", "env.py", "environment.py", ".envrc", "envfile",
    "npmrc", ".npmignore", "package.json", "keystore", "keystore.py", "jks.md",
    "backend/utils/credential_store.py", "README.md", ".gitignore",
])
def test_ordinary_names_are_not_sensitive(path):
    assert not is_sensitive(path)


def test_patterns_are_lower_case():
    # is_sensitive lower-cases the name, so an upper-case pattern would never match.
    assert all(pattern == pattern.lower() for pattern in SENSITIVE_PATTERNS)


def test_tracked_source_named_for_secrets_is_not_refused():
    """A pattern that matched a tracked source file would hide it from
    read_code, search_code and list_code_files."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not available")
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    source = (".py", ".js", ".jsx", ".ts", ".tsx", ".md")
    named = [p for p in proc.stdout.split("\0") if "secret" in Path(p).name.lower() and p.endswith(source)]
    assert "backend/utils/plugin_secrets.py" in named
    assert [p for p in named if is_sensitive(p)] == []


def test_read_code_still_reads_the_plugin_secrets_sources():
    from backend.tools import llama_code_tools as lct

    for rel in ("backend/utils/plugin_secrets.py", "backend/tests/unit/test_plugin_secrets.py"):
        assert lct.read_code(rel).startswith("✓ Successfully read")
        assert not lct._refused_source_name(rel)
    for name in (".npmrc", "prod.env", "client_secret_demo.json", "data/.swarm_internal_secret"):
        assert lct._refused_source_name(name)
