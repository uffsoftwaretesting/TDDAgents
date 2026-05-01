from pathlib import Path

def test_package_imports():
    """
    All core packages should exist and be importable.
    """
    modules = [
        'src',
        'src.domain',
        'src.application',
        'src.infrastructure',
        'src.presentation',
        'src.config',
    ]
    for module in modules:
        __import__(module)

def test_env_example_exists():
    """
    The .env.example file must exist at the project root.
    """
    assert Path('.env.example').is_file(), ".env.example file should exist in the project root"