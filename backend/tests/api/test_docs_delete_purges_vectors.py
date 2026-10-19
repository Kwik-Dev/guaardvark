"""DELETE /api/docs/<id> removes the document's vectors whatever it is: a code
file (chunked into the vector table like prose) and a document whose last
re-index failed (ERROR keeps the previous vectors) included."""
import pytest
from flask import Flask

from backend.models import Document, db


@pytest.fixture
def app(tmp_path, monkeypatch):
    import backend.services.indexing_service as isvc

    purged = []
    monkeypatch.setattr(isvc, "purge_document_vectors",
                        lambda document_id, project_id=None, profile=None:
                        purged.append(document_id) or isvc.PurgeResult(3))
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                       "UPLOAD_FOLDER": str(tmp_path)})
    db.init_app(app)
    from backend.api.docs_api import docs_bp
    app.register_blueprint(docs_bp)
    with app.app_context():
        db.create_all()
        yield app, purged
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize("filename,is_code,status", [
    ("main.py", True, "INDEXED"),
    ("notes.md", False, "INDEXED"),
    ("report.pdf", False, "ERROR"),
])
def test_delete_purges_the_vectors(app, filename, is_code, status):
    app, purged = app
    with app.app_context():
        doc = Document(filename=filename, path=filename, is_code_file=is_code, index_status=status)
        db.session.add(doc)
        db.session.commit()
        doc_id = doc.id
    resp = app.test_client().delete(f"/api/docs/{doc_id}")
    assert resp.status_code == 200, resp.get_json()
    assert purged == [doc_id]
