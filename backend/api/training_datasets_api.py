import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

try:
    from backend.models import TrainingDataset, db
except ImportError as e:
    logging.critical(f"Failed to import TrainingDataset model: {e}", exc_info=True)
    db = TrainingDataset = None

training_bp = Blueprint(
    "training_datasets", __name__, url_prefix="/api/training_datasets"
)
logger = logging.getLogger(__name__)


def serialize(dataset):
    if not dataset:
        return None
    return (
        dataset.to_dict()
        if hasattr(dataset, "to_dict")
        else {
            "id": dataset.id,
            "name": dataset.name,
            "description": dataset.description,
            "path": dataset.path,
            "created_at": (
                dataset.created_at.isoformat() if dataset.created_at else None
            ),
            "updated_at": (
                dataset.updated_at.isoformat() if dataset.updated_at else None
            ),
        }
    )


@training_bp.route("", methods=["GET"])
def list_datasets():
    logger.info("API: GET /api/training_datasets")
    if not db or not TrainingDataset:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        items = TrainingDataset.query.order_by(TrainingDataset.created_at.desc()).all()
        return jsonify([serialize(d) for d in items]), 200
    except SQLAlchemyError as e:
        logger.error(f"DB error fetching datasets: {e}", exc_info=True)
        return jsonify({"error": "Database error fetching datasets."}), 500


def _checked_path(path):
    """(resolved path, None) for a dataset path the trainer can train on, or
    (None, reason). The reason names files and lines, never their text."""
    from backend.services.training.scripts import dataset_formats

    if not isinstance(path, str) or not path.strip():
        return None, "Dataset path is required: a .jsonl or .json file, or a folder of them."
    report = dataset_formats.inspect(path, samples=0)
    if not report["trainable"]:
        return None, f"This dataset cannot be trained on: {report['reason']}"
    return report["path"], None


def _text_field(data, key, *, required=False):
    """(value, None) for an optional text field, or (None, reason)."""
    value = data.get(key)
    if value is None and not required:
        return None, None
    if not isinstance(value, str):
        return None, f"{key} must be text."
    return value, None


@training_bp.route("/", methods=["POST"])
def create_dataset():
    logger.info("API: POST /api/training_datasets/")
    if not db or not TrainingDataset:
        return jsonify({"error": "Server configuration error."}), 500
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request must be a JSON object"}), 400
    name, problem = _text_field(data, "name", required=True)
    if problem or not name.strip():
        return jsonify({"error": problem or "Dataset name is required."}), 400
    description, problem = _text_field(data, "description")
    if problem:
        return jsonify({"error": problem}), 400
    path, problem = _checked_path(data.get("path"))
    if problem:
        return jsonify({"error": problem}), 400
    try:
        new_ds = TrainingDataset(name=name.strip(), description=description, path=path)
        db.session.add(new_ds)
        db.session.commit()
        return jsonify(serialize(new_ds)), 201
    except IntegrityError as e:
        db.session.rollback()
        return jsonify({"error": "Dataset with this name already exists."}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error creating dataset: {e}", exc_info=True)
        return jsonify({"error": "Failed to create dataset."}), 500


@training_bp.route("/<int:ds_id>", methods=["PUT"])
def update_dataset(ds_id):
    logger.info(f"API: PUT /api/training_datasets/{ds_id}")
    if not db or not TrainingDataset:
        return jsonify({"error": "Server configuration error."}), 500
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request must be a JSON object"}), 400
    try:
        ds = db.session.get(TrainingDataset, ds_id)
        if not ds:
            return jsonify({"error": "Dataset not found"}), 404
        updated = []
        if "name" in data:
            name, problem = _text_field(data, "name", required=True)
            if problem or not name.strip():
                return jsonify({"error": problem or "Dataset name is required."}), 400
            if ds.name != name.strip():
                ds.name = name.strip()
                updated.append("name")
        if "description" in data:
            description, problem = _text_field(data, "description")
            if problem:
                return jsonify({"error": problem}), 400
            if ds.description != description:
                ds.description = description
                updated.append("description")
        # A path saved before these checks existed may be kept as it is while
        # the dataset is renamed; a changed path must be trainable.
        if "path" in data and data["path"] != ds.path:
            path, problem = _checked_path(data["path"])
            if problem:
                return jsonify({"error": problem}), 400
            if ds.path != path:
                ds.path = path
                updated.append("path")
        if not updated:
            return jsonify(serialize(ds)), 200
        db.session.commit()
        return jsonify(serialize(ds)), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Dataset with this name already exists."}), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error updating dataset {ds_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to update dataset."}), 500


@training_bp.route("/<int:ds_id>", methods=["GET"])
def get_dataset(ds_id):
    logger.info(f"API: GET /api/training_datasets/{ds_id}")
    if not db or not TrainingDataset:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        ds = db.session.get(TrainingDataset, ds_id)
        if not ds:
            return jsonify({"error": "Dataset not found"}), 404
        return jsonify(serialize(ds)), 200
    except SQLAlchemyError as e:
        logger.error(f"DB error retrieving dataset {ds_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch dataset."}), 500


@training_bp.route("/<int:ds_id>", methods=["DELETE"])
def delete_dataset(ds_id):
    logger.info(f"API: DELETE /api/training_datasets/{ds_id}")
    if not db or not TrainingDataset:
        return jsonify({"error": "Server configuration error."}), 500
    try:
        ds = db.session.get(TrainingDataset, ds_id)
        if not ds:
            return jsonify({"error": "Dataset not found"}), 404
        db.session.delete(ds)
        db.session.commit()
        return jsonify({"message": "Dataset deleted"}), 200
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error deleting dataset {ds_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to delete dataset."}), 500
