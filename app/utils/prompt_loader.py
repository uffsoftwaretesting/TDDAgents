from app.config.config import Config
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader(Config.PROMPTS_DIR))

def load_prompt(template_name: str, **kwargs) -> str:
    """
    Loads a template and injects the variables.
    E.g.: load_prompt('tdd/test_generation.jinja2', function_name='add', is_test_review=True)
    """
    template = env.get_template(template_name)
    return template.render(**kwargs)
