import os


def test_directory_structure() -> None:
    expected_dirs = [
        os.path.join("src", "domain"),
        os.path.join("src", "application"),
        os.path.join("src", "infrastructure"),
        os.path.join("src", "interfaces"),
        "tests"
    ]
    for d in expected_dirs:
        assert os.path.isdir(d), f"Diretório esperado não encontrado: {d}"
