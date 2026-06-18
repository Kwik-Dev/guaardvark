"""
Prompt Enhancement Utility for Video Generation.

Enriches user prompts with quality/style descriptors to improve
output from Wan2.2, CogVideoX, and other video generation models.
No LLM calls, no API calls — pure string concatenation.

Supports "fidelity_mode" (light enhancement only) for "Exact text mode"
/ preserve rendered lettering, plus model_family-aware motion hints.
"""

import re
from typing import Optional


def has_text_intent(prompt: str) -> bool:
    """True if the prompt asks for specific text rendered in the image/video.

    Shared by image and video generation. When on-image text is requested we
    skip (or heavily limit) prompt enhancement: the generic quality/style
    boilerplate dilutes the text tokens and the model drops/garbles letters
    (observed examples: HULK -> HUK, "SALE" rendered as gibberish).

    Examples that trigger:
      - A sign that reads "OPEN"
      - Text "FOO BAR" on the wall
      - The word "HELLO" floating in the air
      - Logo with the letters "ACME"
    """
    if not prompt:
        return False
    # Quoted literal: "HULK", 'OPEN', curly quotes "SALE". Concatenate the quote
    # chars so neither quote type breaks the string literal.
    quotes = '"“”‘’' + "'"
    if re.search('[' + quotes + '].{1,60}?[' + quotes + ']', prompt):
        return True
    p = prompt.lower()
    keywords = (
        'text ', 'text:', 'the word', 'the words', 'says ', 'saying ', 'reads ',
        'that reads', 'sign that', 'label', 'caption', 'title ', 'logo', 'letters',
        'written', 'spelled', 'banner', 'headline', 'subtitle', 'billboard', 'slogan',
    )
    return any(k in p for k in keywords)


# Base quality terms (applied once at the end of full enhancements to avoid duplication).
BASE_QUALITY_TERMS = "high quality, masterpiece"

# Light / fidelity-mode terms: safe additions that do not heavily alter style tokens.
# Used for "preserve text fidelity" mode or when has_text_intent is detected but we still
# want to inject light video-quality + motion guidance.
LIGHT_QUALITY_TERMS = "high quality, sharp focus, clean details, good contrast"

# Model-family aware motion/temporal terms (injected to help Wan vs CogVideoX strengths).
MOTION_TERMS = {
    "default": "smooth coherent motion, temporal consistency",
    "wan": "smooth cinematic motion at native frame rate, strong temporal coherence, natural camera movement and dynamics",
    "cogvideox": "expressive fluid animation, detailed and coherent motion, good timing and pacing",
}

# Style-specific descriptors (style flavor only — base quality and motion appended at runtime).
# Kept shorter to reduce boilerplate duplication.
STYLE_SUFFIXES = {
    "cinematic": (
        "Cinematic lighting, shallow depth of field, film grain, "
        "color graded, 35mm film, professional cinematography, smooth natural motion"
    ),
    "realistic": (
        "Photorealistic, natural lighting, ultra detailed, 8K, "
        "sharp focus, realistic textures, lifelike motion"
    ),
    "artistic": (
        "Artistic, painterly, vivid colors, expressive brushstrokes, "
        "dramatic composition, creative motion"
    ),
    "anime": (
        "Anime style, cel shaded, vibrant colors, dynamic poses, "
        "fluid animation, detailed linework"
    ),
    "3d_animation": (
        "3D-animated, Pixar-style polished CGI, expressive characters, "
        "soft global illumination, subsurface scattering, smooth rigging, "
        "appealing character design"
    ),
    "stop_motion": (
        "stop-motion animation, tactile clay textures, handcrafted miniatures, "
        "slight handcraft imperfection between frames, warm practical lighting, "
        "shallow depth of field"
    ),
    "hand_drawn": (
        "hand-drawn 2D animation, Studio Ghibli aesthetic, painterly watercolor "
        "backgrounds, expressive line work, gentle character motion, "
        "soft natural color palette"
    ),
    "western_cartoon": (
        "classic western animated cartoon style, bold outlines, flat shading, "
        "vibrant saturated palette, exaggerated expressions, snappy keyframed motion"
    ),
}

# Quality-focused negative prompts per style.
# These target only technical defects — no content restrictions.
# Video-specific defects (flicker, jitter, temporal inconsistency) added to help motion models.
NEGATIVE_PROMPTS = {
    "cinematic": (
        "blurry, low quality, pixelated, oversaturated, static, "
        "jerky motion, flickering, temporal artifacts, artifacts, distorted, poorly rendered, "
        "low resolution, watermark, text overlay"
    ),
    "realistic": (
        "blurry, low quality, pixelated, overexposed, underexposed, "
        "artifacts, distorted, poorly rendered, plastic skin, "
        "flickering, frame jitter, low resolution, watermark, text overlay"
    ),
    "artistic": (
        "blurry, low quality, pixelated, muddy colors, flat lighting, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, flickering motion"
    ),
    "anime": (
        "blurry, low quality, pixelated, bad anatomy, deformed, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, 3d render, inconsistent animation"
    ),
    "3d_animation": (
        "blurry, low quality, flat shading, low-poly, jagged edges, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, uncanny valley, temporal inconsistency"
    ),
    "stop_motion": (
        "blurry, low quality, smooth digital motion, CGI look, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, overly smooth frames"
    ),
    "hand_drawn": (
        "blurry, low quality, photorealistic, 3d render, plastic textures, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, digital artifacts"
    ),
    "western_cartoon": (
        "blurry, low quality, photorealistic, soft shading, muddy colors, "
        "artifacts, distorted, poorly rendered, low resolution, "
        "watermark, text overlay, inconsistent frame timing"
    ),
    "none": (
        "blurry, low quality, pixelated, artifacts, distorted, "
        "poorly rendered, low resolution, flickering, jerky motion"
    ),
}


def enhance_video_prompt(
    prompt: str,
    style: str = "cinematic",
    width: int = 0,
    height: int = 0,
    *,
    fidelity_mode: bool = False,
    model_family: Optional[str] = None,
) -> str:
    """Enhance a user prompt with quality descriptors for better video generation.

    Preserves the user's original intent and appends style-specific
    quality descriptors.  The ``"none"`` style returns the prompt unchanged.

    When portrait/vertical dimensions are detected (height > width), adds
    composition guidance so the model frames content for vertical viewing.

    fidelity_mode=True (or when has_text_intent is detected) switches to a
    light enhancement path: only safe video-quality + motion + orientation
    hints are added. This is the "preserve text fidelity" / "Exact text mode"
    behavior — the heavy style boilerplate is skipped so on-screen text
    renders reliably.

    model_family ("wan" or "cogvideox") injects tailored temporal/motion terms
    that play to the strengths of each architecture.

    Args:
        prompt: The user's original prompt text.
        style: One of "cinematic", "realistic", "artistic", "anime",
            "3d_animation", "stop_motion", "hand_drawn", "western_cartoon",
            or "none".
        width: Video width in pixels (used for orientation detection).
        height: Video height in pixels (used for orientation detection).
        fidelity_mode: If True, force the light/partial enhancement path even
            for non-text prompts (user-controlled "preserve text fidelity").
        model_family: Optional hint ("wan" | "cogvideox") for motion-term selection.

    Returns:
        The enhanced prompt string.
    """
    if not prompt or not prompt.strip():
        return prompt

    style = (style or "cinematic").lower().strip()

    if style == "none":
        return prompt

    suffix = STYLE_SUFFIXES.get(style)
    if not suffix:
        # Unknown style — return unmodified
        return prompt

    # Normalise trailing punctuation so the join reads naturally
    trimmed = prompt.rstrip()
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."

    # Determine whether to use the light fidelity path.
    # has_text_intent triggers it automatically (to protect rendered lettering).
    # fidelity_mode allows the user (via UI toggle) to request light enhancement
    # even when no explicit text is present.
    is_text_intent = has_text_intent(prompt)
    use_fidelity = fidelity_mode or is_text_intent

    # Portrait/vertical orientation hint (always useful for video framing)
    portrait_hint = ""
    if height > width and width > 0:
        portrait_hint = (
            "Vertical portrait composition, tall framing, subject centered "
            "in frame, close-up or medium shot, no important content at "
            "the left or right edges."
        )

    # Pick motion terms (model-aware when possible)
    fam = (model_family or "default").lower().strip()
    motion = MOTION_TERMS.get(fam, MOTION_TERMS["default"])

    if use_fidelity:
        # LIGHT / FIDELITY PATH — minimal safe additions only.
        # Still gives the model useful video guidance without drowning text tokens.
        parts = [trimmed]
        if portrait_hint:
            parts.append(portrait_hint)
        parts.append(f"{LIGHT_QUALITY_TERMS}, {motion}")
        return " ".join(parts)

    # FULL STYLE PATH
    # Append the (now leaner) style suffix + base quality + model motion.
    # Avoid double-adding if motion language already present (rare).
    full_suffix = suffix
    if motion.lower() not in full_suffix.lower():
        full_suffix = f"{full_suffix}, {motion}"

    # Always end with the single BASE_QUALITY_TERMS (de-duped from old per-style lists)
    if BASE_QUALITY_TERMS.lower() not in full_suffix.lower():
        full_suffix = f"{full_suffix}, {BASE_QUALITY_TERMS}"

    if portrait_hint:
        return f"{trimmed} {portrait_hint} {full_suffix}"

    return f"{trimmed} {full_suffix}"


def get_default_negative_prompt(style: str = "cinematic") -> str:
    """Get a quality-focused negative prompt (no content restrictions).

    Only targets technical defects: blur, artifacts, distortion, flickering,
    temporal issues, etc. Safe to use alongside user negative prompts.

    Args:
        style: One of "cinematic", "realistic", "artistic", "anime",
            "3d_animation", "stop_motion", "hand_drawn", "western_cartoon",
            or "none".

    Returns:
        A negative prompt string focused on quality issues.
    """
    style = (style or "cinematic").lower().strip()
    return NEGATIVE_PROMPTS.get(style, NEGATIVE_PROMPTS["none"])
