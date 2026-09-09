"""Contract test for the generated gRPC stubs (plan §12 monkey/§6 契约迁移).

Verifies the messages and service surface we depend on (ReadStream /
ListEngines) are present in the regenerated Python stubs, independent of any
running service.
"""

from __future__ import annotations

from docreader.proto import docreader_pb2, docreader_pb2_grpc


def test_read_request_fields() -> None:
    req = docreader_pb2.ReadRequest(
        file_content=b"%PDF",
        file_name="a.pdf",
        file_type="pdf",
        request_id="req-1",
        title="t",
        url="",
    )
    assert req.file_name == "a.pdf"
    assert req.request_id == "req-1"
    # ReadConfig with parser_engine_overrides
    cfg = docreader_pb2.ReadConfig(parser_engine="builtin", parser_engine_overrides={"dpi": "200"})
    assert cfg.parser_engine == "builtin"
    assert cfg.parser_engine_overrides["dpi"] == "200"


def test_read_stream_response_oneof() -> None:
    # meta variant
    meta = docreader_pb2.ReadStreamResponse(
        meta=docreader_pb2.ReadStreamMeta(
            markdown_content="# doc",
            image_count=1,
            metadata={"engine": "builtin"},
        ),
    )
    assert meta.HasField("meta")
    assert meta.meta.markdown_content == "# doc"
    # image variant
    frame = docreader_pb2.ReadStreamResponse(
        image=docreader_pb2.ImageRef(
            filename="p.png", mime_type="image/png", image_data=b"\x89PNG",
        ),
    )
    assert frame.HasField("image")
    assert frame.image.image_data == b"\x89PNG"


def test_servicer_surface() -> None:
    servicer = docreader_pb2_grpc.DocReaderServicer()
    assert callable(getattr(servicer, "ReadStream"))
    assert callable(getattr(servicer, "ListEngines"))
    assert callable(getattr(servicer, "Read"))