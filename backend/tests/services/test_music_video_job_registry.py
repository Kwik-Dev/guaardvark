import pytest
from flask import Flask

from backend.models import db, MusicVideo
from backend.services.job_registry import adapt_music_video
from backend.services.job_types import JobKind, JobStatus


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_adapt_music_video_generating_progress(app):
    with app.app_context():
        mv = MusicVideo(
            name="Test MV",
            style_prompt="neon",
            current_stage="generating",
            status="generating",
            clips=[
                {"index": 0, "status": "done", "start": 0, "end": 2},
                {"index": 1, "status": "pending", "start": 2, "end": 4},
            ],
        )
        db.session.add(mv)
        db.session.commit()

        job = adapt_music_video(mv)

    assert job.kind == JobKind.MUSIC_VIDEO
    assert job.id == f"music_video:{mv.id}"
    assert job.status == JobStatus.RUNNING
    assert job.progress == 50.0
    assert job.metadata["clips_done"] == 1
    assert job.metadata["clip_count"] == 2
    assert job.cancellable is True