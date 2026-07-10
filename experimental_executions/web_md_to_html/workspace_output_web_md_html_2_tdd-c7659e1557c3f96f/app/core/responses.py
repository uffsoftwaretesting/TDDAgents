
"""
Standard response constructors for the Markdown to HTML Conversion service.
"""

def success_response(data: dict) -> dict:
    """
    Return a standardized success response.

    Args:
        data (dict): The data payload of the response.

    Returns:
        dict: A dict with 'data' set to the payload and 'error' set to None.
    """
    return {"data": data, "error": None}


def error_response(error_message: str) -> dict:
    """
    Return a standardized error response.

    Args:
        error_message (str): The error message.

    Returns:
        dict: A dict with 'data' set to None and 'error' set to the message.
    """
    return {"data": None, "error": error_message}