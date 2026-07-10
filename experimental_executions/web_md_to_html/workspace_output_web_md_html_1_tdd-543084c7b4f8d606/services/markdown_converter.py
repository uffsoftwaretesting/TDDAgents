import markdown


class MarkdownConversionError(Exception):
    """Custom exception for markdown conversion failures."""
    pass


def convert(markdown_text: str) -> str:
    """
    Convert a Markdown string to HTML.

    Raises:
        MarkdownConversionError: If the underlying markdown library fails.
    """
    try:
        return markdown.markdown(markdown_text)
    except Exception as e:
        raise MarkdownConversionError("Internal conversion error") from e
