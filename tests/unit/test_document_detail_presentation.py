"""Document detail parser-evidence projection tests."""

from src.ingestion.document_manager import DocumentInfo
from src.web_api.mappers import to_document_detail


def test_detail_projects_tables_and_parser_diagnostics_without_fake_preview() -> None:
    info = DocumentInfo(
        source_path="/docs/report.pdf",
        collection="reports",
        n_chunks=3,
        n_images=0,
        file_size=123,
        status="success",
    )
    chunks = [
        {
            "id": "second",
            "text": "| A | B |\n| --- | --- |\n| 1 | 2 |",
            "metadata": {
                "chunk_index": 1,
                "content_type": "table",
                "table_index": 0,
                "parser_engine": "docreader",
                "parse_status": "partial_success",
                "parse_warnings": ["page 2 OCR confidence is low"],
            },
        },
        {
            "id": "first",
            "text": "# Report",
            "metadata": {
                "chunk_index": 0,
                "parser_engine": "docreader",
                "parse_status": "partial_success",
                "parse_warnings": ["page 2 OCR confidence is low"],
            },
        },
        {
            "id": "table-part-two",
            "text": "| 3 | 4 |",
            "metadata": {
                "chunk_index": 2,
                "content_type": "table",
                "table_index": 0,
            },
        },
    ]

    detail = to_document_detail(info, chunks=chunks)

    assert detail.table_count == 1
    assert detail.parser_engine == "docreader"
    assert detail.parse_status == "partial_success"
    assert detail.parse_warnings == ["page 2 OCR confidence is low"]
