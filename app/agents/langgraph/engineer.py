import os
from openai import OpenAI
from app.utils.prompt_loader import load_prompt


def generate_specification(requirements: str) -> str:
    """
    Engenheiro que transforma requisitos validados em especificação técnica formal.
    
    Args:
        requirements: Requisitos validados pelo analista
        
    Returns:
        str: Especificação técnica formal
    """
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    
    system_prompt = load_prompt(
        template_name='agents/langgraph/engineer/sys_prompt_1.jinja2'
    )
    
    human_prompt = load_prompt(
        template_name='agents/langgraph/engineer/hum_prompt_1.jinja2',
        requirements=requirements
    )
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt}
        ],
        temperature=0,
        max_tokens=1500
    )
    
    content = response.choices[0].message.content
    
    # Remove o token de terminação se presente
    if "TERMINATE_SPEC" in content:
        content = content.replace("TERMINATE_SPEC", "").strip()
    
    return content
