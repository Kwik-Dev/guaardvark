"""Test fixtures for social outreach suite."""
import contextlib

import pytest
from flask import Flask
from backend.models import db


@pytest.fixture(autouse=True)
def _agent_browser_left_alone(monkeypatch):
    """A watched post restarts the agent Firefox with its control port around
    the post (backend/utils/agent_browser_control.py). Here that is a no-op,
    so no test closes or launches a real browser; test_browser_control.py
    exercises the real one against stubs."""
    @contextlib.contextmanager
    def untouched():
        yield None

    monkeypatch.setattr("backend.utils.agent_browser_control.agent_browser_control", untouched)


class FakeRedis:
    """The few Redis calls outreach makes, kept in a dict."""

    def __init__(self):
        self.values = {}
        self.zsets = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def delete(self, *keys):
        return sum(1 for key in keys if self.values.pop(key, None) is not None)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key, low, high):
        zset = self.zsets.get(key, {})
        for member in [m for m, score in zset.items() if low <= score <= high]:
            del zset[member]

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def expire(self, key, seconds):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    """Outreach's Redis (cadence, the watched-post slot) as a FakeRedis."""
    r = FakeRedis()
    monkeypatch.setattr("backend.services.social_outreach.kill_switch._get_redis", lambda: r)
    return r


@pytest.fixture
def watching(monkeypatch):
    """Whether the Agent Screen viewer counts as open; True until a test
    sets state["watching"] = False."""
    from backend.utils import agent_screen_presence

    state = {"watching": True}

    def answer(window_s=agent_screen_presence.WATCH_WINDOW_S):
        if state["watching"]:
            return True, None
        return False, agent_screen_presence.NOT_WATCHING

    monkeypatch.setattr(agent_screen_presence, "watching", answer)
    return state


@pytest.fixture
def outreach_profile(tmp_path, monkeypatch):
    """A filled "what you promote" profile in a temp file, so the drafter has
    something to promote and the scouts have keywords to match."""
    from backend.services.social_outreach import profile

    monkeypatch.setenv(profile.PROFILE_PATH_ENV, str(tmp_path / "profile.json"))
    return profile.save({
        "name": "Example App",
        "pitch": "A local-first AI workstation. Your machine, your data.",
        "site_url": "https://example.com",
        "repo_url": "https://github.com/example/app",
        "account_handle": "example",
        "videos": [{"label": "Demo", "url": "https://www.youtube.com/watch?v=abcdefghijk"}],
        "features": [
            {"key": "rag", "blurb": "search your own documents offline", "keywords": ["rag", "retrieval"]},
            {"key": "video_gen", "blurb": "video generation on one desktop GPU", "keywords": ["video gen", "text2video"]},
            {"key": "local_ai", "blurb": "everything runs locally, no cloud", "keywords": ["ollama", "local llm", "local ai"]},
        ],
    })


@pytest.fixture
def empty_outreach_profile(tmp_path, monkeypatch):
    """No profile file at all: a fresh install."""
    from backend.services.social_outreach import profile

    monkeypatch.setenv(profile.PROFILE_PATH_ENV, str(tmp_path / "profile.json"))


@pytest.fixture
def app():
    """Flask app with in-memory database."""
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Database session that rolls back after test."""
    with app.app_context():
        yield db.session


@pytest.fixture
def web_access_on(app):
    """Posting reaches an outside site, so it needs web access (Settings) on."""
    from backend.models import Setting
    with app.app_context():
        db.session.add(Setting(key="allow_web_search", value="true"))
        db.session.commit()
    yield
