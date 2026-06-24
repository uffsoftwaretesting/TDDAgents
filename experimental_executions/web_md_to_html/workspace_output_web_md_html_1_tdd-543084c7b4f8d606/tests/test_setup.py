def test_main_importable():
    # Verifica se o módulo main.py existe e é importável
    import main
    assert main is not None
