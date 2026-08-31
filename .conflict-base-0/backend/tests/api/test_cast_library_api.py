import pytest
import json

try:
    from flask import Flask
    from backend.models import db, Subject
    from backend.api.cast_library_api import bp as cast_library_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        # Cast-ref upload endpoint resolves data/cast_refs/ under STORAGE_DIR (the
        # key the code actually reads — see cast_library_api._cast_ref_dir). Point
        # it at a tmp dir so uploads NEVER touch the repo's real data/cast_refs/.
        # (Was "DATA_DIR", which the code ignores → tests polluted ./data/cast_refs/1.)
        "STORAGE_DIR": str(tmp_path),
    })
    db.init_app(app)
    app.register_blueprint(cast_library_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_list_empty_cast_library(client):
    resp = client.get("/api/cast-library")
    assert resp.status_code == 200
    assert resp.get_json() == {"subjects": []}


def test_create_subject_returns_201(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "description": "the protagonist",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "Alex"
    assert data["kind"] == "character"
    assert data["training_status"] == "untrained"


def test_create_subject_validates_kind(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "alien", "name": "X", "description": "y",
    })
    assert resp.status_code == 400


def test_list_after_create(client):
    client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "A", "description": "x",
    })
    client.post("/api/cast-library/subjects", json={
        "kind": "environment", "name": "B", "description": "y",
    })
    resp = client.get("/api/cast-library")
    data = resp.get_json()
    assert len(data["subjects"]) == 2


def test_delete_subject(client, app):
    create = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "description": "x",
    })
    subj_id = create.get_json()["id"]
    delete = client.delete(f"/api/cast-library/subjects/{subj_id}")
    assert delete.status_code == 204
    listing = client.get("/api/cast-library")
    assert listing.get_json()["subjects"] == []


def test_delete_unknown_subject_404(client):
    resp = client.delete("/api/cast-library/subjects/9999")
    assert resp.status_code == 404


# --- voice_id (Seam C) ----------------------------------------------------

def test_create_subject_accepts_and_serializes_voice_id(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Serenity", "voice_id": "af_bella",
    })
    assert resp.status_code == 201
    assert resp.get_json()["voice_id"] == "af_bella"


def test_voice_id_defaults_to_null(client):
    resp = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "NoVoice",
    })
    assert resp.get_json()["voice_id"] is None


def test_patch_updates_voice_id(client):
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex",
    }).get_json()
    patch = client.patch(f"/api/cast-library/subjects/{subj['id']}", json={
        "voice_id": "am_adam", "trigger_word": "dean_xyz",
    })
    assert patch.status_code == 200
    data = patch.get_json()
    assert data["voice_id"] == "am_adam"
    assert data["trigger_word"] == "dean_xyz"


def test_patch_empty_string_clears_voice_id(client):
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "voice_id": "af_heart",
    }).get_json()
    patch = client.patch(f"/api/cast-library/subjects/{subj['id']}", json={"voice_id": ""})
    assert patch.get_json()["voice_id"] is None


def test_patch_absent_key_leaves_voice_id_untouched(client):
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "voice_id": "af_heart",
    }).get_json()
    patch = client.patch(f"/api/cast-library/subjects/{subj['id']}", json={"description": "updated"})
    data = patch.get_json()
    assert data["voice_id"] == "af_heart"
    assert data["description"] == "updated"


def test_patch_unknown_subject_404(client):
    resp = client.patch("/api/cast-library/subjects/9999", json={"voice_id": "x"})
    assert resp.status_code == 404


def test_patch_bible_clears_vision_grounded_sets_manual_override(client):
    from backend.models import db, Subject
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "bible": "vision look",
    }).get_json()
    s = db.session.get(Subject, subj["id"])
    s.training_settings_json = {
        "bible_vision_grounded": True,
        "bible_identity_marks": "shaved head",
    }
    db.session.commit()

    patch = client.patch(f"/api/cast-library/subjects/{subj['id']}", json={
        "bible": "manual rewrite with different hair",
    })
    assert patch.status_code == 200
    data = patch.get_json()
    assert data["bible_vision_grounded"] is False
    assert data["bible_manual_override"] is True
    assert data["bible"] == "manual rewrite with different hair"


# --- upload-refs ---------------------------------------------------------

def _png_bytes() -> bytes:
    """Smallest possible valid PNG. Used so the upload path doesn't have to
    inspect contents — extension validation is enough."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
        b"\x00\x00\x00\x02\x00\x01\xe5\x27\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _create_subject(client, name="Alex"):
    return client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": name, "description": "x",
    }).get_json()


def test_upload_refs_persists_files_and_appends_paths(client, app):
    from io import BytesIO
    subj = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "headshot.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["saved"]) == 1
    assert data["saved"][0].endswith("headshot.png")
    assert data["subject"]["ref_image_paths"] == data["saved"]
    # File actually exists on disk
    import os
    assert os.path.exists(data["saved"][0])


def test_upload_refs_skips_unsupported_extension(client):
    from io import BytesIO
    subj = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(b"not an image"), "evil.exe")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["saved"] == []
    assert len(data["skipped"]) == 1
    assert "unsupported" in data["skipped"][0]["reason"].lower()


def test_upload_refs_accepts_any_image_name(client):
    """No provenance guard: ANY supported image is accepted as a reference,
    including ones whose names look like generated frames. Curating a coherent
    reference pool is the user's responsibility (operator call 2026-06-25 — these
    characters are often AI-generated, so 'generated vs photo' is a false split)."""
    from io import BytesIO
    subj = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [
            (BytesIO(_png_bytes()), "wan22_i2v_01045_.png"),
            (BytesIO(_png_bytes()), "storyboard-flux-dev_00169_.png"),
            (BytesIO(_png_bytes()), "real_headshot.jpg"),
        ]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["saved"]) == 3
    assert data["skipped"] == []


def test_upload_refs_appends_to_existing_list(client):
    from io import BytesIO
    subj = client.post("/api/cast-library/subjects", json={
        "kind": "character", "name": "Alex", "description": "x",
        "ref_image_paths": ["/already/there.png"],
    }).get_json()
    resp = client.post(
        f"/api/cast-library/subjects/{subj['id']}/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "new.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    refs = resp.get_json()["subject"]["ref_image_paths"]
    assert len(refs) == 2
    assert refs[0] == "/already/there.png"
    assert refs[1].endswith("new.png")


def test_upload_refs_404_for_unknown_subject(client):
    from io import BytesIO
    resp = client.post(
        "/api/cast-library/subjects/9999/upload-refs",
        data={"files": [(BytesIO(_png_bytes()), "x.png")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404


def test_upload_refs_resolves_filename_collisions(client):
    from io import BytesIO
    subj = _create_subject(client)
    paths = []
    for _ in range(3):
        resp = client.post(
            f"/api/cast-library/subjects/{subj['id']}/upload-refs",
            data={"files": [(BytesIO(_png_bytes()), "ref.png")]},
            content_type="multipart/form-data",
        )
        paths.append(resp.get_json()["saved"][0])
    # Same source name three times → three distinct on-disk paths.
    assert len(set(paths)) == 3
