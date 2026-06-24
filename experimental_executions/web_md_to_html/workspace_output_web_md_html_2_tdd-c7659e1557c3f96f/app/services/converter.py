from markdown import markdown
from app.core.errors import ConversionError

def convert_markdown_to_html(markdown_text: str) -> str:
    """
    Convert Markdown text to HTML.
    Returns an empty string if input is empty.
    Raises ConversionError if the conversion fails internally.
    """
    try:
        return markdown(markdown_text)
    except Exception as e:
        # Wrap any internal exception into a ConversionError
        raise ConversionError(str(e)) from e
