import paths


def test_data_dir_is_inside_trading_copilot():
    assert paths.DATA_DIR.parent.name == "trading_copilot"
    assert paths.DATA_DIR.is_dir(), f"{paths.DATA_DIR} does not exist"


def test_known_data_files_resolve():
    assert paths.MACRO_BASELINES_PATH.is_file()
    assert list(paths.DATA_DIR.glob("*_1D.parquet")), "no parquet files found"


def test_parquet_path_helper():
    assert paths.parquet_path("RELIANCE").name == "RELIANCE_1D.parquet"
    assert paths.parquet_path("RELIANCE").is_file()


def test_ensure_dirs_is_idempotent():
    paths.ensure_dirs()
    paths.ensure_dirs()
    for d in (paths.DATA_DIR, paths.SIGNALS_DIR, paths.TICKS_DIR, paths.FEATURES_DIR):
        assert d.is_dir()
