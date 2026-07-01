import re
import sys

def main():
    path = "/home/llamax1/LLAMAX8/backend/services/unified_chat_engine.py"
    with open(path, "r") as f:
        content = f.read()

    # 1. Update chat() signature and calls
    content = content.replace(
        "is_voice_message: bool = False) -> Dict[str, Any]:",
        "is_voice_message: bool = False, resume_state_id: int = None) -> Dict[str, Any]:"
    )
    content = content.replace(
        "return self._run_chat(session_id, message, options, emit_fn, request_id, steps)",
        "return self._run_chat(session_id, message, options, emit_fn, request_id, steps, resume_state_id)"
    )

    # 2. Update _run_chat() signature
    content = content.replace(
        "emit_fn: Callable, request_id: str, steps: List) -> Dict[str, Any]:",
        "emit_fn: Callable, request_id: str, steps: List, resume_state_id: int = None) -> Dict[str, Any]:"
    )

    # 3. Add Resume State Extraction at the very beginning of _run_chat
    setup_start = content.find("        # 0. Direct media command intercept")
    if setup_start == -1:
        print("Failed to find _run_chat setup start")
        sys.exit(1)
        
    setup_end = content.find("        # 6. ReACT loop")
    if setup_end == -1:
        print("Failed to find _run_chat setup end")
        sys.exit(1)
        
    original_setup = content[setup_start:setup_end]
    indented_setup = "\\n".join("    " + line if line.strip() else line for line in original_setup.split("\\n"))
    
    resume_block = """        start_iteration = 1
        resumed_tool_jobs = None
        step_info = {}
        llm_response = ""
        
        if resume_state_id:
            from backend.models import SuspendedChatState, db as _db
            import json
            from backend.utils.agent_output_parser import ToolCall
            
            state = _db.session.get(SuspendedChatState, resume_state_id)
            if not state:
                return {"success": False, "error": "Invalid resume state"}
                
            start_iteration = state.iteration
            session_id = state.session_id
            request_id = state.request_id
            message = state.message
            options = json.loads(state.options) if state.options else {}
            history = json.loads(state.history)
            rag_context = state.rag_context
            ollama_messages = json.loads(state.ollama_messages)
            llm_response = state.llm_response
            step_info = json.loads(state.step_info) if state.step_info else {}
            
            raw_tool_jobs = json.loads(state.tool_jobs)
            resumed_tool_jobs = []
            for r in raw_tool_jobs:
                tc = ToolCall(**r["tc"])
                resumed_tool_jobs.append((tc, r["tool_name"], r["params"]))
                
            _db.session.delete(state)
            _db.session.commit()
            
            try:
                if "think" in options:
                    self._think = bool(options.get("think"))
                else:
                    from backend.models import Setting, db as _db
                    _s = _db.session.get(Setting, "chat_thinking_default")
                    self._think = bool(_s and (_s.value or "").strip().lower() in ("true", "1", "yes"))
            except Exception:
                self._think = bool(options.get("think", False))
                
        else:
"""
    new_setup = resume_block + indented_setup
    content = content[:setup_start] + new_setup + content[setup_end:]

    # 4. Modify the ReACT loop start and handle resumed_tool_jobs
    loop_sig = "for iteration in range(1, self.max_iterations + 1):"
    loop_replacement = """for iteration in range(start_iteration, self.max_iterations + 1):
            if resumed_tool_jobs is not None:
                tool_jobs = resumed_tool_jobs
                resumed_tool_jobs = None
                
                # Retrieve user's approval response
                with _approval_lock:
                    approved = _approval_responses.pop(session_id, False)
                    
                if not approved:
                    logger.warning(f"Session {session_id} tool approval REJECTED")
                    _pre = _preapproved_tool_names(session_id)
                    for tc, tool_name, params in tool_jobs:
                        tool = self.registry.get_tool(tool_name)
                        if tool and tool.requires_approval:
                            emit_fn("chat:tool_result", {
                                "tool": tool_name,
                                "result": {"success": False, "error": "USER REJECTED: This action was not approved by the user."},
                                "duration_ms": 0,
                            })
                            guard.record_result(tool_name, params, False, "USER REJECTED", iteration)
                            
                            if "tool_calls" not in step_info:
                                step_info["tool_calls"] = []
                            step_info["tool_calls"].append({
                                "tool_name": tool_name,
                                "params": params,
                                "success": False,
                                "duration_ms": 0,
                                "output_preview": "USER REJECTED",
                            })
                            
                    tool_jobs = [
                        (tc, tn, p) for tc, tn, p in tool_jobs
                        if (not (self.registry.get_tool(tn) and self.registry.get_tool(tn).requires_approval) or tn in _pre)
                    ]
                    
                    if not tool_jobs:
                        steps.append(step_info)
                        ollama_messages.append({"role": "assistant", "content": llm_response[:800]})
                        ollama_messages.append({
                            "role": "user",
                            "content": (
                                "Tool results:\\n[USER REJECTED: The user did not approve these actions. "
                                "Please explain why they were needed or suggest an alternative that doesn't "
                                "require these permissions.]"
                            )
                        })
                        continue # Next ReACT iteration
            else:"""
    content = content.replace(loop_sig, loop_replacement)

    # 5. Indent the LLM block inside the `else:`
    llm_block_start = content.find("            # One-shot wrap-up nudge")
    llm_block_end = content.find("            # --- 3+4. Execute and emit results ------------------------------------")
    if llm_block_start == -1 or llm_block_end == -1:
        print("Failed to find LLM block inside ReACT loop")
        sys.exit(1)
        
    original_llm_block = content[llm_block_start:llm_block_end]
    indented_llm_block = "\\n".join("    " + line if line.strip() else line for line in original_llm_block.split("\\n"))
    
    # 6. Replace event.wait(timeout=300) with Suspension
    wait_target = """                # Create and wait on event
                event = threading.Event()
                with _approval_lock:
                    _approval_events[session_id] = event
                    _approval_responses.pop(session_id, None)
                
                # Wait for up to 5 minutes for user response
                event.wait(timeout=300)
                
                with _approval_lock:
                    _approval_events.pop(session_id, None)
                    approved = _approval_responses.pop(session_id, False)
                
                if not approved:
                    logger.warning(f"Session {session_id} tool approval REJECTED or TIMED OUT")
                    # Synthetic rejection results for all approval-required tools
                    rejected_observations = []
                    for tc, tool_name, params in tool_jobs:
                        tool = self.registry.get_tool(tool_name)
                        if tool and tool.requires_approval:
                            emit_fn("chat:tool_result", {
                                "tool": tool_name,
                                "result": {"success": False, "error": "USER REJECTED: This action was not approved by the user."},
                                "duration_ms": 0,
                            })
                            # Record result with guard
                            guard.record_result(tool_name, params, False, "USER REJECTED", iteration)
                            
                            # Add to steps
                            step_info["tool_calls"].append({
                                "tool_name": tool_name,
                                "params": params,
                                "success": False,
                                "duration_ms": 0,
                                "output_preview": "USER REJECTED",
                            })
                    
                    # Remove rejected jobs from tool_jobs so they aren't executed
                    tool_jobs = [
                        (tc, tn, p) for tc, tn, p in tool_jobs
                        if (
                            not (self.registry.get_tool(tn) and self.registry.get_tool(tn).requires_approval)
                            or tn in _pre
                        )
                    ]
                    
                    if not tool_jobs:
                        # All tools in this iteration were rejected
                        steps.append(step_info)
                        ollama_messages.append({"role": "assistant", "content": llm_response[:800]})
                        ollama_messages.append({
                            "role": "user",
                            "content": (
                                "Tool results:\\n[USER REJECTED: The user did not approve these actions. "
                                "Please explain why they were needed or suggest an alternative that doesn't "
                                "require these permissions.]"
                            )
                        })
                        continue # Next ReACT iteration"""
    
    suspension_replacement = """                # Create Suspended state and abort worker!
                from backend.models import SuspendedChatState, db as _db
                import json
                state = SuspendedChatState(
                    session_id=session_id,
                    request_id=request_id,
                    iteration=iteration,
                    history=json.dumps(history),
                    ollama_messages=json.dumps(ollama_messages),
                    tool_jobs=json.dumps([{"tc": tc.model_dump(), "tool_name": tn, "params": p} for tc, tn, p in tool_jobs]),
                    message=message,
                    options=json.dumps(options),
                    rag_context=rag_context,
                    llm_response=llm_response,
                    step_info=json.dumps(step_info)
                )
                _db.session.add(state)
                _db.session.commit()
                
                logger.info(f"Worker Suspended for session {session_id} - waiting for HITL approval.")
                return {"status": "suspended", "session_id": session_id}"""
                
    indented_llm_block = indented_llm_block.replace(wait_target, suspension_replacement)
    
    content = content[:llm_block_start] + indented_llm_block + content[llm_block_end:]
    
    with open(path, "w") as f:
        f.write(content)

    print("Successfully patched unified_chat_engine.py")

if __name__ == "__main__":
    main()
