def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: tests that take >30 s (organics, CREST runs, multi-step pipelines)",
    )
