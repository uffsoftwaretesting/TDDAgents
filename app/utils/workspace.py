import logging
import os
import shutil
from app.config.config import Config

import os
import logging
from app.config.config import Config

class WorkspaceService:
    @staticmethod
    def write_file(filename: str, content: str):
        path = os.path.join(Config.WORKSPACE_PATH, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logging.info(f"💾 Arquivo salvo: {filename}")

    @staticmethod
    def restore_files(tests_code: str, impl_code: str):
        if tests_code:
            WorkspaceService.write_file(Config.TEST_FILE, tests_code)
        if impl_code:
            WorkspaceService.write_file(f"{Config.IMPLEMENTATION_MODULE}.py", impl_code)
        logging.info("📂 Workspace restaurado do estado.")
    
    @staticmethod
    def setup_workspace(clean: bool = True):
        """Configura o diretório de trabalho."""
        if clean and os.path.exists(Config.WORKSPACE_PATH):
            shutil.rmtree(Config.WORKSPACE_PATH)
        
        os.makedirs(Config.WORKSPACE_PATH, exist_ok=True)
        
        impl_path = os.path.join(Config.WORKSPACE_PATH, f"{Config.IMPLEMENTATION_MODULE}.py")
        if not os.path.exists(impl_path) or clean:
            with open(impl_path, "w", encoding="utf-8") as f:
                f.write("# Implementação incremental via TDD\n")
        
        test_path = os.path.join(Config.WORKSPACE_PATH, Config.TEST_FILE)
        if not os.path.exists(test_path) or clean:
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("import pytest\n")
        
        logging.info(f"✅ Workspace '{Config.WORKSPACE_PATH}' configurado.")
    


