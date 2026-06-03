# backend/api/websites_api.py
# Version 1.2: Added explicit project_id check in create_website_route.
# - MODIFIED: Corrected sitemap handling to prevent AttributeError for NoneType (from original v1.1).
# - MODIFIED: Ensured create_website_route explicitly checks for project_id.

# --- Standard Imports ---
import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import defer, joinedload, selectinload

# --- Database Models ---
from backend.models import Client, Document, Project, Website, WebsitePage, db

# --- End Database Models ---


# --- Blueprint Definition ---
websites_bp = Blueprint("websites_api", __name__, url_prefix="/api/websites")
logger = logging.getLogger(__name__)


# --- Helper Function for Serialization ---
def website_to_dict(website):
    from backend.utils.serialization_utils import format_logo_path
    if not website:
        return None

    project_info = None
    if hasattr(website, "project") and website.project:
        project_info = {"id": website.project.id, "name": website.project.name}
    elif website.project_id:
        project_info = {
            "id": website.project_id,
            "name": f"Project ID {website.project_id}",
        }

    client_info = None
    if hasattr(website, "client_ref") and website.client_ref:
        client = website.client_ref
        client_info = {
            "id": client.id,
            "name": client.name,
            "logo_path": format_logo_path(client.logo_path),
            "email": client.email,
            "phone": client.phone,
            "location": client.location,
            "contact_url": client.contact_url,
            "primary_service": client.primary_service,
            "secondary_service": client.secondary_service,
            "brand_tone": client.brand_tone,
            "business_hours": client.business_hours,
            "social_links": client.social_links,
        }
    elif website.client_id:
        client_info = {
            "id": website.client_id,
            "name": f"Client ID {website.client_id}",
        }

    doc_count = 0
    try:
        # Count direct website documents (crawled content)
        website_docs = 0
        if hasattr(website, "documents") and website.documents:
            website_docs = website.documents.count()
        
        # Count project documents (since website belongs to project) 
        project_docs = 0
        if hasattr(website, "project") and website.project:
            try:
                # Use scalar query for dynamic relationship count
                from backend.models import Document
                project_docs = db.session.query(Document).filter(Document.project_id == website.project.id).count()
            except Exception as count_error:
                _logger = current_app.logger if current_app else logging.getLogger(__name__)
                _logger.warning(f"Failed to count project documents for website {website.id}: {count_error}")
                project_docs = 0
        
        # Total: direct website docs + project docs
        doc_count = website_docs + project_docs
        
    except Exception as e:
        # Use a logger instance, assuming logger is defined globally or passed
        _logger = current_app.logger if current_app else logging.getLogger(__name__)
        _logger.warning(
            f"Could not determine document count for website ID {website.id}: {e}"
        )
        doc_count = "N/A"  # Or some other placeholder

    return {
        "id": website.id,
        "project_id": website.project_id,
        "client_id": getattr(website, "client_id", None),
        "url": website.url,
        "sitemap": website.sitemap,  # Original sitemap field from model
        "competitor_url": getattr(website, "competitor_url", None),
        "local_path": getattr(website, "local_path", None),
        "project": project_info,
        "client": client_info,
        "document_count": doc_count,  # Add document count
        # Add other fields from your model as needed
        "status": getattr(website, "status", "pending"),
        "last_crawled": (
            getattr(website, "last_crawled", None).isoformat()
            if getattr(website, "last_crawled", None)
            else None
        ),
        "created_at": website.created_at.isoformat() if website.created_at else None,
        "updated_at": website.updated_at.isoformat() if website.updated_at else None,
    }


# --- API Routes ---


@websites_bp.route("/", methods=["GET"])
def get_websites_route():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received GET /api/websites request")

    if not db or not Website or not Project:
        logger.error(
            "API Error (GET /websites): db object or Website/Project model is None or unavailable."
        )
        return (
            jsonify({"error": "Server configuration error: DB or models missing."}),
            500,
        )

    try:
        query = db.session.query(Website).options(
            joinedload(Website.project),  # Load project but not documents (lazy="dynamic")
            joinedload(Website.client_ref),
        )

        project_id_filter_str = request.args.get("project_id")
        if project_id_filter_str:
            try:
                project_id_filter = int(project_id_filter_str)
                logger.info(
                    f"API: Filtering websites by project_id: {project_id_filter}"
                )
                query = query.filter(Website.project_id == project_id_filter)
            except ValueError:
                logger.warning(
                    f"API: Invalid project_id format for filtering: {project_id_filter_str}. Ignoring filter."
                )

        websites = query.order_by(Website.url).all()
        websites_list = [website_to_dict(website) for website in websites]
        logger.info(f"API: Found {len(websites_list)} websites.")
        return jsonify(websites_list), 200

    except SQLAlchemyError as e:
        logger.error(f"API Error (GET /websites): Database error - {e}", exc_info=True)
        db.session.rollback()
        return (
            jsonify({"error": "Database error fetching websites", "details": str(e)}),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /websites): Unexpected error - {e}", exc_info=True
        )
        db.session.rollback()
        return (
            jsonify(
                {"error": "An unexpected server error occurred", "details": str(e)}
            ),
            500,
        )


@websites_bp.route("/", methods=["POST"])
def create_website_route():
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info("API: Received POST /api/websites/ request")
    if not db or not Website or not Project:
        logger.error(
            "API Error (POST /websites): db object or Website/Project model is None or unavailable."
        )
        return (
            jsonify({"error": "Server configuration error: DB or models missing."}),
            500,
        )

    if not request.is_json:
        logger.warning("API Error (POST /websites): Request body not JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logger.debug(f"API: Request data: {data}")

    url = data.get("url")
    sitemap_from_payload = data.get(
        "sitemap"
    )  # Keep sitemap_url for payload consistency if frontend sends that
    if sitemap_from_payload is None:  # Check if frontend sent 'sitemap_url' instead
        sitemap_from_payload = data.get("sitemap_url")

    # MODIFIED: Explicitly check for project_id
    project_id_str = data.get("project_id")
    if project_id_str is None:  # Handles if key is missing or value is null
        logger.warning(
            f"API Error (POST /websites): Missing required field 'project_id'. Data: {data}"
        )
        return jsonify({"error": "Missing required field: project_id"}), 400
    project_id_str = str(
        project_id_str
    )  # Ensure it's a string for int conversion if not null

    if not url or not url.strip():
        return jsonify({"error": "Website URL cannot be empty."}), 400
    if not url.startswith(("http://", "https://")):
        return (
            jsonify(
                {"error": "Invalid URL format. Must start with http:// or https://"}
            ),
            400,
        )

    url = url.strip()
    sitemap_cleaned = sitemap_from_payload.strip() if sitemap_from_payload else None
    
    # Extract competitor_url
    competitor_url = data.get("competitor_url")
    competitor_url_cleaned = competitor_url.strip() if competitor_url else None

    # Extract local_path (local source folder for swarm/agent code runs)
    local_path = data.get("local_path")
    local_path_cleaned = local_path.strip() if local_path else None

    try:
        project_id = int(project_id_str)
        project_exists = db.session.query(
            Project.query.filter_by(id=project_id).exists()
        ).scalar()
        if not project_exists:
            logger.warning(
                f"API Error (POST /websites): Project with ID {project_id} not found."
            )
            return jsonify({"error": f"Project with ID {project_id} not found."}), 404
    except (ValueError, TypeError):
        logger.warning(
            f"API Error (POST /websites): Invalid project_id format: {project_id_str}"
        )
        return jsonify({"error": "Invalid project_id format. Must be an integer."}), 400
    except SQLAlchemyError as e:
        logger.error(
            f"API Error (POST /websites): DB error checking project ID {project_id_str} - {e}",
            exc_info=True,
        )
        db.session.rollback()
        return (
            jsonify(
                {"error": "Database error validating project ID.", "details": str(e)}
            ),
            500,
        )

    client_id = None
    client_id_val = data.get("client_id")
    if client_id_val is not None:
        try:
            client_id = int(str(client_id_val))
            client_exists = (
                db.session.query(Client.id).filter_by(id=client_id).scalar() is not None
            )
            if not client_exists:
                return jsonify({"error": f"Client with ID {client_id} not found."}), 404
        except (ValueError, TypeError):
            return (
                jsonify({"error": "Invalid client_id format. Must be an integer."}),
                400,
            )
        except SQLAlchemyError as e:
            db.session.rollback()
            return (
                jsonify(
                    {"error": "Database error validating client ID.", "details": str(e)}
                ),
                500,
            )

    try:
        new_website = Website(
            url=url,
            sitemap=sitemap_cleaned,
            competitor_url=competitor_url_cleaned,
            local_path=local_path_cleaned,
            project_id=project_id,
            client_id=client_id
        )
        db.session.add(new_website)
        db.session.commit()
        website_id = new_website.id
        logger.info(
            f"API: Created website '{url}' with ID: {website_id} for Project ID: {project_id}"
        )
        created_website = (
            db.session.query(Website)
            .options(joinedload(Website.project), joinedload(Website.client_ref))
            .get(website_id)
        )
        return jsonify(website_to_dict(created_website)), 201

    except IntegrityError as e:
        db.session.rollback()
        logger.warning(
            f"API Error (POST /websites): Integrity error - {e}", exc_info=True
        )
        error_detail = (
            "Website creation failed due to data conflict (e.g., duplicate URL?)."
        )
        if (
            hasattr(e, "orig")
            and e.orig
            and "UNIQUE constraint failed: websites.url" in str(e.orig)
        ):
            error_detail = "A website with this URL already exists."
        return jsonify({"error": error_detail, "details": str(e)}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"API Error (POST /websites): Database error - {e}", exc_info=True)
        return (
            jsonify({"error": "Database error creating website", "details": str(e)}),
            500,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"API Error (POST /websites): Unexpected error - {e}", exc_info=True
        )
        return (
            jsonify(
                {"error": "An unexpected server error occurred", "details": str(e)}
            ),
            500,
        )


@websites_bp.route("/<int:website_id>", methods=["PUT"])
def update_website_route(website_id):
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received PUT /api/websites/{website_id} request")
    if not db or not Website or not Project:
        logger.error(
            f"API Error (PUT /websites/{website_id}): db object or models unavailable."
        )
        return (
            jsonify({"error": "Server configuration error: DB or models missing."}),
            500,
        )

    if not request.is_json:
        logger.warning(
            f"API Error (PUT /websites/{website_id}): Request body not JSON."
        )
        return jsonify({"error": "Request must be JSON"}), 400

    website = db.session.get(Website, website_id)
    if website is None:
        logger.warning(f"API Error (PUT /websites/{website_id}): Website not found.")
        return jsonify({"error": "Website not found"}), 404

    data = request.get_json()
    logger.debug(f"API: Request data: {data}")

    updated_fields = []
    try:
        if "url" in data:
            new_url = data["url"].strip()
            if not new_url:
                return jsonify({"error": "Website URL cannot be empty."}), 400
            if not new_url.startswith(("http://", "https://")):
                return jsonify({"error": "Invalid URL format."}), 400
            if website.url != new_url:
                website.url = new_url
                updated_fields.append("url")

        # Handle 'sitemap' or 'sitemap_url' from payload
        sitemap_payload_val = None
        if "sitemap" in data:
            sitemap_payload_val = data.get("sitemap")
        elif "sitemap_url" in data:  # Check for sitemap_url if sitemap is not present
            sitemap_payload_val = data.get("sitemap_url")

        if (
            sitemap_payload_val is not None
        ):  # If either key provided some value (even empty string)
            new_sitemap_stripped = (
                sitemap_payload_val.strip() if sitemap_payload_val else None
            )
            if website.sitemap != new_sitemap_stripped:
                website.sitemap = new_sitemap_stripped
                updated_fields.append("sitemap")
        
        # Handle competitor_url
        if "competitor_url" in data:
            new_competitor_url = data.get("competitor_url")
            new_competitor_url_stripped = (
                new_competitor_url.strip() if new_competitor_url else None
            )
            if website.competitor_url != new_competitor_url_stripped:
                website.competitor_url = new_competitor_url_stripped
                updated_fields.append("competitor_url")

        # Handle local_path (local source folder for swarm/agent code runs)
        if "local_path" in data:
            new_local_path = data.get("local_path")
            new_local_path_stripped = (
                new_local_path.strip() if new_local_path else None
            )
            if website.local_path != new_local_path_stripped:
                website.local_path = new_local_path_stripped
                updated_fields.append("local_path")

        if "project_id" in data:
            new_project_id_val = data.get("project_id")
            if new_project_id_val is None:
                return (
                    jsonify(
                        {"error": "Project ID cannot be set to null for a website."}
                    ),
                    400,
                )
            else:
                try:
                    new_project_id = int(str(new_project_id_val))
                    if website.project_id != new_project_id:
                        new_project_exists = db.session.query(
                            Project.query.filter_by(id=new_project_id).exists()
                        ).scalar()
                        if not new_project_exists:
                            return (
                                jsonify(
                                    {
                                        "error": f"Project with ID {new_project_id} not found."
                                    }
                                ),
                                404,
                            )
                        website.project_id = new_project_id
                        updated_fields.append("project_id")
                except (ValueError, TypeError):
                    return (
                        jsonify(
                            {"error": "Invalid project_id format. Must be an integer."}
                        ),
                        400,
                    )
                except SQLAlchemyError as e:
                    db.session.rollback()
                    return (
                        jsonify(
                            {
                                "error": "Database error validating project ID.",
                                "details": str(e),
                            }
                        ),
                        500,
                    )

        if "client_id" in data:
            new_client_val = data.get("client_id")
            if new_client_val is None:
                if website.client_id is not None:
                    website.client_id = None
                    updated_fields.append("client_id (unset)")
            else:
                try:
                    new_client_id = int(str(new_client_val))
                    if website.client_id != new_client_id:
                        client_exists = (
                            db.session.query(Client.id)
                            .filter_by(id=new_client_id)
                            .scalar()
                            is not None
                        )
                        if not client_exists:
                            return (
                                jsonify(
                                    {
                                        "error": f"Client with ID {new_client_id} not found."
                                    }
                                ),
                                404,
                            )
                        website.client_id = new_client_id
                        updated_fields.append("client_id")
                except (ValueError, TypeError):
                    return (
                        jsonify(
                            {"error": "Invalid client_id format. Must be an integer."}
                        ),
                        400,
                    )
                except SQLAlchemyError as e:
                    db.session.rollback()
                    return (
                        jsonify(
                            {
                                "error": "Database error validating client ID.",
                                "details": str(e),
                            }
                        ),
                        500,
                    )

        if not updated_fields:
            logger.info(
                f"API Info (PUT /websites/{website_id}): No valid or changed fields provided."
            )
            current_website_with_project = (
                db.session.query(Website)
                .options(joinedload(Website.project), joinedload(Website.client_ref))
                .get(website_id)
            )
            return jsonify(website_to_dict(current_website_with_project)), 200

        db.session.commit()
        logger.info(
            f"API: Updated website ID: {website_id}. Fields: {', '.join(updated_fields)}"
        )
        updated_website_with_project = (
            db.session.query(Website)
            .options(joinedload(Website.project), joinedload(Website.client_ref))
            .get(website_id)
        )
        return jsonify(website_to_dict(updated_website_with_project)), 200

    except IntegrityError as e:
        db.session.rollback()
        logger.warning(
            f"API Error (PUT /websites/{website_id}): Integrity error - {e}",
            exc_info=True,
        )
        error_detail = (
            "Website update failed due to data conflict (e.g., duplicate URL?)."
        )
        if (
            hasattr(e, "orig")
            and e.orig
            and "UNIQUE constraint failed: websites.url" in str(e.orig)
        ):
            error_detail = "A website with this URL already exists."
        return jsonify({"error": error_detail, "details": str(e)}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (PUT /websites/{website_id}): Database error - {e}",
            exc_info=True,
        )
        return (
            jsonify({"error": "Database error updating website", "details": str(e)}),
            500,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"API Error (PUT /websites/{website_id}): Unexpected error - {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "An unexpected server error occurred", "details": str(e)}
            ),
            500,
        )


@websites_bp.route("/<int:website_id>", methods=["DELETE"])
def delete_website_route(website_id):
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received DELETE /api/websites/{website_id} request")
    if not db or not Website:
        logger.error(
            f"API Error (DELETE /websites/{website_id}): db object or Website model unavailable."
        )
        return (
            jsonify({"error": "Server configuration error: DB or models missing."}),
            500,
        )

    website = db.session.get(Website, website_id)
    if website is None:
        logger.warning(f"API Error (DELETE /websites/{website_id}): Website not found.")
        return jsonify({"error": "Website not found"}), 404

    try:
        website_url = website.url
        db.session.delete(website)
        db.session.commit()
        logger.info(f"API: Deleted website ID: {website_id} (URL: '{website_url}')")
        return (
            jsonify(
                {
                    "message": f"Website '{website_url}' (ID: {website_id}) deleted successfully."
                }
            ),
            200,
        )

    except IntegrityError as e:
        db.session.rollback()
        logger.error(
            f"API Error (DELETE /websites/{website_id}): Integrity error (likely related entities exist) - {e}",
            exc_info=True,
        )
        # Provide a more specific message if it's a foreign key constraint violation
        if (
            "FOREIGN KEY constraint failed" in str(e.orig).lower()
            or "violates foreign key constraint" in str(e.orig).lower()
        ):
            return (
                jsonify(
                    {
                        "error": "Cannot delete website. It is still associated with documents. Please reassign or delete associated documents first.",
                        "details": str(e.orig if hasattr(e, "orig") else e),
                    }
                ),
                409,
            )
        return (
            jsonify(
                {
                    "error": "Cannot delete website due to existing associations.",
                    "details": str(e.orig if hasattr(e, "orig") else e),
                }
            ),
            409,
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(
            f"API Error (DELETE /websites/{website_id}): Database error - {e}",
            exc_info=True,
        )
        return (
            jsonify({"error": "Database error deleting website", "details": str(e)}),
            500,
        )
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"API Error (DELETE /websites/{website_id}): Unexpected error - {e}",
            exc_info=True,
        )
        return (
            jsonify(
                {"error": "An unexpected server error occurred", "details": str(e)}
            ),
            500,
        )


@websites_bp.route("/<int:website_id>", methods=["GET"])
def get_website_details_route(website_id):
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received GET /api/websites/{website_id} request")
    if not db or not Website or not Project:
        logger.error(
            f"API Error (GET /websites/{website_id}): db object or models unavailable."
        )
        return (
            jsonify({"error": "Server configuration error: DB or models missing."}),
            500,
        )

    try:
        website = (
            db.session.query(Website)
            .options(joinedload(Website.project), joinedload(Website.client_ref))
            .get(website_id)
        )

        if website is None:
            logger.warning(
                f"API Error (GET /websites/{website_id}): Website not found."
            )
            return jsonify({"error": "Website not found"}), 404

        logger.info(f"API: Found website ID: {website_id} (URL: '{website.url}')")
        return jsonify(website_to_dict(website)), 200

    except SQLAlchemyError as e:
        logger.error(
            f"API Error (GET /websites/{website_id}): Database error - {e}",
            exc_info=True,
        )
        db.session.rollback()
        return (
            jsonify(
                {"error": "Database error fetching website details", "details": str(e)}
            ),
            500,
        )
    except Exception as e:
        logger.error(
            f"API Error (GET /websites/{website_id}): Unexpected error - {e}",
            exc_info=True,
        )
        db.session.rollback()
        return (
            jsonify(
                {"error": "An unexpected server error occurred", "details": str(e)}
            ),
            500,
        )


@websites_bp.route("/<int:website_id>/scrape", methods=["POST"])
def scrape_website_route(website_id):
    """Queue a background sitemap crawl (Task-backed). Returns 202 immediately.

    Replaces the old synchronous single-page scrape: a crawl now walks the
    sitemap, persists each page as a WebsitePage, and shows up in Activity.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    logger.info(f"API: Received POST /api/websites/{website_id}/scrape request")

    if not db or not Website:
        logger.error("API Error (SCRAPE): DB or Website model unavailable")
        return jsonify({"error": "Server configuration error"}), 500

    website = db.session.get(Website, website_id)
    if website is None:
        logger.warning(f"API Error (SCRAPE): Website {website_id} not found")
        return jsonify({"error": "Website not found"}), 404

    options = request.get_json(silent=True) if request.is_json else {}
    options = options or {}
    max_pages = options.get("max_pages")
    schedule_at = options.get("schedule_at")  # ISO datetime → schedule for later

    try:
        from backend.services.website_jobs.job_service import queue_crawl_run

        payload = queue_crawl_run(
            website_id, max_pages=max_pages, created_by="websites", schedule_at=schedule_at
        )
    except Exception as e:
        logger.error(
            f"Failed to queue crawl for website {website_id}: {e}", exc_info=True
        )
        return (
            jsonify(
                {
                    "error": "Could not queue crawl job (is the Celery worker running?)",
                    "details": str(e),
                }
            ),
            503,
        )

    return jsonify(payload), 202


@websites_bp.route("/<int:website_id>/code-run", methods=["POST"])
def code_run_route(website_id):
    """Queue a local CODE run (swarm/agent) on the website's local_path folder.

    Body: {mode: 'swarm'|'agent', instructions?: str, schedule_at?: ISO}.
    Returns 202 with the Task payload. Phase 2D — swarm mode needs the swarm plugin
    enabled + an LLM; agent mode is gated pending external-folder tool rooting.
    """
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    if db.session.get(Website, website_id) is None:
        return jsonify({"error": "Website not found"}), 404

    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "swarm").strip().lower()
    instructions = data.get("instructions") or ""
    schedule_at = data.get("schedule_at")

    try:
        from backend.services.website_jobs.job_service import queue_code_run

        payload = queue_code_run(
            website_id,
            mode=mode,
            instructions=instructions,
            created_by="websites",
            schedule_at=schedule_at,
        )
        return jsonify(payload), 202
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Failed to queue code run for website {website_id}: {e}", exc_info=True)
        return jsonify({"error": "Could not queue code run", "details": str(e)}), 503


@websites_bp.route("/<int:website_id>/pages", methods=["GET"])
def list_website_pages_route(website_id):
    """List crawled pages for a website (newest crawl first). Content omitted."""
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    if db.session.get(Website, website_id) is None:
        return jsonify({"error": "Website not found"}), 404

    status_filter = request.args.get("status")
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except (ValueError, TypeError):
        limit = 200
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    try:
        query = db.session.query(WebsitePage).filter_by(website_id=website_id)
        if status_filter:
            query = query.filter_by(status=status_filter)
        total = query.count()
        rows = (
            query.order_by(WebsitePage.crawled_at.desc(), WebsitePage.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return (
            jsonify(
                {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "pages": [r.to_dict(include_content=False) for r in rows],
                }
            ),
            200,
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"API Error (GET pages for {website_id}): {e}", exc_info=True)
        return jsonify({"error": "Database error fetching pages", "details": str(e)}), 500


@websites_bp.route("/<int:website_id>/pages/<int:page_id>", methods=["GET"])
def get_website_page_route(website_id, page_id):
    """Get one crawled page including its full content."""
    page = db.session.get(WebsitePage, page_id)
    if page is None or page.website_id != website_id:
        return jsonify({"error": "Page not found"}), 404
    return jsonify(page.to_dict(include_content=True)), 200
