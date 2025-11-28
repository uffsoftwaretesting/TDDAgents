import os
from openai import OpenAI
from app.utils.prompt_loader import load_prompt


def analyze_requirements(user_input: str, conversation_history: str = "") -> dict:
    """
    Analista de requisitos que faz perguntas para esclarecer requisitos vagos.
    
    Returns:
        dict: {
            'response': str,
            'needs_clarification': bool,
            'has_checklist': bool
        }
    """
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    
    system_prompt = load_prompt(
        template_name='agents/langgraph/analyst/sys_prompt_1.jinja2'
    )
    
    human_prompt = load_prompt(
        template_name='agents/langgraph/analyst/hum_prompt_1.jinja2',
        user_input=user_input,
        conversation_history=conversation_history
    )
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt}
        ],
        temperature=0,
        max_tokens=1000
    )
    
    content = str(response.choices[0].message.content)
    
    # Analisar a resposta para determinar o estado
    has_checklist = "===CHECKLIST_END===" in content
    has_checklist_phrase = "Posso prosseguir?" in content
    is_asking_questions = "?" in content and not has_checklist_phrase
    
    # Se tem o checklist mas não tem o token, adicionar
    if has_checklist_phrase and not has_checklist:
        content += "\n===CHECKLIST_END==="
        has_checklist = True
    
    needs_clarification = not has_checklist and is_asking_questions
    
    return {
        'response': content,
        'needs_clarification': needs_clarification,
        'has_checklist': has_checklist
    }
