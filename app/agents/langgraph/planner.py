from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from typing import List, Dict, Any
from app.config import Config
import json
import logging

from app.utils.prompt_loader import load_prompt

def generate_plan(specification: str) -> List[str]:
    """Gera um plano de TDD (lista de sub-requisitos) a partir da especificação."""
    llm = ChatOpenAI(model=Config.MODEL, temperature=0.1)
    
    rendered_sys_message = load_prompt(
        template_name='agents/langgraph/planner/sys_prompt_1.jinja2',
    )
    
    rendered_hum_message = load_prompt(
        template_name='agents/langgraph/planner/hum_prompt_1.jinja2',
        specification=specification
    )
    
    messages = [
        SystemMessage(content=rendered_sys_message),
        HumanMessage(content=rendered_hum_message)
    ]

    response = llm.invoke(messages)
    content = str(response.content).strip()
    
    try:
        # Tenta corrigir a resposta se o LLM incluiu markdown
        if content.startswith('```json'):
            content = content.strip('```json').strip()
        elif content.startswith('```'):
            content = content.strip('```').strip()
            
        data = json.loads(content)
        
        # FIX: Usar a chave correta 'tdd_plan' conforme especificado no prompt
        tdd_plan = data.get('tdd_plan', [])
        
        # Extrair apenas os 'sub_requirement' de cada etapa
        sub_requirements = [step['sub_requirement'] for step in tdd_plan if 'sub_requirement' in step]
        
        return sub_requirements
        
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logging.error(f"❌ Erro ao decodificar JSON do Planner: {e}")
        logging.error(f"Conteúdo do LLM: {content}")
        # Retorna um plano de falha se houver erro
        return ["Falha ao gerar o plano, escreva um teste que valide a falha de implementação."]
