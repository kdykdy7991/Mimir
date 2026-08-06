# Smoke import tests — verify key packages can be imported.


def test_src_top_level_imports():
    """Verify all top-level source packages are importable."""
    import src.core
    import src.ingestion
    import src.libs
    import src.mcp_server
    import src.observability
    assert True


def test_src_submodule_imports():
    """Verify key submodules under each package are importable."""
    # Core submodules
    import src.core.query_engine
    import src.core.response
    import src.core.trace

    # Ingestion submodules
    import src.ingestion.chunking
    import src.ingestion.embedding
    import src.ingestion.storage
    import src.ingestion.transform

    # Libs submodules
    import src.libs.embedding
    import src.libs.evaluator
    import src.libs.llm
    import src.libs.loader
    import src.libs.reranker
    import src.libs.splitter
    import src.libs.vector_store

    # MCP submodules
    import src.mcp_server.tools

    # Observability submodules
    import src.observability.dashboard
    import src.observability.dashboard.pages
    import src.observability.dashboard.services
    import src.observability.evaluation

    assert True


def test_tests_directories_exist():
    """Verify the test directory structure matches convention."""
    from pathlib import Path

    expected = [
        Path("tests/unit"),
        Path("tests/integration"),
        Path("tests/e2e"),
        Path("tests/fixtures/sample_documents"),
    ]
    for p in expected:
        assert p.exists(), f"Missing directory: {p}"

