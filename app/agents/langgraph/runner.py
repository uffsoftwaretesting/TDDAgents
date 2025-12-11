import subprocess
from app.config import Config

def run_pytest() -> str:
    """Executa pytest no arquivo de testes."""
    test_file = Config.TEST_FILE
    
    try:
        result = subprocess.run(
            ["pytest", f"{Config.WORKSPACE_PATH}/{test_file}", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Combina stdout e stderr para análise completa
        output = result.stdout
        if result.stderr:
            output += f"\n\nERROS:\n{result.stderr}"
        
        return output.strip()
    except subprocess.TimeoutExpired:
        return "❌ Erro: execução de testes expirou (timeout de 30s)."
    except FileNotFoundError:
        return "❌ Erro: pytest não está instalado. Execute: pip install pytest"
    except Exception as e:
        return f"❌ Erro ao executar testes: {str(e)}"
