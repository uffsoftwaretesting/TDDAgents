def test_generate_mesh_exists():
    """
    Verifica se _generate_mesh existe e é chamável.
    """
    from diferencas_finitas_bvp.assembly import _generate_mesh
    assert callable(_generate_mesh)
