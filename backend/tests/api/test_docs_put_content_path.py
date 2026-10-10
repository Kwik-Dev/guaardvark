"""PUT /api/docs/<id> with content writes the file copy only inside uploads or outputs.

A document row can carry an absolute path (production and interconnector
imports). os.path.join(UPLOAD_FOLDER, path) then ignores the uploads folder,
so an unchecked write would land wherever the row points. The route refuses
such a document and leaves both the row and the file alone.
"""

import pytest
from flask import Flask

from backend.models import Document, db


@pytest.fixture
def docs_app(tmp_path):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    elsewhere = tmp_path / "elsewhere"
    for folder in (uploads, outputs, elsewhere):
        folder.mkdir()
    (uploads / "inside.py").write_text("print('old')\n", encoding="utf-8")
    (outputs / "made.py").write_text("print('old')\n", encoding="utf-8")
    (elsewhere / "outside.py").write_text("print('old')\n", encoding="utf-8")

    app = Flask(__name__)
    app.config.update(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(uploads),
            "OUTPUT_DIR": str(outputs),
        }
    )
    db.init_app(app)
    from backend.api.docs_api import docs_bp

    if docs_bp.name not in app.blueprints:
        app.register_blueprint(docs_bp)
    with app.app_context():
        db.create_all()
        inside = Document(filename="inside.py", path="inside.py", content="print('old')\n")
        made = Document(filename="made.py", path=str(outputs / "made.py"), content="print('old')\n")
        outside = Document(filename="outside.py", path=str(elsewhere / "outside.py"), content="print('old')\n")
        traversal = Document(filename="t.py", path="../elsewhere/outside.py", content="print('old')\n")
        db.session.add_all([inside, made, outside, traversal])
        db.session.commit()
        ids = {"inside": inside.id, "made": made.id, "outside": outside.id, "traversal": traversal.id}
        yield app, ids, {"uploads": uploads, "outputs": outputs, "elsewhere": elsewhere}
        db.session.remove()
        db.drop_all()


def _put(app, doc_id):
    return app.test_client().put(f"/api/docs/{doc_id}", json={"content": "print('new')\n"})


def test_relative_path_under_uploads_is_written(docs_app):
    app, ids, dirs = docs_app
    resp = _put(app, ids["inside"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert (dirs["uploads"] / "inside.py").read_text(encoding="utf-8") == "print('new')\n"
    with app.app_context():
        assert db.session.get(Document, ids["inside"]).content == "print('new')\n"


def test_absolute_path_under_outputs_is_written(docs_app):
    app, ids, dirs = docs_app
    resp = _put(app, ids["made"])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert (dirs["outputs"] / "made.py").read_text(encoding="utf-8") == "print('new')\n"


@pytest.mark.parametrize("key", ["outside", "traversal"])
def test_path_outside_storage_is_refused_and_nothing_changes(docs_app, key):
    app, ids, dirs = docs_app
    resp = _put(app, ids[key])
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "PATH_OUTSIDE_STORAGE"
    assert (dirs["elsewhere"] / "outside.py").read_text(encoding="utf-8") == "print('old')\n"
    with app.app_context():
        assert db.session.get(Document, ids[key]).content == "print('old')\n"
