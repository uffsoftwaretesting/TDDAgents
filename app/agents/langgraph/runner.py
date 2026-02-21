import subprocess
from app.config import Config


def run_pytest() -> str:
    """
    Executa o pytest no arquivo de testes atual e retorna o output completo.

    stdout e stderr são mesclados para que os agentes downstream sempre vejam
    o quadro completo (erros de import, por exemplo, aparecem apenas no stderr).
    """
    try:
        result = subprocess.run(
            [
                "pytest",
                f"{Config.WORKSPACE_PATH}/{Config.TEST_FILE}",
                "-v",
                "--tb=short",
                # Códigos de cor quebram o matching de strings — mantém output simples.
                "--no-header",
                "-p", "no:warnings",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        output = result.stdout
        if result.stderr:
            output += f"\n\nSTDERR:\n{result.stderr}"

        return output.strip()

    except subprocess.TimeoutExpired:
        return "ERRO: execução dos testes expirou após 30 s."
    except FileNotFoundError:
        return "ERRO: pytest não encontrado — execute: pip install pytest"
    except Exception as e:
        return f"ERRO: não foi possível executar os testes: {e}"