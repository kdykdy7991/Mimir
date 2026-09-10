"""Phase 6/7 gray-run rehearsal as a committed integration test.

Mirrors the manual local gray run: a real server (the live registry, all
registered engines) serving ReadStream / ListEngines over the wire via the
generated stubs. Parses markdown, DOCX and PDF end-to-end as the "real
business-document gray run" that is the Phase 7 prerequisite (plan §Phase-7),
verifying the new docreader backend reaches SUCCESS for the legacy + new formats.
"""

from __future__ import annotations

from concurrent import futures
import io
import struct
import zipfile

import grpc

from docreader.main import DocReaderServicer
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2, docreader_pb2_grpc


def _start() -> tuple[grpc.Server, str]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    # Real registry (all registered engines incl. Phase 6 formats).
    docreader_pb2_grpc.add_DocReaderServicer_to_server(DocReaderServicer(parser=Parser()), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def _docx_bytes() -> bytes:
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = "<w:p><w:r><w:t>Gray run docx heading</w:t></w:r></w:p>"
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W[1:-1]}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml.encode())
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    import pymupdf  # available in toolchain; produces a valid PDF with real text
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 72), "Gray run pdf text")
    return d.tobytes()


def test_gray_run_multi_format_over_wire() -> None:
    server, port = _start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = docreader_pb2_grpc.DocReaderStub(channel)
            engines = stub.ListEngines(docreader_pb2.ListEnginesRequest(), timeout=10)
            names = {e.name for e in engines.engines}
            assert "builtin" in names
            for (fn, content, ftype, marker) in (
                ("g.md", b"# Gray md\nbody", "md", "# Gray md"),
                ("g.docx", _docx_bytes(), "docx", "Gray run docx heading"),
                ("g.pdf", _pdf_bytes(), "pdf", "Gray run pdf text"),
            ):
                frames = list(stub.ReadStream(
                    docreader_pb2.ReadRequest(file_content=content, file_name=fn, file_type=ftype),
                    timeout=20,
                ))
                assert frames and frames[0].meta.error == ""
                text = frames[0].meta.markdown_content
                if marker is not None:
                    assert marker in text
    finally:
        server.stop(0)