import importlib

def test_modules_exist():
    """
    Verifica se todos os módulos do pacote podem ser importados sem erro.
    """
    modules = [
        "diferencas_finitas_bvp",
        "diferencas_finitas_bvp.validation",
        "diferencas_finitas_bvp.assembly",
        "diferencas_finitas_bvp.solver",
        "diferencas_finitas_bvp.interpolation",
        "diferencas_finitas_bvp.core"
    ]
    for module_name in modules:
        module = importlib.import_module(module_name)
        assert module is not None, f"Falha ao importar {module_name}"