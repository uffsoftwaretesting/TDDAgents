from typing import Literal
from langgraph.graph import StateGraph, END, START

from app.config.config import AgentState
from app.graph.nodes import (
    node_execute_tester,
    node_execute_runner_red,
    node_execute_developer,
    node_execute_runner_green,
)

def _increment_failures(state: AgentState, result: dict, fault_type: str = None) -> dict:
    """Helper function to increment failures, categorizing them if needed."""
    result["total_detected_failures"] = state.get("total_detected_failures", 0) + 1
    result["current_subreq_failures"] = state.get("current_subreq_failures", 0) + 1
    
    if fault_type:
        result[fault_type] = state.get(fault_type, 0) + 1
        
    return result

def wrapper_runner_red(state: AgentState) -> dict:
    result = node_execute_runner_red(state)
    status = result.get("status")
    if status in ("test_review_needed", "max_retries_exceeded"):
        result = _increment_failures(state, result, fault_type="test_faults")
    
    if status in ("max_retries_exceeded", "sandbox_failed", "tester_failed"):
        plan_index = state.get("plan_index", 0)
        flow_types = list(state.get("is_flow_type", []))
        while len(flow_types) <= plan_index:
            flow_types.append("")
        flow_types[plan_index] = ""
        result["is_flow_type"] = flow_types
        
    return result
    

def wrapper_runner_green(state: AgentState) -> dict:
    result = node_execute_runner_green(state)
    status = result.get("status")
    is_type_fault = result.get("is_type_fault", "")
        
    if status == "green_passed":
        failures_this_req = state.get("current_subreq_failures", 0)
        if failures_this_req > 0:
            result["autonomously_corrected_failures"] = state.get("autonomously_corrected_failures", 0) + failures_this_req
        
        result["current_subreq_failures"] = 0
        
    else:
        if status == "test_review_needed":
            result = _increment_failures(state, result, fault_type="test_faults")

        elif status == "green_failed":
            result = _increment_failures(state, result, fault_type="implementation_faults")
        
        elif status == "max_retries_exceeded":
            if is_type_fault == "test_faults":
                result = _increment_failures(state, result, fault_type="test_faults")
            elif is_type_fault == "implementation_faults":
                result = _increment_failures(state, result, fault_type="implementation_faults")
                
    if status in ("max_retries_exceeded", "sandbox_failed", "tester_failed", "developer_failed"):
        plan_index = state.get("plan_index", 0)
        flow_types = list(state.get("is_flow_type", []))
        while len(flow_types) <= plan_index:
            flow_types.append("")
        flow_types[plan_index] = ""
        result["is_flow_type"] = flow_types
        
    return result

def wrapper_tester(state: AgentState) -> dict:
    result = node_execute_tester(state)
    status = result.get("status")
    if status in ("tester_failed", "sandbox_failed", "max_retries_exceeded"):
        plan_index = state.get("plan_index", 0)
        flow_types = list(state.get("is_flow_type", []))
        while len(flow_types) <= plan_index:
            flow_types.append("")
        flow_types[plan_index] = ""
        result["is_flow_type"] = flow_types
    return result

def wrapper_developer(state: AgentState) -> dict:
    result = node_execute_developer(state)
    status = result.get("status")
    if status in ("developer_failed", "sandbox_failed", "max_retries_exceeded"):
        plan_index = state.get("plan_index", 0)
        flow_types = list(state.get("is_flow_type", []))
        while len(flow_types) <= plan_index:
            flow_types.append("")
        flow_types[plan_index] = ""
        result["is_flow_type"] = flow_types
    return result

def build_tdd_subgraph():
    """
    Builds the internal TDD cycle:  Tester → RunnerRed → Developer → RunnerGreen
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("tester", wrapper_tester)
    workflow.add_node("runner_red", wrapper_runner_red)
    workflow.add_node("developer", wrapper_developer)
    workflow.add_node("runner_green", wrapper_runner_green)

    workflow.add_edge(START, "tester")

    def route_after_tester(state: AgentState) -> Literal["tester", "runner_red", END]:
        status = state.get("status")
        if status == "infra_error_tester": return "tester"
        if status in ("tester_failed", "sandbox_failed"): return END
        return "runner_red"
    workflow.add_conditional_edges("tester", route_after_tester)

    def route_after_red(state: AgentState) -> Literal["developer", "tester", "runner_red", END]:
        status = state.get("status")
        if status == "infra_error_red": return "runner_red"
        if status == "red_confirmed": return "developer"
        if status == "test_review_needed": return "tester"
        if status in ("tester_failed", "max_retries_exceeded", "sandbox_failed"): return END
        return "tester"
    workflow.add_conditional_edges("runner_red", route_after_red)
    
    def route_after_developer(state: AgentState) -> Literal["developer", "runner_green", END]:
        status = state.get("status")
        if status == "infra_error_developer": return "developer"
        if status in ("developer_failed", "sandbox_failed"): return END
        return "runner_green"
    workflow.add_conditional_edges("developer", route_after_developer)

    def route_after_green(state: AgentState) -> Literal[END, "developer", "tester", "runner_green"]:
        status = state.get("status")
        if status == "infra_error_green": return "runner_green"
        if status == "green_passed": return END
        if status == "test_review_needed": return "tester"
        if status in ("max_retries_exceeded", "tester_failed", "developer_failed", "sandbox_failed"): return END
        return "developer"
    workflow.add_conditional_edges("runner_green", route_after_green)

    return workflow.compile()