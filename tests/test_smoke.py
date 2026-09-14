def test_imports_resolve():
    import technical_engine
    import microstructure_engine

    assert hasattr(technical_engine, "MathEngine")
    assert hasattr(microstructure_engine, "MicrostructureEngine")
