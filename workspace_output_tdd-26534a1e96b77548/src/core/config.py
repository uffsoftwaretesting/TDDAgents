from dotenv import load_dotenv
from pathlib import Path

# Load .env from the current working directory explicitly
dotenv_path = Path.cwd() / ".env"
load_dotenv(dotenv_path)
