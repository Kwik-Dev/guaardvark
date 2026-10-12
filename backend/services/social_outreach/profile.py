"""
What this install promotes: the local outreach profile.

A fresh install has nothing to promote, so every outreach surface (drafts,
self-share links, UTM tagging, the snippet bank) reads this profile instead
of carrying a product's own links and copy. It lives in
``data/social_outreach/profile.json`` (gitignored, so each clone keeps its own)
and is edited from the Outreach page.

Schema, every field optional and empty by default:
    name            short product or project name
    pitch           one or two sentences on what it is
    site_url        the page a comment points people to
    repo_url        the repository, preferred over the site on YouTube
    account_handle  the account outreach posts under (no leading @ or u/)
    videos          [{"label": "...", "url": "https://..."}]
    features        [{"key": "...", "blurb": "...", "keywords": ["..."]}]

``GUAARDVARK_OUTREACH_PROFILE`` points the file somewhere else; ``load`` and
``save`` also take an explicit path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PROFILE_PATH_ENV = "GUAARDVARK_OUTREACH_PROFILE"
PROFILE_RELPATH = Path("data") / "social_outreach" / "profile.json"

# Caps on what the page can save. Generous for a real profile, small enough
# that a stray paste cannot bloat the prompt every draft reads.
MAX_NAME = 80
MAX_PITCH = 2000
MAX_URL = 500
MAX_HANDLE = 80
MAX_LABEL = 80
MAX_BLURB = 300
MAX_KEYWORD = 60
MAX_KEY = 40
MAX_VIDEOS = 20
MAX_FEATURES = 40
MAX_KEYWORDS = 20

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_KEY_CHARS = re.compile(r"[^a-z0-9_]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class Profile:
    name: str = ""
    pitch: str = ""
    site_url: str = ""
    repo_url: str = ""
    account_handle: str = ""
    videos: List[Dict[str, str]] = field(default_factory=list)
    features: List[Dict[str, Any]] = field(default_factory=list)
    # (key, compiled keyword patterns) per feature, built once per load.
    _patterns: List[tuple] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._patterns = [
            (
                f["key"],
                [
                    re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", re.IGNORECASE)
                    for kw in f.get("keywords") or []
                    if kw
                ],
            )
            for f in self.features
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pitch": self.pitch,
            "site_url": self.site_url,
            "repo_url": self.repo_url,
            "account_handle": self.account_handle,
            "videos": [dict(v) for v in self.videos],
            "features": [
                {"key": f["key"], "blurb": f["blurb"], "keywords": list(f["keywords"])}
                for f in self.features
            ],
        }

    def is_configured(self) -> bool:
        """A profile the drafter can work from: a name and a pitch."""
        return bool(self.name and self.pitch)

    def cta_url(self, platform: Optional[str] = None) -> str:
        """The link a comment points to: the repo on YouTube when there is
        one, the site otherwise; "" when the profile has neither."""
        if (platform or "").strip().lower() == "youtube" and self.repo_url:
            return self.repo_url
        return self.site_url or self.repo_url

    def feature_blurbs(self) -> Dict[str, str]:
        return {f["key"]: f["blurb"] for f in self.features if f.get("blurb")}

    def link_hosts(self) -> List[str]:
        """Hostnames whose links get UTM tags: the site's. The repo host is a
        shared one (github.com and the like), where a tag would land on other
        people's links too, so repo links are cited untagged."""
        host = (urlparse(self.site_url).hostname or "").lower() if self.site_url else ""
        return [host] if host else []

    def own_links(self) -> List[str]:
        """The site and repo URLs without scheme, lower-cased, for checking
        whether a draft already cites one of them."""
        out = []
        for url in (self.site_url, self.repo_url):
            if url:
                out.append(re.sub(r"^https?://", "", url.lower()).rstrip("/"))
        return out

    def find_relevant_feature(self, text: str) -> Optional[str]:
        """Key of the first feature with a keyword in ``text`` (whole words,
        case-insensitive, in the order the features are listed), or None."""
        if not text:
            return None
        for key, patterns in self._patterns:
            if any(p.search(text) for p in patterns):
                return key
        return None

    def pitch_sheet(self) -> str:
        """The facts a drafter may use, built from the profile; "" when there
        is nothing to promote."""
        if not self.is_configured():
            return ""
        lines = [f"{self.name}: {self.pitch}"]
        blurbs = self.feature_blurbs()
        if blurbs:
            lines.append("")
            lines.append("What it does:")
            lines.extend(f"- {blurb}" for blurb in blurbs.values())
        links = [(label, url) for label, url in (("Site", self.site_url), ("Repo", self.repo_url)) if url]
        if links:
            lines.append("")
            lines.append("Links (the only URLs to cite):")
            lines.extend(f"- {label}: {url}" for label, url in links)
        return "\n".join(lines)


# --- validation ----------------------------------------------------------

def _clean_text(value: Any, cap: int, *, keep_newlines: bool = False) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("expected text")
    text = _CONTROL_CHARS.sub("", value)
    if keep_newlines:
        text = re.sub(r"[ \t]+", " ", text).strip()
    else:
        text = _WHITESPACE.sub(" ", text).strip()
    return text[:cap]


def _clean_url(value: Any, what: str) -> str:
    url = _clean_text(value, MAX_URL)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in url:
        raise ValueError(f"{what} must be an http or https URL")
    return url


def _clean_handle(value: Any) -> str:
    handle = _clean_text(value, MAX_HANDLE)
    return handle.lstrip("@").removeprefix("u/").removeprefix("/u/").strip()


def _clean_key(value: Any, fallback: str) -> str:
    key = _KEY_CHARS.sub("_", _clean_text(value, MAX_KEY).lower()).strip("_")
    return key or fallback


def _as_list(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list")
    return value


def validate(data: Any) -> Dict[str, Any]:
    """Normalise a profile payload, raising ValueError with a plain message
    for anything the page should not have sent."""
    if not isinstance(data, dict):
        raise ValueError("profile must be an object")

    videos = []
    for i, item in enumerate(_as_list(data.get("videos"), "videos")[:MAX_VIDEOS]):
        if not isinstance(item, dict):
            raise ValueError("each video must be an object with label and url")
        url = _clean_url(item.get("url"), f"video {i + 1} url")
        label = _clean_text(item.get("label"), MAX_LABEL)
        if not url:
            # An empty row is dropped; a half-filled one is refused, so the
            # editor does not silently lose what the person typed.
            if label:
                raise ValueError(f"video {i + 1} ('{label}') needs a URL")
            continue
        videos.append({"label": label or f"Video {len(videos) + 1}", "url": url})

    features = []
    seen_keys = set()
    for i, item in enumerate(_as_list(data.get("features"), "features")[:MAX_FEATURES]):
        if not isinstance(item, dict):
            raise ValueError("each feature must be an object with key, blurb and keywords")
        blurb = _clean_text(item.get("blurb"), MAX_BLURB)
        keywords_in = item.get("keywords")
        if isinstance(keywords_in, str):
            keywords_in = keywords_in.split(",")
        keywords = []
        for kw in _as_list(keywords_in, f"feature {i + 1} keywords")[:MAX_KEYWORDS]:
            kw = _clean_text(kw, MAX_KEYWORD).lower()
            if kw and kw not in keywords:
                keywords.append(kw)
        if not blurb and not keywords:
            named = _clean_text(item.get("key"), MAX_KEY)
            if named:
                raise ValueError(f"feature {i + 1} ('{named}') needs a description or keywords")
            continue
        key = _clean_key(item.get("key"), f"feature_{len(features) + 1}")
        if key in seen_keys:
            raise ValueError(f"feature key '{key}' is used twice")
        seen_keys.add(key)
        features.append({"key": key, "blurb": blurb, "keywords": keywords})

    return {
        "name": _clean_text(data.get("name"), MAX_NAME),
        "pitch": _clean_text(data.get("pitch"), MAX_PITCH, keep_newlines=True),
        "site_url": _clean_url(data.get("site_url"), "site_url"),
        "repo_url": _clean_url(data.get("repo_url"), "repo_url"),
        "account_handle": _clean_handle(data.get("account_handle")),
        "videos": videos,
        "features": features,
    }


# --- storage -------------------------------------------------------------

_lock = threading.Lock()
# path -> (mtime, Profile); re-read when the file changes so an edit on the
# page reaches the next draft without a restart.
_cache: Dict[str, tuple] = {}


def profile_path(path: Optional[os.PathLike | str] = None) -> Path:
    if path:
        return Path(path)
    override = os.environ.get(PROFILE_PATH_ENV)
    if override:
        return Path(override)
    root = os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3]
    return Path(root) / PROFILE_RELPATH


def load(path: Optional[os.PathLike | str] = None) -> Profile:
    """The current profile; an empty one when the file is missing or unreadable."""
    file = profile_path(path)
    key = str(file)
    try:
        mtime = file.stat().st_mtime
    except FileNotFoundError:
        _cache.pop(key, None)
        return Profile()
    except OSError as e:
        logger.warning("outreach profile stat failed: %s", e)
        cached = _cache.get(key)
        return cached[1] if cached else Profile()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            profile = Profile(**validate(data))
        except (OSError, ValueError, TypeError) as e:
            logger.warning("outreach profile unreadable (%s): %s", file, e)
            return cached[1] if cached else Profile()
        _cache[key] = (mtime, profile)
        return profile


def save(data: Any, path: Optional[os.PathLike | str] = None) -> Profile:
    """Validate and write the profile atomically; returns what was stored."""
    clean = validate(data)
    file = profile_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, file)
        _cache.pop(str(file), None)
    return load(path)


# --- module-level conveniences for callers that only need one answer -----

def is_configured() -> bool:
    return load().is_configured()


def site_url() -> str:
    return load().site_url


def cta_url(platform: Optional[str] = None) -> str:
    return load().cta_url(platform)


def feature_blurbs() -> Dict[str, str]:
    return load().feature_blurbs()


def find_relevant_feature(text: str) -> Optional[str]:
    return load().find_relevant_feature(text)


def link_hosts() -> List[str]:
    return load().link_hosts()


def no_product_reason() -> str:
    """Why a draft was not made: shown in place of one."""
    return "Nothing to promote yet: fill in \"What you promote\" on the Outreach page"


def no_link_reason() -> str:
    return "no link to share: add a site URL under \"What you promote\" on the Outreach page"
