from flask import Blueprint, request, jsonify, current_app
from backend.services.orchestrator_service import get_orchestrator
from datetime import datetime
import logging

orchestrator_bp = Blueprint('orchestrator', __name__, url_prefix='/api/orchestrator')
logger = logging.getLogger(__name__)


def _save_orchestrator_plan_message(
    session_id: str,
    project_id,
    plan_id: str,
    serialized_plan: dict,
) -> None:
    """Persist an assistant message embedding the orchestrator plan for chat history."""
    if not session_id:
        return
    try:
        from backend.models import db, LLMMessage
        from backend.utils.db_utils import safe_db_commit, safe_db_rollback

        message = LLMMessage(
            session_id=session_id,
            project_id=project_id,
            role="assistant",
            content="Orchestration plan",
            extra_data={
                "messageType": "orchestrator_plan",
                "orchestratorPlan": serialized_plan,
                "orchestratorPlanId": plan_id,
            },
            timestamp=datetime.now(),
        )
        db.session.add(message)
        if not safe_db_commit(f"orchestrator_plan_message_{session_id}"):
            safe_db_rollback(f"orchestrator_plan_message_{session_id}")
            logger.error("Failed to persist orchestrator plan message for session %s", session_id)
    except Exception as e:
        logger.error("Error saving orchestrator plan message: %s", e, exc_info=True)

@orchestrator_bp.route('/plan', methods=['POST'])
def create_plan():
    """
    Create a new orchestration plan from a user request.
    """
    try:
        import uuid
        data = request.get_json()
        if not data or 'request' not in data:
            return jsonify({'error': 'Missing "request" field'}), 400
            
        user_request = data['request']
        context = data.get('context', {})
        
        orchestrator = get_orchestrator()
        plan = orchestrator._create_plan(user_request)

        if not plan.subtasks:
            return jsonify({
                'success': False,
                'error': 'Failed to generate a valid plan',
            }), 422

        # Store plan in memory and save to disk
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        orchestrator._active_plans[plan_id] = plan
        
        # Map session ID to plan ID for persistence across page refreshes
        session_id = context.get('sessionId')
        if session_id:
            orchestrator._session_to_plan_id[session_id] = plan_id
            
        orchestrator._save_plans()

        serialized = orchestrator._serialize_plan(plan)
        _save_orchestrator_plan_message(
            session_id=session_id,
            project_id=context.get('projectId'),
            plan_id=plan_id,
            serialized_plan=serialized,
        )
        
        return jsonify({
            'success': True,
            'plan_id': plan_id,
            'plan': serialized,
        })
        
    except Exception as e:
        logger.error(f"Error creating plan: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@orchestrator_bp.route('/execute', methods=['POST'])
def execute_plan():
    """
    Execute an existing plan.
    """
    try:
        data = request.get_json()
        if not data or 'plan_id' not in data:
            return jsonify({'error': 'Missing "plan_id" field'}), 400
            
        plan_id = data['plan_id']
        context = data.get('context', {})
        
        orchestrator = get_orchestrator()
        plan = orchestrator._active_plans.get(plan_id)
        
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404

        if not plan.subtasks:
            return jsonify({
                'success': False,
                'error': 'Plan has no steps',
            }), 422
            
        # Execute in background? For now, sync for simplicity, but ideally async
        # We can use Celery here later.
        
        result = orchestrator._execute_plan(plan, context)
        
        return jsonify({
            'success': True,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error executing plan: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@orchestrator_bp.route('/status/<plan_id>', methods=['GET'])
def get_plan_status(plan_id):
    """
    Get the status of a plan.
    """
    try:
        orchestrator = get_orchestrator()
        plan = orchestrator._active_plans.get(plan_id)
        
        if not plan:
            return jsonify({'error': 'Plan not found'}), 404
            
        return jsonify({
            'success': True,
            'plan': orchestrator._serialize_plan(plan)
        })
        
    except Exception as e:
        logger.error(f"Error getting plan status: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
