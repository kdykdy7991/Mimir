"""Encrypted datasource configuration and optimistic checkpoint storage."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialKeyMissingError(RuntimeError):
    pass


class CheckpointConflictError(RuntimeError):
    pass


class CredentialCipher:
    """AES-256-GCM envelope; the master key never enters SQLite."""

    ENV_NAME = "SKDY_DATASOURCE_MASTER_KEY"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("datasource master key must be exactly 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_env(cls) -> "CredentialCipher":
        raw = os.environ.get(cls.ENV_NAME)
        if not raw:
            raise CredentialKeyMissingError(
                f"{cls.ENV_NAME} is required to store datasource credentials",
            )
        try:
            key = base64.urlsafe_b64decode(raw.encode())
        except Exception as exc:
            raise CredentialKeyMissingError(f"{cls.ENV_NAME} is not valid base64") from exc
        return cls(key)

    def encrypt(self, credentials: Mapping[str, Any], *, source_id: str) -> bytes:
        nonce = os.urandom(12)
        plaintext = json.dumps(dict(credentials), sort_keys=True, separators=(",", ":")).encode()
        return nonce + self._cipher.encrypt(nonce, plaintext, source_id.encode())

    def decrypt(self, payload: bytes, *, source_id: str) -> dict[str, Any]:
        nonce, ciphertext = payload[:12], payload[12:]
        return json.loads(self._cipher.decrypt(nonce, ciphertext, source_id.encode()))


@dataclass(frozen=True)
class DataSource:
    id: str
    name: str
    connector_type: str
    collection_id: str
    policy: dict[str, Any]
    checkpoint: dict[str, Any]
    checkpoint_revision: int
    enabled: bool
    created_at: float
    updated_at: float


class DataSourceStore:
    def __init__(self, db_path: str | Path, cipher: CredentialCipher | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = cipher
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_sources (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    connector_type TEXT NOT NULL, collection_id TEXT NOT NULL,
                    policy_json TEXT NOT NULL, credentials_ciphertext BLOB NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_revision INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_data_sources_collection
                    ON data_sources(collection_id, enabled);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    status TEXT NOT NULL, started_at REAL NOT NULL,
                    finished_at REAL, added INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
                    conflicts INTEGER NOT NULL DEFAULT 0, error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sync_runs_source
                    ON sync_runs(source_id, started_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def create(
        self, *, name: str, connector_type: str, collection_id: str,
        policy: Mapping[str, Any], credentials: Mapping[str, Any],
        source_id: str | None = None,
    ) -> DataSource:
        source_id = source_id or str(uuid4())
        if self._cipher is None:
            raise CredentialKeyMissingError("credential cipher is required for writes")
        now = time.time()
        encrypted = self._cipher.encrypt(credentials, source_id=source_id)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO data_sources
                   (id,name,connector_type,collection_id,policy_json,
                    credentials_ciphertext,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (source_id, name.strip(), connector_type, collection_id,
                 json.dumps(dict(policy), sort_keys=True), encrypted, now, now),
            )
        return self.get(source_id)

    def get(self, source_id: str) -> DataSource:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM data_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._public(row)

    def list(self, *, collection_id: str | None = None) -> list[DataSource]:
        with self._connect() as conn:
            if collection_id is None:
                rows = conn.execute("SELECT * FROM data_sources ORDER BY created_at,id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM data_sources WHERE collection_id=? ORDER BY created_at,id",
                    (collection_id,),
                ).fetchall()
        return [self._public(row) for row in rows]

    def credentials(self, source_id: str) -> dict[str, Any]:
        if self._cipher is None:
            raise CredentialKeyMissingError("credential cipher is required for reads")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT credentials_ciphertext FROM data_sources WHERE id=?", (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._cipher.decrypt(bytes(row[0]), source_id=source_id)

    def update_checkpoint(
        self, source_id: str, checkpoint: Mapping[str, Any], *, expected_revision: int,
    ) -> DataSource:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE data_sources SET checkpoint_json=?, checkpoint_revision=checkpoint_revision+1,
                       updated_at=? WHERE id=? AND checkpoint_revision=?""",
                (json.dumps(dict(checkpoint), sort_keys=True), now, source_id, expected_revision),
            )
        if cur.rowcount != 1:
            raise CheckpointConflictError("datasource checkpoint changed concurrently")
        return self.get(source_id)

    def set_enabled(self, source_id: str, enabled: bool) -> DataSource:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE data_sources SET enabled=?,updated_at=? WHERE id=?",
                (int(enabled), time.time(), source_id),
            )
        if cur.rowcount != 1:
            raise KeyError(source_id)
        return self.get(source_id)

    def record_run(
        self, *, source_id: str, status: str, started_at: float,
        finished_at: float | None = None, added: int = 0, updated: int = 0,
        deleted: int = 0, conflicts: int = 0, error_code: str | None = None,
        run_id: str | None = None,
    ) -> str:
        if status not in {"running", "succeeded", "failed", "conflict"}:
            raise ValueError("invalid sync run status")
        run_id = run_id or str(uuid4())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sync_runs
                   (id,source_id,status,started_at,finished_at,added,updated,deleted,conflicts,error_code)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id,source_id,status,started_at,finished_at,added,updated,
                 deleted,conflicts,error_code),
            )
        return run_id

    def finish_run(
        self, run_id: str, *, status: str, finished_at: float,
        added: int = 0, updated: int = 0, deleted: int = 0,
        conflicts: int = 0, error_code: str | None = None,
    ) -> None:
        if status not in {"succeeded", "failed", "conflict"}:
            raise ValueError("invalid terminal sync run status")
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE sync_runs SET status=?,finished_at=?,added=?,updated=?,
                   deleted=?,conflicts=?,error_code=?
                   WHERE id=? AND status='running'""",
                (status, finished_at, added, updated, deleted, conflicts,
                 error_code, run_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(run_id)

    def sync_status(self, source_id: str) -> dict[str, Any]:
        source = self.get(source_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_runs WHERE source_id=? ORDER BY started_at DESC,id DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        return {
            "source": source,
            "last_run": dict(row) if row else None,
        }

    def list_failures(self, source_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        self.get(source_id)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM sync_runs WHERE source_id=? AND status IN ('failed','conflict')
                   ORDER BY started_at DESC,id DESC LIMIT ?""",
                (source_id,limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _public(row: sqlite3.Row) -> DataSource:
        return DataSource(
            id=str(row["id"]), name=str(row["name"]),
            connector_type=str(row["connector_type"]),
            collection_id=str(row["collection_id"]),
            policy=json.loads(row["policy_json"]),
            checkpoint=json.loads(row["checkpoint_json"]),
            checkpoint_revision=int(row["checkpoint_revision"]),
            enabled=bool(row["enabled"]), created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


__all__ = [
    "CheckpointConflictError", "CredentialCipher", "CredentialKeyMissingError",
    "DataSource", "DataSourceStore",
]
