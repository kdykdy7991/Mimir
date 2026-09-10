"""Docker smoke test for the DocReader container (3rd-review blocking item 3).

Proves the *image* (not just the dev venv) can actually parse a real PDF over
gRPC: build → start container → health/readiness passes → ReadStream a native
PDF → non-empty Markdown (and a scanned-page PDF → a non-empty image frame).

Requires a working docker + a locally buildable image (pip reaches PyPI via the
host proxy normally; set DOCKER_BUILD_NETWORK=host if the build needs to reach
the host's 127.0.0.1 proxy). Skips (not fails) when docker or the base image
cannot be built offline.

Usage::

    python scripts/smoke_docreader_container.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import grpc

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "services" / "docreader"))
sys.path.insert(0, str(_ROOT))

IMAGE = "skdy-docreader:smoke"
CTR = "skdy-docreader-smoke"
PORT = 50059
_NATIVE_MD = "container parses this native pdf page"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _build_pdf(pymupdf, text: str, scanned: bool = False) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    if not scanned:
        page.insert_text((50, 100), text)
    else:
        # A genuinely scanned page is image-dominated (no real text layer):
        # cover the page with a full-page pixmap so image-area ratio >= scanner
        # threshold (blank pages classify as "text" and emit no image).
        pix = _full_page_pixmap(pymupdf, 400, 600)
        page.insert_image(pymupdf.Rect(0, 0, 400, 600), pixmap=pix)
    doc_bytes = doc.tobytes()
    doc.close()
    return doc_bytes


def _full_page_pixmap(pymupdf, width: int, height: int):
    """Build a full-page light-grey raster (scanned-page stand-in)."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    pix.clear_with(200)  # uniform non-trivial raster covering the whole page
    return pix


def _listening() -> bool:
    try:
        import socket
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            return True
    except OSError:
        return False


def _read(pdf_bytes: bytes) -> str | None:
    """Return the markdown_content from the running container's ReadStream."""
    from docreader.proto import docreader_pb2, docreader_pb2_grpc
    with grpc.insecure_channel(f"127.0.0.1:{PORT}") as ch:
        stub = docreader_pb2_grpc.DocReaderStub(ch)
        frames = list(stub.ReadStream(
            docreader_pb2.ReadRequest(
                file_content=pdf_bytes,
                file_name="a.pdf",
                file_type="pdf",
            ),
            timeout=30,
        ))
    if not frames or not frames[0].HasField("meta"):
        return None
    return frames[0].meta.markdown_content


def main() -> int:
    if shutil.which("docker") is None:
        print("[smoke] docker not available; SKIP", file=sys.stderr)
        return 0
    import pymupdf  # used to generate the real PDF bytes

    # 1. build image
    build_net = ["--network=host"] if __import__("os").environ.get("DOCKER_BUILD_NETWORK") == "host" else []
    r = _run(["docker", "build", *build_net, "-t", IMAGE, str(_ROOT / "services" / "docreader")])
    if r.returncode != 0:
        print("[smoke] image build FAILED — cannot verify container PDF path\n" + r.stderr[-2000:], file=sys.stderr)
        return 1

    # 2. run container
    print(f"[smoke] starting {CTR} on :{PORT} ...")
    _run(["docker", "rm", "-f", CTR])  # idempotent
    r = _run(["docker", "run", "-d", "--name", CTR, "--rm",
              "-p", f"{PORT}:50051", IMAGE])
    if r.returncode != 0:
        print("[smoke] container start FAILED\n" + r.stderr[-1000:], file=sys.stderr)
        return 1

    # 3. wait for health/readiness (docker healthcheck gate)
    try:
        t0 = time.time()
        healthy = False
        while time.time() - t0 < 120:
            if not _listening():
                time.sleep(3)
                continue
            status = _run(["docker", "inspect", "--format={{.State.Health.Status}}", CTR]).stdout.strip()
            if status == "healthy":
                healthy = True
                break
            time.sleep(3)
        if not healthy:
            logs = _run(["docker", "logs", CTR]).stdout[-1500:]
            print(f"[smoke] container never became healthy\nlogs:\n{logs}", file=sys.stderr)
            return 1
        print("[smoke] container HEALTHY (readiness probe passed)")

        # 4. gRPC parse a real native-text PDF
        md = _read(pdf_bytes=_build_pdf(pymupdf, _NATIVE_MD))
        if not md or _NATIVE_MD not in (md or ""):
            logs = _run(["docker", "logs", CTR]).stdout[-800:]
            print(f"[smoke] native PDF returned empty/unexpected markdown\nmd={md!r}\nlogs={logs}", file=sys.stderr)
            return 1
        print(f"[smoke] native PDF -> non-empty Markdown OK ({len(md)} chars)")

        # scanned-page PDF -> must emit a non-empty image frame
        scanned = _build_pdf(pymupdf, "", scanned=True)
        # read via the streaming path and count image frames
        from docreader.proto import docreader_pb2, docreader_pb2_grpc
        with grpc.insecure_channel(f"127.0.0.1:{PORT}") as ch:
            stub = docreader_pb2_grpc.DocReaderStub(ch)
            frames = list(stub.ReadStream(
                docreader_pb2.ReadRequest(
                    file_content=scanned,
                    file_name="s.pdf",
                    file_type="pdf",
                ),
                timeout=30,
            ))
        images = [f for f in frames if f.HasField("image") and f.image.image_data]
        if not images:
            print("[smoke] scanned PDF produced no image frame", file=sys.stderr)
            return 1
        img_len = len(images[0].image.image_data)
        print(f"[smoke] scanned PDF -> {len(images)} image frame(s), first {img_len} bytes OK")
        return 0
    finally:
        _run(["docker", "rm", "-f", CTR])


if __name__ == "__main__":
    sys.exit(main())