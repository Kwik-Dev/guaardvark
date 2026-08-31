"""Generic timeline JSON → Shotcut-compatible MLT XML.

Accepts the same shape the existing VideoEditorPage already produces and the
ffmpeg backend already consumes — see `backend/api/video_overlay_api.py`
docstring on /render-timeline. We translate it to MLT and emit a `.mlt`.

Currently supports:
  - One video clip with optional in/out trim (video_trim_start, video_trim_end)
  - N text overlay filters (dynamictext) with per-text timing, font, color, position
  - One audio replacement track with volume control

Multi-clip support is M4-ish — the JSON shape doesn't carry it yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import filters as filter_catalog
from . import transitions as transition_catalog
from .frame_math import FrameRate, frames_to_smpte, seconds_to_absolute_frame
from .mlt_parser import ProjectProfile


def _probe_media_duration(path: str | Path) -> Optional[float]:
    """Return a media file's duration in seconds via ffprobe, or None on failure."""
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        val = out.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def _loop_audio(path: str | Path, target_dur: float, out_dir: str | Path) -> str:
    """Loop a short audio file with ffmpeg to cover `target_dur` seconds.

    Returns the path to the looped file (created in `out_dir`). The MLT `repeat`
    filter and the avformat `loop` property don't loop audio reliably in this
    melt build, so we pre-loop with ffmpeg instead.
    """
    import subprocess

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{Path(path).stem}_loop_{int(round(target_dur))}s.wav"
    if not out.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(path),
             "-t", f"{target_dur:.3f}", "-c:a", "pcm_s16le", str(out)],
            check=True, capture_output=True, timeout=120,
        )
    return str(out)


@dataclass
class TextElement:
    """One text overlay on the video — UI coordinates are pixel-space."""

    text: str
    font_size: int = 48
    font_color: str = "#ffffff"
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    start_seconds: float = 0.0
    end_seconds: float = 0.0


@dataclass
class Timeline:
    video_path: str
    audio_path: Optional[str] = None
    video_trim_start: float = 0.0
    video_trim_end: Optional[float] = None  # None = full duration
    audio_volume: float = 1.0
    text_elements: list[TextElement] = field(default_factory=list)


def timeline_from_payload(payload: dict[str, Any]) -> Timeline:
    """Translate the Flask request body into a Timeline."""
    text_els = []
    for t in payload.get("text_elements") or []:
        text_els.append(
            TextElement(
                text=str(t.get("text", "")),
                font_size=int(t.get("fontSize", t.get("font_size", 48))),
                font_color=str(t.get("fontColor", t.get("font_color", "#ffffff"))),
                x=float(t.get("x", 0.0)),
                y=float(t.get("y", 0.0)),
                rotation=float(t.get("rotation", 0.0)),
                start_seconds=float(t.get("startSeconds", t.get("start_seconds", 0.0))),
                end_seconds=float(t.get("endSeconds", t.get("end_seconds", 0.0))),
            )
        )
    return Timeline(
        video_path=str(payload["video_path"]),
        audio_path=payload.get("audio_path") or None,
        video_trim_start=float(payload.get("video_trim_start") or 0.0),
        video_trim_end=(float(payload["video_trim_end"]) if payload.get("video_trim_end") not in (None, "") else None),
        audio_volume=float(payload.get("audio_volume", 1.0)),
        text_elements=text_els,
    )


def compose_timeline(
    timeline: Timeline,
    output_path: str | Path,
    profile: ProjectProfile,
    *,
    video_source_duration_seconds: Optional[float] = None,
) -> Path:
    """Emit a Shotcut .mlt for `timeline`. Returns the written path."""
    from lxml import etree

    fps = profile.frame_rate
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the video out-point. If the caller didn't trim and we don't know
    # the source duration, fall back to a permissive 10-minute ceiling — Shotcut
    # will clamp at the actual end-of-file at playback time.
    if timeline.video_trim_end is not None:
        clip_duration_s = max(0.0, timeline.video_trim_end - timeline.video_trim_start)
    elif video_source_duration_seconds is not None:
        clip_duration_s = max(0.0, video_source_duration_seconds - timeline.video_trim_start)
    else:
        clip_duration_s = 600.0  # 10 minutes
    clip_duration_frames = seconds_to_absolute_frame(clip_duration_s, fps)

    video_in_frame = seconds_to_absolute_frame(timeline.video_trim_start, fps)
    video_out_frame = video_in_frame + clip_duration_frames

    mlt = etree.Element(
        "mlt",
        attrib={
            "LC_NUMERIC": "C",
            "version": "7.24.0",
            "title": "Shotcut version 24.04",
            "producer": "main_bin",
            "root": str(out_path.parent),
        },
    )
    _append_profile(mlt, profile)
    _append_main_bin(mlt)

    # Video chain — one chain for the single source clip.
    video_chain_id = "chain_video"
    _append_chain(
        mlt,
        video_chain_id,
        timeline.video_path,
        length_frames=video_out_frame,
        fps=fps,
        audio=False,
    )
    _append_text_filters(mlt, video_chain_id, timeline.text_elements, fps, profile)

    # Audio replacement chain (optional).
    audio_chain_id = None
    if timeline.audio_path:
        audio_chain_id = "chain_audio"
        _append_chain(
            mlt,
            audio_chain_id,
            timeline.audio_path,
            length_frames=clip_duration_frames,
            fps=fps,
            audio=True,
            volume=timeline.audio_volume,
        )

    _append_video_playlist(mlt, video_chain_id, video_in_frame, video_out_frame, fps)
    if audio_chain_id:
        _append_audio_playlist(mlt, audio_chain_id, clip_duration_frames, fps)
    _append_tractor(mlt, clip_duration_frames, fps, has_audio_track=bool(audio_chain_id))

    tree = etree.ElementTree(mlt)
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=False,
    )
    return out_path


# ---------- XML helpers (shared shape with mlt_writer, kept local for clarity) ---


def _append_profile(mlt, profile: ProjectProfile) -> None:
    from lxml import etree

    fps = profile.frame_rate
    etree.SubElement(
        mlt,
        "profile",
        attrib={
            "description": f"automatic for {profile.width}x{profile.height}",
            "width": str(profile.width),
            "height": str(profile.height),
            "progressive": "1",
            "sample_aspect_num": str(profile.sample_aspect_num),
            "sample_aspect_den": str(profile.sample_aspect_den),
            "display_aspect_num": str(profile.width),
            "display_aspect_den": str(profile.height),
            "frame_rate_num": str(fps.num),
            "frame_rate_den": str(fps.den),
            "colorspace": "709",
        },
    )


def _append_main_bin(mlt) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "main_bin", "title": "Shotcut version 24.04"})
    _prop(pl, "shotcut:projectAudioChannels", "2")
    _prop(pl, "shotcut:projectFolder", "0")
    _prop(pl, "xml_retain", "1")


def _append_chain(
    mlt,
    chain_id: str,
    resource: str,
    length_frames: int,
    fps: FrameRate,
    *,
    audio: bool,
    volume: Optional[float] = None,
) -> None:
    from lxml import etree

    smpte = frames_to_smpte(max(length_frames, 1), fps)
    chain = etree.SubElement(mlt, "chain", attrib={"id": chain_id, "out": smpte})
    _prop(chain, "length", smpte)
    _prop(chain, "resource", str(Path(resource).resolve()))
    _prop(chain, "mlt_service", "avformat-novalidate")
    if audio:
        _prop(chain, "audio_index", "0")
        _prop(chain, "video_index", "-1")
        if volume is not None and volume != 1.0:
            f = etree.SubElement(chain, "filter", attrib={"id": f"{chain_id}_vol"})
            _prop(f, "mlt_service", "volume")
            _prop(f, "gain", f"{volume:.3f}")


def _append_text_filters(
    mlt_root,
    video_chain_id: str,
    elements: list[TextElement],
    fps: FrameRate,
    profile: ProjectProfile,
) -> None:
    """Attach <filter mlt_service='dynamictext'> children to the video chain.

    MLT's dynamictext filter uses `geometry` in the form "X/Y:WxH" (pixels)
    or with trailing '%' for percentages. Shotcut writes pixel-space.
    """
    from lxml import etree

    if not elements:
        return
    chain = next((c for c in mlt_root.iter("chain") if c.get("id") == video_chain_id), None)
    if chain is None:
        return

    for i, el in enumerate(elements):
        in_frame = seconds_to_absolute_frame(max(0.0, el.start_seconds), fps)
        out_frame = seconds_to_absolute_frame(max(el.start_seconds, el.end_seconds), fps)
        if out_frame <= in_frame:
            continue

        filt = etree.SubElement(
            chain,
            "filter",
            attrib={
                "id": f"{video_chain_id}_text{i}",
                "in": frames_to_smpte(in_frame, fps),
                "out": frames_to_smpte(max(out_frame - 1, in_frame), fps),
            },
        )
        _prop(filt, "mlt_service", "dynamictext")
        _prop(filt, "argument", el.text)
        _prop(filt, "family", "Sans")
        _prop(filt, "size", str(el.font_size))
        _prop(filt, "fgcolour", _normalize_color(el.font_color))
        _prop(filt, "bgcolour", "0x00000000")
        _prop(filt, "olcolour", "0x000000ff")
        _prop(filt, "outline", "1")
        _prop(filt, "weight", "500")
        # geometry: top-left origin, in pixels. Width 0 lets MLT auto-size.
        _prop(filt, "geometry", f"{int(el.x)}/{int(el.y)}:0x0")
        _prop(filt, "halign", "left")
        _prop(filt, "valign", "top")
        if el.rotation:
            _prop(filt, "rotation", f"{el.rotation:.3f}")


def _normalize_color(c: str) -> str:
    """Convert '#RRGGBB' / '#RRGGBBAA' / 'name' → MLT's '0xRRGGBBAA' form."""
    if not c:
        return "0xffffffff"
    s = c.strip().lower()
    if s.startswith("#"):
        hexpart = s[1:]
        if len(hexpart) == 6:
            return f"0x{hexpart}ff"
        if len(hexpart) == 8:
            return f"0x{hexpart}"
    if s.startswith("0x"):
        return s
    named = {
        "white": "0xffffffff",
        "black": "0x000000ff",
        "red": "0xff0000ff",
        "green": "0x00ff00ff",
        "blue": "0x0000ffff",
        "yellow": "0xffff00ff",
    }
    return named.get(s, "0xffffffff")


def _append_video_playlist(
    mlt,
    video_chain_id: str,
    in_frame: int,
    out_frame: int,
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "playlist0"})
    _prop(pl, "shotcut:video", "1")
    _prop(pl, "shotcut:name", "V1")
    etree.SubElement(
        pl,
        "entry",
        attrib={
            "producer": video_chain_id,
            "in": frames_to_smpte(in_frame, fps),
            "out": frames_to_smpte(max(out_frame - 1, in_frame), fps),
        },
    )


def _append_audio_playlist(
    mlt,
    audio_chain_id: str,
    duration_frames: int,
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": "playlist1"})
    _prop(pl, "shotcut:audio", "1")
    _prop(pl, "shotcut:name", "A1")
    etree.SubElement(
        pl,
        "entry",
        attrib={
            "producer": audio_chain_id,
            "in": "00:00:00.000",
            "out": frames_to_smpte(max(duration_frames - 1, 0), fps),
        },
    )


def _append_tractor(
    mlt,
    duration_frames: int,
    fps: FrameRate,
    *,
    has_audio_track: bool,
) -> None:
    from lxml import etree

    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    tractor = etree.SubElement(
        mlt,
        "tractor",
        attrib={
            "id": "tractor0",
            "title": "Shotcut version 24.04",
            "global_feed": "1",
            "in": "00:00:00.000",
            "out": out_smpte,
        },
    )
    _prop(tractor, "shotcut", "1")
    _prop(tractor, "shotcut:projectAudioChannels", "2")
    _prop(tractor, "shotcut:projectFolder", "0")
    _prop(tractor, "shotcut:scaleFactor", "1")
    _prop(tractor, "shotcut:trackHeight", "50")

    multi = etree.SubElement(tractor, "multitrack")
    etree.SubElement(multi, "track", attrib={"producer": "playlist0"})
    if has_audio_track:
        etree.SubElement(multi, "track", attrib={"producer": "playlist1", "hide": "video"})


def _prop(parent, name: str, value: str) -> None:
    from lxml import etree

    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value


# Suppress unused-import warning — uuid is used by callers in service/app.py
_ = uuid


# ===========================================================================
# Multi-clip arrangement composer (A2).
# ===========================================================================


@dataclass
class _PlacedClip:
    """One clip after track assignment and per-track timing has been resolved."""

    clip_id: str
    source_path: str
    track: int                # 0 = V1, 1 = V2
    track_start_frames: int   # absolute frame on the track timeline
    track_end_frames: int     # exclusive
    source_in_frames: int     # in the source clip
    source_out_frames: int
    filter_preset: str
    transition_to_next: str


def compose_arrangement(
    arrangement_clips: list[dict[str, Any]],
    audio_path: Optional[str],
    output_path: str | Path,
    profile: ProjectProfile,
    *,
    audio_volume: float = 1.0,
    song_duration_seconds: Optional[float] = None,
) -> Path:
    """Emit a multi-clip Shotcut .mlt from an Arrangement.clips list.

    Each entry of `arrangement_clips` is a dict (the JSON shape produced by
    `Arrangement.to_dict()`). Hard-cut clips share a single video track;
    non-hard-cut transitions push the next clip onto the other video track
    so they can overlap and the <transition> can bridge them.
    """
    from lxml import etree

    fps = profile.frame_rate
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not arrangement_clips:
        raise ValueError("compose_arrangement: empty arrangement")

    placed = _assign_tracks_and_timing(arrangement_clips, fps)
    needs_v2 = any(p.track == 1 for p in placed)

    timeline_end_frames = max(p.track_end_frames for p in placed)
    audio_end_frames = (
        seconds_to_absolute_frame(song_duration_seconds, fps)
        if song_duration_seconds is not None
        else timeline_end_frames
    )

    mlt = etree.Element(
        "mlt",
        attrib={
            "LC_NUMERIC": "C",
            "version": "7.24.0",
            "title": "Shotcut version 24.04",
            "producer": "main_bin",
            "root": str(out_path.parent),
        },
    )
    _append_profile(mlt, profile)
    _append_main_bin(mlt)

    # One chain per source clip (NOT per arrangement entry — a clip can appear
    # twice if the arranger reused it). Re-using a chain id collapses the
    # duplicate references in Shotcut's Source bin to a single entry.
    chain_id_by_source: dict[str, str] = {}
    for p in placed:
        if p.source_path in chain_id_by_source:
            continue
        cid = f"chain_{len(chain_id_by_source)}"
        chain_id_by_source[p.source_path] = cid

    # Emit chains: we don't know the source duration; pass the placed
    # clip's source_out as a length hint (good enough for melt timing).
    for src_path, cid in chain_id_by_source.items():
        max_source_out = max(
            p.source_out_frames for p in placed if p.source_path == src_path
        )
        _append_chain(mlt, cid, src_path, length_frames=max_source_out, fps=fps, audio=False)

    # Per-arrangement-entry filter chains are attached to a FILTER ANCHOR
    # `<chain>` clone — but Shotcut treats filters as belonging to a clip
    # instance. The simplest correct model: emit a sub-element on the chain
    # for the filter, with `in`/`out` matching the entry's slice within the
    # source. For v1 we apply filters to the chain (= every reference to it
    # gets the filter). When a clip's source appears multiple times with
    # different filter recommendations, the LAST one wins. Caveat acceptable
    # for A2; A3+ can split into per-entry filter sets via filter-track tracks.
    for p in placed:
        if p.filter_preset and p.filter_preset != "none":
            chain = next(
                c for c in mlt.iter("chain")
                if c.get("id") == chain_id_by_source[p.source_path]
            )
            filter_catalog.apply_filter(
                chain,
                p.filter_preset,
                duration_frames=p.source_out_frames - p.source_in_frames,
                fps=fps,
            )

    # Audio chain (the song).
    audio_chain_id: Optional[str] = None
    if audio_path:
        audio_chain_id = "chain_audio"
        # Loop the soundtrack if it's shorter than the arrangement (e.g. when
        # Respect-bin-order uses every clip and the total exceeds the song).
        src_dur = _probe_media_duration(audio_path)
        target_dur = (
            song_duration_seconds
            if song_duration_seconds is not None
            else timeline_end_frames / fps.fps
        )
        audio_resource = audio_path
        if src_dur is not None and target_dur is not None and src_dur < target_dur - 0.05:
            audio_resource = _loop_audio(audio_path, target_dur, out_path.parent)
        _append_chain(
            mlt, audio_chain_id, audio_resource,
            length_frames=audio_end_frames,
            fps=fps, audio=True, volume=audio_volume,
        )

    # Video playlists: V1 always, V2 only if needed.
    v1_clips = [p for p in placed if p.track == 0]
    v2_clips = [p for p in placed if p.track == 1] if needs_v2 else []
    _append_track_playlist(mlt, "playlist0", "V1", "video", v1_clips, chain_id_by_source, fps)
    if needs_v2:
        _append_track_playlist(mlt, "playlist1_v2", "V2", "video", v2_clips, chain_id_by_source, fps)

    # Audio playlist always after the video playlists.
    audio_pl_id = "playlist_audio"
    if audio_chain_id:
        _append_audio_only_playlist(mlt, audio_pl_id, audio_chain_id, audio_end_frames, fps)

    # Caption track: one timed `dynamictext` producer per clip that carries a
    # caption. Composited over the video via a `composite` transition in the
    # tractor (a plain multitrack does NOT composite a text track over an
    # avformat video; the composite transition does).
    caption_pl_id: Optional[str] = None
    captions = [
        {
            "text": c.get("caption"),
            "start": float(c["timeline_start"]),
            "end": float(c["timeline_end"]),
            "x": c.get("caption_x"),
            "y": c.get("caption_y"),
            "size": c.get("caption_size"),
            "color": c.get("caption_color"),
            "bgcolor": c.get("caption_bgcolor"),
            "halign": c.get("caption_halign"),
            "valign": c.get("caption_valign"),
        }
        for c in arrangement_clips if c.get("caption")
    ]
    if captions:
        caption_pl_id = "playlist_captions"
        _append_caption_track(mlt, caption_pl_id, captions, profile, fps, timeline_end_frames)

    # Tractor: track order is critical for transitions. multitrack indices map:
    #   0 = playlist0 (V1)
    #   1 = playlist1_v2 (V2) if present
    #   2 (or 1) = audio
    _append_arrangement_tractor(
        mlt,
        timeline_end_frames,
        fps,
        has_v2=needs_v2,
        audio_pl_id=audio_pl_id if audio_chain_id else None,
        caption_pl_id=caption_pl_id,
        placed=placed,
    )

    tree = etree.ElementTree(mlt)
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=False,
    )
    return out_path


def compose_single_clip_caption(
    source_path: str,
    source_in: float,
    source_out: float,
    duration_s: float,
    caption: Optional[dict[str, Any]],
    output_path: str | Path,
    profile: ProjectProfile,
) -> Path:
    """Emit a single-clip .mlt with one timed caption burned via a `text` filter.

    MLT's `composite` transition silently drops a text track over an avformat
    video (it only composites over a color producer), and the `dynamictext`
    filter renders a timecode instead of its text. The `text` filter renders
    its `argument` property reliably over video, so we attach it directly to the
    video chain — no separate track or composite needed.

    `caption` is a dict: {text, start, end, x, y, size, color, halign, valign}.
    `start`/`end` are relative to this clip (0..duration_s).
    """
    from lxml import etree

    fps = profile.frame_rate
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration_frames = seconds_to_absolute_frame(max(duration_s, 0.0), fps)
    source_in_frames = seconds_to_absolute_frame(max(source_in, 0.0), fps)
    source_out_frames = seconds_to_absolute_frame(max(source_out, source_in), fps)

    mlt = etree.Element(
        "mlt",
        attrib={
            "LC_NUMERIC": "C",
            "version": "7.24.0",
            "title": "Shotcut version 24.04",
            "producer": "main_bin",
            "root": str(out_path.parent),
        },
    )
    _append_profile(mlt, profile)
    _append_main_bin(mlt)

    chain_id = "chain_0"
    _append_chain(
        mlt, chain_id, source_path,
        length_frames=source_out_frames, fps=fps, audio=False,
    )

    # Attach the caption as a `text` filter on the video chain (reliable over
    # video, unlike the composite transition).
    if caption and str(caption.get("text") or "").strip():
        _append_caption_filter(
            mlt, chain_id, caption, profile, fps, duration_frames,
        )

    _append_video_playlist(mlt, chain_id, source_in_frames, source_out_frames, fps)

    _append_arrangement_tractor(
        mlt,
        duration_frames,
        fps,
        has_v2=False,
        audio_pl_id=None,
        caption_pl_id=None,
        placed=[],
    )

    tree = etree.ElementTree(mlt)
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=False,
    )
    return out_path


def _append_caption_filter(
    mlt,
    chain_id: str,
    caption: dict[str, Any],
    profile: ProjectProfile,
    fps: FrameRate,
    duration_frames: int,
) -> None:
    """Attach a `text` filter (renders its `argument`) to a video chain.

    The `text` filter reliably renders text over an avformat video, unlike the
    `dynamictext` filter (which renders a timecode) or the `composite`
    transition (which drops the text track over video).
    """
    from lxml import etree

    chain = next((c for c in mlt.iter("chain") if c.get("id") == chain_id), None)
    if chain is None:
        return

    text = str(caption.get("text") or "").strip()
    if not text:
        return

    start_f = seconds_to_absolute_frame(max(0.0, float(caption.get("start") or 0.0)), fps)
    end_f = seconds_to_absolute_frame(max(start_f, float(caption.get("end") or duration_frames)), fps)
    end_f = min(end_f, max(duration_frames, 1))

    filt = etree.SubElement(
        chain,
        "filter",
        attrib={
            "id": f"{chain_id}_caption",
            "in": frames_to_smpte(start_f, fps),
            "out": frames_to_smpte(max(end_f - 1, start_f), fps),
        },
    )
    _prop(filt, "mlt_service", "text")
    _prop(filt, "argument", text)
    _prop(filt, "family", "Sans")
    _prop(filt, "size", str(caption.get("size") or 48))
    _prop(filt, "fgcolour", caption.get("color") or "#ffffff")
    # Background colour behind the text (default transparent).
    _prop(filt, "bgcolour", caption.get("bgcolor") or "#00000000")
    # Dark outline keeps white text readable on any (light) background.
    _prop(filt, "outline", "2")
    _prop(filt, "outline_colour", "#000000")
    _prop(filt, "halign", caption.get("halign") or "center")
    _prop(filt, "valign", caption.get("valign") or "bottom")
    _prop(filt, "geometry", f"0,0,{profile.width},{profile.height}")
    if caption.get("x") is not None or caption.get("y") is not None:
        _prop(filt, "x", str(caption.get("x") or 0.5))
        _prop(filt, "y", str(caption.get("y") or 0.5))


def _assign_tracks_and_timing(
    arrangement_clips: list[dict[str, Any]],
    fps: FrameRate,
) -> list[_PlacedClip]:
    """Walk the arrangement, alternating to V2 whenever a non-hard-cut transition appears."""
    placed: list[_PlacedClip] = []
    current_track = 0  # V1

    for i, c in enumerate(arrangement_clips):
        prev_transition = arrangement_clips[i - 1].get("transition_to_next", "hard-cut") if i > 0 else "hard-cut"
        next_transition = c.get("transition_to_next", "hard-cut") if i < len(arrangement_clips) - 1 else "hard-cut"

        # Track-switch on entry: previous transition was non-hard-cut → swap.
        if i > 0 and prev_transition != "hard-cut":
            current_track = 1 - current_track

        # Timing extensions.
        in_ext = transition_catalog.get(prev_transition).overlap_frames(fps) if prev_transition != "hard-cut" else 0
        out_ext = transition_catalog.get(next_transition).overlap_frames(fps) if next_transition != "hard-cut" else 0

        timeline_start_f = seconds_to_absolute_frame(c["timeline_start"], fps)
        timeline_end_f = seconds_to_absolute_frame(c["timeline_end"], fps)
        source_in_f = seconds_to_absolute_frame(c["source_in"], fps)
        source_out_f = seconds_to_absolute_frame(c["source_out"], fps)

        placed.append(
            _PlacedClip(
                clip_id=c.get("clip_id", f"entry{i}"),
                source_path=c["source_path"],
                track=current_track,
                track_start_frames=max(0, timeline_start_f - in_ext),
                track_end_frames=timeline_end_f + out_ext,
                source_in_frames=max(0, source_in_f - in_ext),
                source_out_frames=source_out_f + out_ext,
                filter_preset=c.get("filter_preset", "none"),
                transition_to_next=next_transition,
            )
        )

    return placed


def _append_track_playlist(
    mlt,
    playlist_id: str,
    track_name: str,
    kind: str,
    placed_on_track: list[_PlacedClip],
    chain_id_by_source: dict[str, str],
    fps: FrameRate,
) -> None:
    """Emit one playlist with <blank> gaps and <entry> for each placed clip."""
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": playlist_id})
    _prop(pl, f"shotcut:{kind}", "1")
    _prop(pl, "shotcut:name", track_name)

    if not placed_on_track:
        return

    placed_on_track = sorted(placed_on_track, key=lambda p: p.track_start_frames)
    cursor = 0
    for p in placed_on_track:
        if p.track_start_frames > cursor:
            blank_frames = p.track_start_frames - cursor
            etree.SubElement(pl, "blank", attrib={"length": frames_to_smpte(blank_frames, fps)})
        entry_in = frames_to_smpte(p.source_in_frames, fps)
        entry_out = frames_to_smpte(max(p.source_in_frames, p.source_out_frames - 1), fps)
        etree.SubElement(
            pl, "entry",
            attrib={
                "producer": chain_id_by_source[p.source_path],
                "in": entry_in,
                "out": entry_out,
            },
        )
        cursor = p.track_end_frames


def _append_audio_only_playlist(
    mlt,
    playlist_id: str,
    audio_chain_id: str,
    duration_frames: int,
    fps: FrameRate,
) -> None:
    from lxml import etree

    pl = etree.SubElement(mlt, "playlist", attrib={"id": playlist_id})
    _prop(pl, "shotcut:audio", "1")
    _prop(pl, "shotcut:name", "A1")
    etree.SubElement(
        pl, "entry",
        attrib={
            "producer": audio_chain_id,
            "in": "00:00:00.000",
            "out": frames_to_smpte(max(0, duration_frames - 1), fps),
        },
    )


def _append_caption_track(
    mlt,
    playlist_id: str,
    captions: list[dict[str, Any]],
    profile: ProjectProfile,
    fps: FrameRate,
    total_frames: int,
) -> None:
    """Add a text track with one timed `dynamictext` producer per caption.

    Each caption dict: {text, start, end, x, y, size, color, halign, valign}.
    Position/style default to bottom-center / size 48 / white when unset.
    Blank entries fill the gaps so each caption sits at its absolute window.
    Composited over the video by a `composite` transition in the tractor.

    The producer's `out` is set to the FULL video duration (total_frames-1):
    the MLT composite transition only composites a text track when its producer
    spans the same length as the video track.
    """
    from lxml import etree

    # IMPORTANT: emit the caption PRODUCERS first, then the playlist that
    # references them. MLT's composite transition silently fails to composite
    # a text track over the video when the playlist element is declared before
    # its producers in the XML (the producers must already exist when the
    # playlist entries are resolved).
    slots: list[tuple[str, int, int]] = []  # (producer_id, start_f, dur)
    for i, cap in enumerate(captions):
        text = cap.get("text")
        if not text or not str(text).strip():
            continue
        start_f = seconds_to_absolute_frame(cap["start"], fps)
        end_f = seconds_to_absolute_frame(cap["end"], fps)
        dur = max(1, end_f - start_f)
        pid = f"caption_{i}"
        prod = etree.SubElement(mlt, "producer", attrib={"id": pid, "in": "0", "out": str(max(0, total_frames - 1))})
        _prop(prod, "mlt_service", "dynamictext")
        _prop(prod, "text", str(text))
        _prop(prod, "family", "Sans")
        _prop(prod, "size", str(cap.get("size") or 48))
        _prop(prod, "fgcolour", cap.get("color") or "#ffffff")
        _prop(prod, "bgcolour", cap.get("bgcolor") or "#00000000")
        # Dark outline keeps white text readable on any (light) background.
        _prop(prod, "outline", "2")
        _prop(prod, "outline_colour", "#000000")
        _prop(prod, "halign", cap.get("halign") or "center")
        _prop(prod, "valign", cap.get("valign") or "bottom")
        _prop(prod, "geometry", f"0,0,{profile.width},{profile.height}")
        if cap.get("x") is not None or cap.get("y") is not None:
            _prop(prod, "x", str(cap.get("x") or 0.5))
            _prop(prod, "y", str(cap.get("y") or 0.5))
        slots.append((pid, start_f, dur))

    pl = etree.SubElement(mlt, "playlist", attrib={"id": playlist_id})
    _prop(pl, "shotcut:name", "Captions")
    cursor = 0
    for pid, start_f, dur in slots:
        if start_f > cursor:
            etree.SubElement(pl, "blank", attrib={"length": str(start_f - cursor)})
        etree.SubElement(pl, "entry", attrib={"producer": pid, "in": "0", "out": str(dur - 1)})
        cursor = start_f + dur


def _append_arrangement_tractor(
    mlt,
    duration_frames: int,
    fps: FrameRate,
    *,
    has_v2: bool,
    audio_pl_id: Optional[str],
    caption_pl_id: Optional[str],
    placed: list[_PlacedClip],
) -> None:
    from lxml import etree

    out_smpte = frames_to_smpte(max(0, duration_frames - 1), fps)
    tractor = etree.SubElement(
        mlt, "tractor",
        attrib={
            "id": "tractor0",
            "title": "Shotcut version 24.04",
            "global_feed": "1",
            "in": "00:00:00.000",
            "out": out_smpte,
        },
    )
    _prop(tractor, "shotcut", "1")
    _prop(tractor, "shotcut:projectAudioChannels", "2")
    _prop(tractor, "shotcut:projectFolder", "0")
    _prop(tractor, "shotcut:scaleFactor", "1")
    _prop(tractor, "shotcut:trackHeight", "50")

    multi = etree.SubElement(tractor, "multitrack")
    etree.SubElement(multi, "track", attrib={"producer": "playlist0"})  # V1 = track 0
    if has_v2:
        etree.SubElement(multi, "track", attrib={"producer": "playlist1_v2"})  # V2 = track 1
    caption_track_idx: Optional[int] = None
    if caption_pl_id:
        caption_track_idx = 2 if has_v2 else 1
        etree.SubElement(multi, "track", attrib={"producer": caption_pl_id})
    if audio_pl_id:
        etree.SubElement(multi, "track", attrib={"producer": audio_pl_id, "hide": "video"})

    # Composite the caption track over the video (a plain multitrack does NOT
    # composite a text track over an avformat video; the composite transition does).
    if caption_track_idx is not None:
        etree.SubElement(
            tractor, "transition",
            attrib={"id": "caption_composite", "in": "0", "out": str(max(0, duration_frames - 1))},
        )
        _prop(tractor[-1], "mlt_service", "composite")
        _prop(tractor[-1], "a_track", "0")
        _prop(tractor[-1], "b_track", str(caption_track_idx))
        _prop(tractor[-1], "geometry", "0,0,100%,100%")
        _prop(tractor[-1], "fill", "1")


    # Emit a transition for every non-hard-cut between adjacent clips on
    # different tracks.
    if has_v2:
        for i in range(len(placed) - 1):
            a, b = placed[i], placed[i + 1]
            if a.transition_to_next == "hard-cut" or a.track == b.track:
                continue
            # Overlap region in tractor-timeline frames.
            overlap_start = max(b.track_start_frames, 0)
            overlap_end = min(a.track_end_frames, duration_frames)
            if overlap_end <= overlap_start:
                continue
            transition_catalog.emit_transition(
                tractor,
                a.transition_to_next,
                in_frame=overlap_start,
                out_frame=overlap_end,
                a_track=a.track,        # 0 or 1
                b_track=b.track,
                fps=fps,
            )
