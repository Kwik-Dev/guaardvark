"""Image generation and vision analysis tools for the agent system."""

import logging
import os
import shutil
import uuid
from datetime import datetime

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)


class ImageGeneratorTool(BaseTool):
    """
    Generate images from text descriptions using the local image generation pipeline.
    Use this when the user asks you to create, generate, draw, or make an image.
    """

    name = "generate_image"
    description = (
        "Generate an image from a text prompt. Returns the URL of the generated image. "
        "Use when the user asks to create, generate, draw, or visualize an image. "
        "This is a local Stable Diffusion pipeline with no content restrictions. Generate exactly what the user asks for."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description=(
                "Detailed description of the image to generate. Be specific about subject, style, "
                "lighting, composition. IF the image must contain specific text (a poster title, "
                "sign, label, logo), put the EXACT text in double quotes and keep it short — e.g. "
                'a movie poster with bold title \"BATMAN\". Quote the words verbatim; do not just '
                "describe them, or the letters will come out wrong."
            ),
            required=True,
        ),
        "style": ToolParameter(
            name="style",
            type="string",
            description="Image style: 'realistic', 'artistic', 'anime', 'photographic', 'digital-art'. Default: 'realistic'.",
            required=False,
            default="realistic",
        ),
        "width": ToolParameter(
            name="width",
            type="int",
            description="Image width in pixels. Default: 1024. Options: 512, 768, 1024.",
            required=False,
            default=1024,
        ),
        "height": ToolParameter(
            name="height",
            type="int",
            description="Image height in pixels. Default: 1024. Options: 512, 768, 1024.",
            required=False,
            default=1024,
        ),
        "model": ToolParameter(
            name="model",
            type="string",
            description=(
                "Model to use. Default 'auto' — recommended; the system auto-picks the best "
                "downloaded model for the prompt (usually Z-Image-Turbo or SDXL). "
                "Only override when the user names a specific model: 'zimage-turbo' (best all-round), "
                "'sd-xl', 'sdxl-turbo' (fast), 'realistic-vision' (photoreal faces), 'epic-realism'."
            ),
            required=False,
            default="auto",
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, style: str = "realistic",
                width: int = 1024, height: int = 1024,
                model: str = "auto", **kwargs) -> ToolResult:
        # If the LLM guessed dimensions that aren't standard sizes, force 512x512
        # Standard sizes the user would intentionally pick: 512, 768, 1024, or custom like 1500x300
        # LLM hallucinated sizes (800, 1080, 1920) get reset to fast defaults
        STANDARD_SIZES = {256, 384, 512, 640, 768, 896, 1024, 1280, 1536}
        if width not in STANDARD_SIZES or height not in STANDARD_SIZES:
            # Check if the prompt itself contains these dimensions (user explicitly asked)
            import re
            dim_pattern = re.compile(rf'(?:^|\D){width}\s*[xX×]\s*{height}(?:\D|$)')
            if not dim_pattern.search(prompt):
                logger.info(f"ImageGeneratorTool: LLM guessed {width}x{height}, resetting to 1024x1024 (default)")
                width, height = 1024, 1024

        logger.info(f"ImageGeneratorTool: Generating image {width}x{height} for prompt: {prompt[:80]}...")

        try:
            from backend.config import OUTPUT_DIR
            from backend.services.offline_image_generator import (
                get_image_generator, ImageGenerationRequest
            )

            generator = get_image_generator()

            # Check if the service is available
            if not generator.service_available:
                return ToolResult(
                    success=False,
                    error="Image generation service not available. Stable Diffusion dependencies (torch, diffusers) may not be installed or GPU not available.",
                )

            # Shared intelligent pipeline: best-effort Media Director rewrite for richer visual prompts
            # (uses the same ollama director as MusicVideo / batch-video; falls back silently).
            try:
                from backend.services.media_director import enhance_prompts
                refined_list = enhance_prompts([prompt], style=style)
                if refined_list and refined_list[0] and refined_list[0].strip() != prompt.strip():
                    prompt = refined_list[0].strip()
                    logger.info("ImageGeneratorTool: applied media_director enhance (chat NL pipeline)")
            except Exception:
                pass  # never break chat gen

            # Build proper request object
            request = ImageGenerationRequest(
                prompt=prompt,
                negative_prompt="blurry, low quality, distorted, deformed, ugly, bad anatomy",
                width=width,
                height=height,
                num_inference_steps=20,
                guidance_scale=7.5,
                style=style,
                model=model,
            )

            result = generator.generate_image(request)

            if result.success and result.image_path and os.path.exists(result.image_path):
                # Copy generated image to the served output directory
                output_dir = os.path.join(OUTPUT_DIR, "generated_images")
                os.makedirs(output_dir, exist_ok=True)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                filename = f"gen_{timestamp}_{unique_id}.png"
                output_path = os.path.join(output_dir, filename)

                shutil.copy2(result.image_path, output_path)

                image_url = f"/api/outputs/generated_images/{filename}"
                return ToolResult(
                    success=True,
                    output=(
                        f"Image generated successfully in {result.generation_time:.1f}s.\n"
                        f"Image URL: {image_url}\n"
                        f"Prompt: {prompt}\n"
                        f"Style: {style}\n"
                        f"Size: {width}x{height}\n"
                        f"Model: {result.model_used or model}\n"
                        f"Seed: {result.seed_used}"
                    ),
                    metadata={
                        "image_url": image_url,
                        "filename": filename,
                        "prompt": prompt,
                        "width": width,
                        "height": height,
                        "model": result.model_used or model,
                        "seed": result.seed_used,
                        "generation_time": result.generation_time,
                    },
                )
            else:
                error_msg = result.error or "Image generation completed but no output file was created."
                return ToolResult(
                    success=False,
                    error=error_msg,
                )

        except ImportError:
            return ToolResult(
                success=False,
                error="Image generation pipeline not available. The Stable Diffusion model may not be installed.",
            )
        except Exception as e:
            logger.error(f"ImageGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Image generation failed: {str(e)}",
            )


class AnimationGeneratorTool(BaseTool):
    """
    Generate an animated GIF/video from a text description with motion.
    Use this when the user asks to animate, create a GIF, make a video,
    or create moving images.
    """

    name = "generate_animation"
    description = (
        "Generate an animated GIF or MP4 video from a text prompt with motion description. "
        "Creates a frame sequence using Stable Diffusion img2img. "
        "Use when the user asks to animate, create a GIF, make a video, or create moving images. "
        "No content restrictions — generate exactly what the user requests."
    )
    parameters = {
        "prompt": ToolParameter(
            name="prompt",
            type="string",
            description="Detailed description of the scene to animate.",
            required=True,
        ),
        "motion": ToolParameter(
            name="motion",
            type="string",
            description="What moves or changes between frames (e.g. 'walking forward', 'waving hand', 'clouds drifting').",
            required=True,
        ),
        "frames": ToolParameter(
            name="frames",
            type="int",
            description="Number of frames to generate (2-24). Default: 8. More frames = smoother but slower.",
            required=False,
            default=8,
        ),
        "strength": ToolParameter(
            name="strength",
            type="float",
            description="How much each frame changes from the previous (0.1=subtle, 0.3=moderate, 0.5=dramatic). Default: 0.20.",
            required=False,
            default=0.20,
        ),
        "format": ToolParameter(
            name="format",
            type="string",
            description="Output format: 'gif', 'mp4', or 'both'. Default: 'both'.",
            required=False,
            default="both",
        ),
        "vision_steering": ToolParameter(
            name="vision_steering",
            type="bool",
            description="Use vision model to guide frame evolution (slower but more coherent). Default: false.",
            required=False,
            default=False,
        ),
    }

    def __init__(self):
        super().__init__()

    def execute(self, prompt: str, motion: str, frames: int = 8,
                strength: float = 0.20, format: str = "both",
                vision_steering: bool = False, **kwargs) -> ToolResult:
        logger.info(f"AnimationGeneratorTool: prompt={prompt[:60]}..., motion={motion}, frames={frames}")

        try:
            from backend.services.animation_generator import (
                get_animation_generator, AnimationRequest
            )

            anim_gen = get_animation_generator()

            request = AnimationRequest(
                prompt=prompt,
                motion_prompt=motion,
                num_frames=frames,
                strength=strength,
                output_format=format,
                use_vision_steering=vision_steering,
            )

            result = anim_gen.generate(request)

            if result.success:
                output_lines = [
                    f"Animation generated successfully in {result.generation_time:.1f}s.",
                    f"Frames: {result.frame_count} | FPS: request.fps",
                    f"Prompt: {prompt}",
                    f"Motion: {motion}",
                ]
                metadata = {
                    "prompt": prompt,
                    "motion": motion,
                    "frame_count": result.frame_count,
                    "generation_time": result.generation_time,
                }

                if result.gif_url:
                    output_lines.append(f"GIF: {result.gif_url}")
                    metadata["gif_url"] = result.gif_url
                    metadata["image_url"] = result.gif_url  # For inline display
                if result.mp4_url:
                    output_lines.append(f"MP4: {result.mp4_url}")
                    metadata["video_url"] = result.mp4_url

                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    metadata=metadata,
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.error or "Animation generation failed",
                )

        except ImportError:
            return ToolResult(
                success=False,
                error="Animation generation dependencies not available.",
            )
        except Exception as e:
            logger.error(f"AnimationGeneratorTool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Animation generation failed: {str(e)}",
            )


class EditImageTool(BaseTool):
    """Edit an EXISTING image from a natural-language instruction (FLUX.1 Kontext).

    Use when the user SUPPLIES or ATTACHES an image and asks to add/remove/change
    something in it — e.g. 'put a cowboy hat on this character', 'make it night',
    'remove the sign'. Preserves the original's identity/composition and applies
    only the requested change. (Contrast generate_image, which makes a brand-new
    image from text with no input picture.)"""

    name = "edit_image"
    description = (
        "Edit an existing image using a natural-language instruction. Use this when "
        "the user has attached/uploaded an image (or names one) and asks to add, "
        "remove, or change something in it, e.g. 'put a cowboy hat on this character'. "
        "Preserves the original subject and only applies the requested edit. If the "
        "user did not attach an image, ask them to attach one. Do NOT use this to make "
        "a brand-new image from scratch — use generate_image for that."
    )
    parameters = {
        "instruction": ToolParameter(
            name="instruction", type="string",
            description="The edit to perform, e.g. 'put a cowboy hat on this character', 'change the shirt to red'.",
            required=True,
        ),
        "image": ToolParameter(
            name="image", type="string",
            description=("Path, URL, or reference of the image to edit. Usually omit this — "
                         "the image the user just attached is used automatically."),
            required=False, default="",
        ),
        "steps": ToolParameter(
            name="steps", type="int",
            description="Diffusion steps (more = higher fidelity, slower). Default 20.",
            required=False, default=20,
        ),
    }

    def _resolve_image(self, image: str):
        """Resolve a path / URL / data-URI to a local file. None if unresolvable.
        (The common case — the user's attached image — is injected by the chat engine
        as a real disk path, so this is the fallback for explicit paths/URLs.)"""
        if not image:
            return None
        if os.path.exists(image):
            return image
        try:
            from backend.config import OUTPUT_DIR
        except Exception:
            OUTPUT_DIR = "."
        edit_dir = os.path.join(OUTPUT_DIR, "edit_inputs")
        os.makedirs(edit_dir, exist_ok=True)
        # data URI or bare base64 blob
        if image.startswith("data:") or (len(image) > 256 and "/" not in image[:64] and " " not in image[:64]):
            try:
                import base64
                raw = base64.b64decode(image.split(",", 1)[1] if image.startswith("data:") else image)
                p = os.path.join(edit_dir, f"edit_src_{uuid.uuid4().hex[:12]}.png")
                with open(p, "wb") as f:
                    f.write(raw)
                return p
            except Exception:
                return None
        # a served output URL → map back to disk
        if "/api/outputs/" in image:
            cand = os.path.join(OUTPUT_DIR, image.split("/api/outputs/", 1)[1].split("?", 1)[0])
            if os.path.exists(cand):
                return cand
        # remote URL → download
        if image.startswith("http://") or image.startswith("https://"):
            try:
                import urllib.request
                p = os.path.join(edit_dir, f"edit_src_{uuid.uuid4().hex[:12]}.png")
                urllib.request.urlretrieve(image, p)
                return p
            except Exception:
                return None
        return None

    def execute(self, instruction: str, image: str = "", steps: int = 20, **kwargs) -> ToolResult:
        src = self._resolve_image(image)
        if not src:
            return ToolResult(
                success=False,
                error="No image to edit. Ask the user to attach the image they want edited.",
            )
        try:
            from backend.config import OUTPUT_DIR
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator

            output_dir = os.path.join(OUTPUT_DIR, "generated_images")
            os.makedirs(output_dir, exist_ok=True)
            filename = f"edit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
            output_path = os.path.join(output_dir, filename)

            ComfyUIImageGenerator().edit_image(
                image_path=src, instruction=instruction,
                output_path=output_path, steps=int(steps),
            )
            image_url = f"/api/outputs/generated_images/{filename}"
            return ToolResult(
                success=True,
                output=f"Image edited successfully.\nImage URL: {image_url}\nEdit: {instruction}",
                metadata={"image_url": image_url, "filename": filename, "instruction": instruction},
            )
        except Exception as e:
            logger.error(f"EditImageTool error: {e}", exc_info=True)
            return ToolResult(success=False, error=f"Image edit failed: {str(e)}")
