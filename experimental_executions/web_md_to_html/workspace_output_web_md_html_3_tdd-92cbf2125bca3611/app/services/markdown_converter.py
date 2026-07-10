import markdown2


class MarkdownConversionError(Exception):
    """Custom exception for markdown conversion failures."""
    pass


def convert_markdown_to_html(text: str) -> str:
    """
    Convert a markdown string to HTML using markdown2.

    :param text: Markdown text to convert
    :return: HTML string
    :raises TypeError: if text is not a string
    :raises MarkdownConversionError: if conversion fails
    """
    if not isinstance(text, str):
        raise TypeError("Input must be a string")

    try:
        html = markdown2.markdown(text)
        return html
    except Exception as e:
        raise MarkdownConversionError(f"Error converting markdown: {e}") from e
