import os
import sys
import tempfile
import shutil
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.orchestrator_service import OrchestratorService, OrchestrationPlan, SubTask

def test_orchestrator_persistence():
    # Setup temporary directory for persistence
    temp_dir = tempfile.mkdtemp()
    
    # Mock SYSTEM_DIR
    import backend.config
    old_system_dir = backend.config.SYSTEM_DIR
    backend.config.SYSTEM_DIR = temp_dir
    
    try:
        # Create a service instance
        service = OrchestratorService()
        
        # Verify empty plans list initially
        assert len(service._active_plans) == 0
        assert len(service._session_to_plan_id) == 0
        
        # Create a test plan
        subtasks = [
            SubTask(id=1, description="step 1", assigned_agent="agent_1", status="completed", result="result 1"),
            SubTask(id=2, description="step 2", assigned_agent="agent_2", status="pending")
        ]
        plan = OrchestrationPlan(
            original_request="test request",
            subtasks=subtasks,
            current_step_index=1,
            status="executing",
            final_answer="final result"
        )
        
        plan_id = "test_plan_id"
        session_id = "test_session_id"
        
        # Add to service
        service._active_plans[plan_id] = plan
        service._session_to_plan_id[session_id] = plan_id
        
        # Save plans
        service._save_plans()
        
        # Verify file exists
        persist_file = os.path.join(temp_dir, "orchestrator_plans.json")
        assert os.path.exists(persist_file)
        
        # Create a new service instance to load from disk
        new_service = OrchestratorService()
        
        # Verify plans are hydrated correctly
        assert len(new_service._active_plans) == 1
        assert len(new_service._session_to_plan_id) == 1
        assert "test_plan_id" in new_service._active_plans
        assert new_service._session_to_plan_id["test_session_id"] == "test_plan_id"
        
        loaded_plan = new_service._active_plans["test_plan_id"]
        assert loaded_plan.original_request == "test request"
        assert loaded_plan.current_step_index == 1
        assert loaded_plan.status == "executing"
        assert loaded_plan.final_answer == "final result"
        assert len(loaded_plan.subtasks) == 2
        assert loaded_plan.subtasks[0].description == "step 1"
        assert loaded_plan.subtasks[0].status == "completed"
        assert loaded_plan.subtasks[0].result == "result 1"
        assert loaded_plan.subtasks[1].status == "pending"
        
    finally:
        # Restore and clean up
        backend.config.SYSTEM_DIR = old_system_dir
        shutil.rmtree(temp_dir)
