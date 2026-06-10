"""REST API for selecting the active LLM provider (Ollama vs Mistral)."""
import logging

from flask import Blueprint, request

from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

llm_provider_bp = Blueprint("llm_provider", __name__, url_prefix="/api/llm")


@llm_provider_bp.route("/provider", methods=["GET"])
def get_provider():
    """Current provider + what's available, for the settings toggle."""
    from backend.services import llm_provider as lp
    return success_response(data={
        "provider": lp.get_active_provider(),
        "ollama_available": True,
        "mistral_available": lp.mistral_available(),
        "mistral_model": lp.get_mistral_model(),
        "providers": [
            {"id": lp.OLLAMA, "label": "Ollama (local)", "available": True},
            {"id": lp.MISTRAL, "label": "Mistral (cloud API)", "available": lp.mistral_available()},
        ],
    })


@llm_provider_bp.route("/provider", methods=["POST"])
def set_provider():
    """Switch the active provider. Body: {"provider": "ollama"|"mistral"}."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    provider = body.get("provider", "")
    try:
        active = lp.set_active_provider(provider)
    except ValueError as e:
        return error_response(str(e), 400)
    return success_response(data={"provider": active}, message=f"LLM provider set to {active}")


@llm_provider_bp.route("/provider/models", methods=["GET"])
def list_provider_models():
    """List models for a provider (?provider=mistral; defaults to the active one)."""
    from backend.services import llm_provider as lp
    provider = (request.args.get("provider") or lp.get_active_provider()).strip().lower()
    if provider == lp.MISTRAL:
        if not lp.mistral_available():
            return error_response("Mistral API key not configured (set MISTRAL_API_KEY in .env).", 400)
        from backend.services import mistral_provider
        return success_response(data={"provider": provider, "models": mistral_provider.list_models()})
    # Ollama listing already has a dedicated endpoint; point callers there.
    return success_response(data={"provider": provider, "models": [], "see": "/api/model/list"})


@llm_provider_bp.route("/provider/mistral-model", methods=["POST"])
def set_mistral_model():
    """Set the active Mistral model. Body: {"model": "mistral-large-latest"}."""
    from backend.services import llm_provider as lp
    body = request.get_json(silent=True) or {}
    try:
        model = lp.set_mistral_model(body.get("model", ""))
    except ValueError as e:
        return error_response(str(e), 400)
    return success_response(data={"mistral_model": model}, message=f"Mistral model set to {model}")


@llm_provider_bp.route("/provider/test", methods=["POST"])
def test_mistral():
    """Live round-trip against Mistral to confirm the key/model work."""
    from backend.services import llm_provider as lp
    if not lp.mistral_available():
        return error_response("Mistral API key not configured (set MISTRAL_API_KEY in .env).", 400)
    from backend.services import mistral_provider
    try:
        text = mistral_provider.complete(
            "Reply with exactly: Connection successful",
            model=lp.get_mistral_model(),
        )
    except Exception as e:  # noqa: BLE001
        return error_response(f"Mistral request failed: {e}", 503)
    return success_response(data={"connected": True, "response": text, "model": lp.get_mistral_model()})
