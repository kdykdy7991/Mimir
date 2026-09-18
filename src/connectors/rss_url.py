"""Incremental RSS/Atom and controlled single-URL connectors."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from defusedxml import ElementTree

from src.connectors.contracts import ConnectorItem, SourceDocument, SyncCheckpoint
from src.connectors.http_security import SafeHttpClient


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ControlledUrlConnector:
    def __init__(self, url: str, client: SafeHttpClient) -> None:
        self.url = url
        self.client = client
        self._documents: dict[str, SourceDocument] = {}
        self._next = SyncCheckpoint()

    def test(self) -> None:
        self.client.validator.validate(self.url)

    def list(self, checkpoint: SyncCheckpoint | None) -> Iterable[ConnectorItem]:
        state = _checkpoint_data(checkpoint)
        headers = _conditional_headers(state)
        response = self.client.get(self.url, headers=headers)
        if response.status_code == 304:
            self._next = checkpoint or SyncCheckpoint()
            return ()
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"controlled URL returned HTTP {response.status_code}")
        checksum = _sha(response.body)
        external_id = hashlib.sha256(self.url.encode()).hexdigest()
        revision = response.headers.get("etag") or response.headers.get("last-modified") or checksum
        document = SourceDocument(
            external_id=external_id, revision=revision, title=self.url,
            content=response.body,
            media_type=response.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
            source_uri=self.url, checksum_sha256=checksum,
        )
        self._documents[external_id] = document
        self._next = SyncCheckpoint(
            cursor=_encode_state({
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "checksum": checksum, "seen": [external_id],
            }),
            revision=(checkpoint.revision + 1 if checkpoint else 1),
            updated_at=datetime.now(timezone.utc),
        )
        if state.get("checksum") == checksum:
            return ()
        return (ConnectorItem(external_id=external_id, revision=revision),)

    def fetch(self, item: ConnectorItem) -> SourceDocument:
        if item.deleted:
            return SourceDocument(
                external_id=item.external_id, revision=item.revision, title="",
                content=b"", media_type="application/octet-stream",
                source_uri=self.url, checksum_sha256=_sha(b""), deleted=True,
            )
        try:
            return self._documents[item.external_id]
        except KeyError as exc:
            raise KeyError("item must be fetched from the current listing") from exc

    def checkpoint(self) -> SyncCheckpoint:
        return self._next

    def close(self) -> None:
        self.client.close()


class RssConnector:
    def __init__(self, feed_url: str, client: SafeHttpClient) -> None:
        self.feed_url = feed_url
        self.client = client
        self._documents: dict[str, SourceDocument] = {}
        self._next = SyncCheckpoint()

    def test(self) -> None:
        self.client.validator.validate(self.feed_url)

    def list(self, checkpoint: SyncCheckpoint | None) -> Iterable[ConnectorItem]:
        state = _checkpoint_data(checkpoint)
        response = self.client.get(self.feed_url, headers=_conditional_headers(state))
        if response.status_code == 304:
            self._next = checkpoint or SyncCheckpoint()
            return ()
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"feed returned HTTP {response.status_code}")
        root = ElementTree.fromstring(response.body)
        documents = _parse_feed(root, self.feed_url)
        previous = set(state.get("seen", []))
        current = set(documents)
        old_revisions = state.get("revisions", {})
        items = [
            ConnectorItem(external_id=key, revision=doc.revision)
            for key, doc in documents.items()
            if old_revisions.get(key) != doc.revision
        ]
        items.extend(
            ConnectorItem(external_id=key, revision="deleted", deleted=True)
            for key in sorted(previous - current)
        )
        self._documents = documents
        self._next = SyncCheckpoint(
            cursor=_encode_state({
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
                "seen": sorted(current),
                "revisions": {key: doc.revision for key, doc in documents.items()},
            }),
            revision=(checkpoint.revision + 1 if checkpoint else 1),
            updated_at=datetime.now(timezone.utc),
        )
        return tuple(items)

    def fetch(self, item: ConnectorItem) -> SourceDocument:
        if item.deleted:
            return SourceDocument(
                external_id=item.external_id, revision=item.revision, title="",
                content=b"", media_type="text/plain", source_uri=self.feed_url,
                checksum_sha256=_sha(b""), deleted=True,
            )
        return self._documents[item.external_id]

    def checkpoint(self) -> SyncCheckpoint:
        return self._next

    def close(self) -> None:
        self.client.close()


def _parse_feed(root: Any, feed_url: str) -> dict[str, SourceDocument]:
    rows: dict[str, SourceDocument] = {}
    candidates = list(root.findall(".//item")) + list(root.findall(".//{*}entry"))
    for node in candidates:
        title = _text(node, "title")
        link_node = node.find("link")
        if link_node is None:
            link_node = node.find("{*}link")
        link = ""
        if link_node is not None:
            link = (link_node.text or link_node.attrib.get("href") or "").strip()
        guid = _text(node, "guid") or _text(node, "id") or link or title
        if not guid:
            continue
        content = (
            _text(node, "encoded") or _text(node, "content")
            or _text(node, "summary") or _text(node, "description")
        ).encode("utf-8")
        checksum = _sha(content)
        updated = _text(node, "updated") or _text(node, "pubDate")
        revision = updated or checksum
        external_id = hashlib.sha256(guid.encode()).hexdigest()
        rows[external_id] = SourceDocument(
            external_id=external_id, revision=revision, title=title,
            # The feed controls ``link``; keep the already-validated feed URL
            # as provenance and never turn an unvalidated entry link into an
            # internal fetch/citation target.
            content=content, media_type="text/html", source_uri=feed_url,
            checksum_sha256=checksum, metadata={"remote_id": guid},
        )
    return rows


def _text(node: Any, local: str) -> str:
    child = node.find(local)
    if child is None:
        child = node.find(f"{{*}}{local}")
    return (child.text or "").strip() if child is not None else ""


def _conditional_headers(state: dict[str, Any]) -> dict[str, str]:
    headers = {"accept": "application/rss+xml, application/atom+xml, */*"}
    if state.get("etag"):
        headers["if-none-match"] = str(state["etag"])
    if state.get("last_modified"):
        headers["if-modified-since"] = str(state["last_modified"])
    return headers


def _checkpoint_data(checkpoint: SyncCheckpoint | None) -> dict[str, Any]:
    if checkpoint is None or not checkpoint.cursor:
        return {}
    import json
    try:
        value = json.loads(checkpoint.cursor)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _encode_state(value: dict[str, Any]) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = ["ControlledUrlConnector", "RssConnector"]
