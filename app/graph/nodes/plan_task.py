import logging
import time
from langchain_core.messages import AIMessage

from app.config.config import AgentState, Config
from app.agents.langgraph.planner import generate_plan
from app.errors.agents.handler import handle_llm_exception
from app.errors.exceptions import FatalInfraError, TransientInfraError

logger = logging.getLogger("TDDOrchestrator")


def node_plan_task(state: AgentState) -> AgentState:
    logger.info("\n" + "=" * 80)
    logger.info("📋 PLANEJAMENTO (PLANNER)")
    logger.info("=" * 80)
    logger.info(f"📌 Especificação: {state['specification'][:100]}...")

    infra_retries = state.get("infra_retries", 0)

    try:
        plan = generate_plan(state["specification"])

    except TransientInfraError as exc:
        infra_retries += 1
        if infra_retries >= Config.MAX_INFRA_RETRIES:
            logger.error(f"❌ PLANNER: Falha de Infra (Limite Atingido): {exc.original_exc}")
            return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}
        
        logger.warning(f"⚠️ PLANNER: Erro Transiente. Tentativa {infra_retries}/{Config.MAX_INFRA_RETRIES}. Aguardando 3s... ({exc.original_exc})")
        time.sleep(3)
        return {**state, "status": "infra_error_planner", "infra_retries": infra_retries}

    except FatalInfraError as exc:
        logger.error(f"❌ PLANNER: Falha Fatal de Infraestrutura: {exc.original_exc}")
        return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}
    except Exception as exc:
        logger.error(f"❌ PLANNER: Falha Fatal de Infraestrutura")
        return {**state, "status": "plan_failed", "plan": [], "infra_retries": 0}

    if not plan:
        logger.error("❌ ERRO: O Planner falhou ao gerar o plano de tarefas.")
        return {
            **state,
            "status": "plan_failed",
            "plan": [],
            "infra_retries": 0,
            "audit_log": [AIMessage(content="[Planner] Falha ao gerar o plano de tarefas.")],
        }

    logger.info("-" * 80)
    logger.info(f"✅ PLANO GERADO COM {len(plan)} SUB-REQUISITOS:")
    for i, item in enumerate(plan, 1):
        logger.info(f"   {i}. {item}")
    logger.info("-" * 80)

    plan_summary = "\n".join(f"{i+1}. {item}" for i, item in enumerate(plan))
    audit_entry = AIMessage(
        content=f"[Planner] Plano gerado com {len(plan)} sub-requisitos:\n{plan_summary}"
    )

    return {
        **state,
        "plan": plan,
        "plan_index": 0,
        "current_sub_req": plan[0],
        "iteration": 1,
        "infra_retries": 0,
        "status": "planning_complete",
        # Os históricos dos agentes começam vazios, nenhuma memória deve ser
        # carregada para o primeiro sub-requisito. Já são [] no initial_state.
        "tester_messages": [],
        "developer_messages": [],
        "reviewer_messages": [],
        "audit_log": [audit_entry],
    }