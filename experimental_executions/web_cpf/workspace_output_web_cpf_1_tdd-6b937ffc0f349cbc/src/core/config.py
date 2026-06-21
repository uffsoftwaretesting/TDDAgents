import os


class Settings:
    """
    Application settings loaded from environment variables with sensible defaults.
    """
    def __init__(self):
        # Environment name, default 'development'
        self.ENV = os.getenv('APP_ENV', 'development')
        # Host for the API server, default '127.0.0.1'
        self.HOST = os.getenv('API_HOST', '127.0.0.1')
        # Port for the API server, default 8000, ensure integer
        port = os.getenv('API_PORT')
        if port is not None:
            try:
                self.PORT = int(port)
            except ValueError:
                # Fallback to default if invalid
                self.PORT = 8000
        else:
            self.PORT = 8000
