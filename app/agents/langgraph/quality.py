from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.config import Config

# Se você estiver usando jinja, crie os templates. 
# Aqui farei direto para facilitar a visualização.

SYSTEM_PROMPT_QUALITY = """
Você é um Auditor de Qualidade de Código (QA Lead).
Sua responsabilidade é avaliar o código final produzido por um fluxo TDD.

Analise os seguintes aspectos:
1. **Legibilidade**: Nomes de variáveis, funções e clareza.
2. **Boas Práticas**: Padrões Python (PEP8), docstrings, type hinting.
3. **Complexidade**: Identifique trechos desnecessariamente complexos.
4. **Segurança**: Vulnerabilidades óbvias (ex: eval, inputs sem tratamento).

Saída esperada: Um relatório Markdown conciso. Dê uma nota de 0 a 10.
"""

def evaluate_code_quality(implementation_code: str, specification: str) -> str:
    """Gera um relatório de qualidade sobre o código final."""
    
    if not implementation_code:
        return "Nenhum código foi gerado para avaliação."

    llm = ChatOpenAI(model=Config.MODEL, temperature=0.1)

    human_msg = f"""
    --- ESPECIFICAÇÃO ORIGINAL ---
    {specification}

    --- CÓDIGO IMPLEMENTADO ---
    {implementation_code}
    
    Gere o relatório de qualidade final.
    """

    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT_QUALITY),
        HumanMessage(content=human_msg)
    ])

    return str(response.content).strip()
