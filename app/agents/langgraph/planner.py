import json
import logging
import re
from typing import List

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import Config
from app.utils.prompt_loader import load_prompt

logger = logging.getLogger("TDDOrchestrator")


def _extract_json(content: str) -> str:
    """
    Remove delimitadores de markdown e extrai o primeiro objeto JSON da resposta do LLM.

    A abordagem original usava str.strip('```json'), que é um strip de caracteres,
    não de substring — removeria acidentalmente os caracteres 'j', 's', 'o', 'n'
    do início e fim do próprio JSON. Esta abordagem com regex é exata.
    """
    # Remove delimitadores ```json ... ``` ou ``` ... ```
    fenced = re.sub(r"```(?:json)?\s*", "", content).replace("```", "").strip()

    # Se após o strip temos JSON válido, usa diretamente
    if fenced.startswith("{") or fenced.startswith("["):
        return fenced

    # Último recurso: encontra o primeiro bloco {...} no conteúdo bruto
    match = re.search(r"\{.*\}", content, re.DOTALL)
    return match.group(0) if match else fenced


def generate_plan(specification: str) -> List[str]:
    """
    Gera um plano TDD (lista ordenada de sub-requisitos) a partir da especificação.

    O planner é intencionalmente sem estado — é chamado uma vez por execução e
    não precisa de histórico de conversa. Nenhuma alteração na arquitetura de
    mensagens por agente é necessária aqui.
    """
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.1)

    rendered_sys = load_prompt(
        template_name='agents/langgraph/planner/sys_prompt_1.jinja2',
    )
    rendered_hum = load_prompt(
        template_name='agents/langgraph/planner/hum_prompt_1.jinja2',
        specification=specification,
    )

    response = llm.invoke([
        SystemMessage(content=rendered_sys),
        HumanMessage(content=rendered_hum),
    ])
    content = str(response.content).strip()

    try:
        clean = _extract_json(content)
        data = json.loads(clean)

        tdd_plan = data.get("tdd_plan", [])
        if not tdd_plan:
            raise ValueError("A chave 'tdd_plan' está ausente ou vazia na resposta do LLM.")

        sub_requirements = [
            step["sub_requirement"]
            for step in tdd_plan
            if "sub_requirement" in step
        ]

        if not sub_requirements:
            raise ValueError("Nenhuma entrada 'sub_requirement' encontrada no tdd_plan.")

        return sub_requirements

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.error(f"❌ Planner falhou ao decodificar a resposta do LLM: {e}")
        logger.error(f"   Conteúdo bruto: {content}")
        return []