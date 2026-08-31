"""E1 regression: UI field labels parse as structured fields.

The SwarmPage UI / AI-plan-builder emits 'Assign to:', 'Files:', 'Deps:'.
The parser must treat these as aliases for preferred_backend / file_scope /
dependencies — and existing '- files:' templates must still parse identically.
"""

from pathlib import Path

from service.plan_parser import parse_plan


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "plan.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_ui_labels_parse_structured(tmp_path):
    plan = _write(tmp_path, """# UI-built plan

## Task: Create the database models
Assign to: claude
Files: [backend/models.py, backend/schema.py]
Deps: none

Build the SQLAlchemy models.

## Task: Build API routes
Assign to: cline
Files: backend/api/feature_api.py
Deps: Create the database models, Build service layer

Wire up the CRUD endpoints.

## Task: Build service layer
Files: backend/services/feature_service.py

Business logic.
""")
    tasks = parse_plan(plan)
    by_id = {t.id: t for t in tasks}

    models = by_id["create-the-database-models"]
    assert models.preferred_backend == "claude"
    assert models.file_scope == ["backend/models.py", "backend/schema.py"]
    assert models.dependencies == []  # "none" -> empty

    routes = by_id["build-api-routes"]
    assert routes.preferred_backend == "cline"
    assert routes.file_scope == ["backend/api/feature_api.py"]
    # both deps resolve to real task ids (one slug, one fuzzy/title match)
    assert "create-the-database-models" in routes.dependencies
    assert "build-service-layer" in routes.dependencies


def test_deps_alias_variants(tmp_path):
    """'Deps:', 'Depends:', 'depends_on:' all feed dependencies."""
    plan = _write(tmp_path, """# variants

## Task: A
Files: a.py

## Task: B
Files: b.py
Depends: A

## Task: C
Files: c.py
depends_on: A
""")
    tasks = parse_plan(plan)
    by_id = {t.id: t for t in tasks}
    assert by_id["b"].dependencies == ["a"]
    assert by_id["c"].dependencies == ["a"]


def test_existing_template_still_parses(tmp_path):
    """Regression: the shipped rest-api.md template parses unchanged."""
    template = Path(__file__).resolve().parent.parent / "templates" / "rest-api.md"
    tasks = parse_plan(template)
    by_id = {t.id: t for t in tasks}

    assert "create-database-models" in by_id
    models = by_id["create-database-models"]
    assert models.file_scope == ["backend/models.py"]
    assert models.dependencies == []  # "none"

    routes = by_id["build-api-routes"]
    assert routes.file_scope == ["backend/api/new_feature_api.py"]
    assert routes.dependencies == ["create-database-models"]
