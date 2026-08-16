"""Composited overlays for training videos: spec cards, titles, end plates.

Cards carry every specification a trainee acts on — fastener callouts, spacing,
overlaps, code citations — because generated b-roll cannot be trusted to show
them correctly. Text is composited here at full resolution rather than burned in
by ffmpeg so the type stays crisp and the layout stays under our control.

Follows the composition approach proven on the Guaardvark thumbnail set:
translucent panels over the frame, letterspaced labels, variable-font weights.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import context
from config import ASSET_ROOT, HEIGHT, WIDTH

_FONT_CANDIDATES = [
    ASSET_ROOT / "fonts/Inter-Variable.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
]


def _font_file() -> Path:
    for path in _FONT_CANDIDATES:
        if path.exists():
            return path
    raise RuntimeError(
        "no usable font found; install fonts-dejavu-core or vendor a TTF into "
        f"{ASSET_ROOT / 'fonts'}")


def font(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    """Load the card face at `size`. Weight applies to variable fonts only."""
    f = ImageFont.truetype(str(_font_file()), size)
    try:
        f.set_variation_by_axes([min(size, 144), weight])
    except (AttributeError, OSError):
        pass                      # static font: the file's own weight stands
    return f


def tracked_width(draw: ImageDraw.ImageDraw, text: str, fnt,
                  tracking: float) -> float:
    """Width of `text` at `tracking`, measured without marking the canvas."""
    if not text:
        return 0.0
    glyphs = sum(draw.textlength(ch, font=fnt) for ch in text)
    return glyphs + tracking * (len(text) - 1)


def tracked(draw: ImageDraw.ImageDraw, xy, text: str, fnt, tracking: float,
            fill) -> float:
    """Draw letterspaced text; Pillow has no native tracking."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking
    return x - xy[0]


def _wrap(draw, text: str, fnt, max_w: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def spec_card(step_label: str, title: str, specs: list[str],
              citation: str | None, dest: Path) -> Path:
    """Build a transparent overlay carrying one step's hard numbers.

    `specs` are short callouts, one per line. `citation` is the code reference,
    set apart at the foot of the panel.
    """
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 44
    panel_w = 900
    x0, y0 = 96, 150

    label_f = font(26, 800)
    title_f = font(52, 800)
    spec_f = font(34, 500)
    cite_f = font(25, 600)

    wrapped: list[str] = []
    for s in specs:
        wrapped.extend(_wrap(draw, s, spec_f, panel_w - 2 * pad))
    title_lines = _wrap(draw, title, title_f, panel_w - 2 * pad)

    body_h = (len(title_lines) * 62) + 18 + (len(wrapped) * 48)
    panel_h = pad + 34 + 16 + body_h + (54 if citation else 0) + pad

    panel = Image.new("RGBA", (panel_w, panel_h), (*context.current().ink, 214))
    img.paste(panel, (x0, y0), panel)

    draw.rectangle([x0, y0, x0 + 7, y0 + panel_h], fill=(*context.current().accent, 255))

    y = y0 + pad
    tracked(draw, (x0 + pad, y), step_label.upper(), label_f, 4.5, (*context.current().accent, 255))
    y += 34 + 16

    for line in title_lines:
        draw.text((x0 + pad, y), line, font=title_f, fill=(*context.current().paper, 255))
        y += 62
    y += 18

    for line in wrapped:
        draw.text((x0 + pad, y), line, font=spec_f, fill=(235, 241, 249, 255))
        y += 48

    if citation:
        y += 6
        draw.line([(x0 + pad, y), (x0 + panel_w - pad, y)], fill=(*context.current().rule, 120), width=2)
        y += 16
        draw.text((x0 + pad, y), citation, font=cite_f, fill=(*context.current().rule, 255))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def reference_card(authority: str, code: str, title: str, dest: Path) -> Path:
    """Standards plate: the issuing body, the citation, and its official title.

    Named standards are shown as typeset text rather than an agency logo or
    seal, which would carry usage restrictions and could read as endorsement.
    """
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 40
    panel_w = 800
    x0, y0 = WIDTH - panel_w - 96, 190

    code_f = font(46, 800)
    title_f = font(32, 500)

    # Agency names run long; shrink the eyebrow until it fits rather than
    # letting it run past the plate edge.
    auth_track = 5.0
    auth_f = font(24, 800)
    for size in range(24, 12, -1):
        auth_f = font(size, 800)
        auth_track = 5.0 * size / 24
        if tracked_width(draw, authority.upper(), auth_f,
                         auth_track) <= panel_w - 2 * pad:
            break

    title_lines = _wrap(draw, title, title_f, panel_w - 2 * pad)
    panel_h = pad + 30 + 14 + 56 + 12 + len(title_lines) * 44 + pad

    panel = Image.new("RGBA", (panel_w, panel_h), (*context.current().paper, 232))
    img.paste(panel, (x0, y0), panel)
    draw.rectangle([x0, y0, x0 + panel_w, y0 + 6], fill=(*context.current().accent, 255))

    y = y0 + pad
    tracked(draw, (x0 + pad, y), authority.upper(), auth_f, auth_track,
            (*context.current().ink, 210))
    y += 30 + 14
    draw.text((x0 + pad, y), code, font=code_f, fill=(*context.current().ink, 255))
    y += 56 + 12
    for line in title_lines:
        draw.text((x0 + pad, y), line, font=title_f, fill=(60, 78, 104, 255))
        y += 44

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def title_card(series: str, title: str, subtitle: str, dest: Path) -> Path:
    """Full-frame opening plate."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (*context.current().ink, 255))
    draw = ImageDraw.Draw(img)

    series_f = font(30, 800)
    title_f = font(86, 800)
    sub_f = font(38, 400)

    cx = WIDTH // 2
    width = tracked_width(draw, series.upper(), series_f, 7)
    tracked(draw, (cx - width / 2, 348), series.upper(), series_f, 7, (*context.current().accent, 255))

    lines = _wrap(draw, title, title_f, WIDTH - 420)
    y = 424
    for line in lines:
        draw.text((cx - draw.textlength(line, font=title_f) / 2, y),
                  line, font=title_f, fill=(*context.current().paper, 255))
        y += 104

    y += 14
    draw.line([(cx - 90, y), (cx + 90, y)], fill=(*context.current().accent, 255), width=3)
    y += 40
    draw.text((cx - draw.textlength(subtitle, font=sub_f) / 2, y),
              subtitle, font=sub_f, fill=(*context.current().rule, 255))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def lower_third(text: str, dest: Path) -> Path:
    """Section-heading strip along the lower edge."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fnt = font(40, 700)

    text_w = draw.textlength(text, font=fnt)
    bar_w, bar_h = int(text_w) + 120, 92
    x0, y0 = 96, HEIGHT - 190

    bar = Image.new("RGBA", (bar_w, bar_h), (*context.current().ink, 224))
    img.paste(bar, (x0, y0), bar)
    draw.rectangle([x0, y0, x0 + 7, y0 + bar_h], fill=(*context.current().accent, 255))
    draw.text((x0 + 44, y0 + (bar_h - 52) // 2), text, font=fnt,
              fill=(*context.current().paper, 255))

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest
