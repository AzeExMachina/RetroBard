# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

_clock_lock = threading.Lock()
_last_stamp = datetime.min.replace(tzinfo=UTC)


def _now() -> str:
    """An ISO timestamp that never repeats, so the oldest line is always well defined."""
    global _last_stamp
    with _clock_lock:
        stamp = datetime.now(UTC)
        if stamp <= _last_stamp:
            stamp = _last_stamp + timedelta(microseconds=1)
        _last_stamp = stamp
    return stamp.isoformat()


class SqliteDiskCache:
    """Spoken lines on disk, indexed in SQLite by a hash of voice and text.

    The folder also holds the UDP token (session._start_udp), which this class never
    touches. With max_bytes set, the least recently used lines are removed whenever
    the cache grows past it."""

    def __init__(self, base_dir: Path, file_ext: str = "mp3", max_bytes: int | None = None):
        self.base_dir = base_dir
        self.file_ext = file_ext
        self.max_bytes = max_bytes
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "index.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audio_cache (
                    cache_key TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    voice_id TEXT NOT NULL,
                    dialogue_text TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_game_speaker ON audio_cache(game_id, speaker);"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(audio_cache)")}
            if "last_used_at" not in columns:
                conn.execute("ALTER TABLE audio_cache ADD COLUMN last_used_at TEXT")
            # Blank out text kept by older versions (lookups only use the hashed key)
            conn.execute(
                "UPDATE audio_cache SET dialogue_text = '' WHERE dialogue_text != ''"
            )

    @staticmethod
    def _make_key(voice_id: str, text: str) -> str:
        normalized = f"{voice_id}:{text.strip().lower()}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def get(self, game_id: str, speaker: str, text: str, voice_id: str) -> bytes | None:
        cache_key = self._make_key(voice_id, text)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT relative_path FROM audio_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()

        if not row:
            return None

        file_path = self.base_dir / row[0]
        try:
            data = file_path.read_bytes()
        except OSError:  # removed behind the index's back
            return None
        with self._connect() as conn:
            conn.execute(
                "UPDATE audio_cache SET last_used_at = ? WHERE cache_key = ?",
                (_now(), cache_key),
            )
        return data

    def put(
        self, game_id: str, speaker: str, text: str, voice_id: str, data: bytes
    ) -> None:
        cache_key = self._make_key(voice_id, text)
        rel_path = f"{game_id}/{cache_key}.{self.file_ext}"
        full_path = (self.base_dir / rel_path).resolve()
        if not full_path.is_relative_to(self.base_dir.resolve()):
            raise ValueError(f"Refusing to write the cache outside {self.base_dir}: {game_id!r}")
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audio_cache
                (cache_key, game_id, speaker, voice_id, dialogue_text, relative_path,
                 created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                # The dialogue text itself is deliberately not stored, only its hash
                (cache_key, game_id, speaker, voice_id, "", rel_path, now, now),
            )
        if self.max_bytes is not None:
            self.prune(self.max_bytes)

    # ------------------------------------------------------------------ housekeeping

    def _indexed_files(self) -> list[tuple[str, Path, int]]:
        """(cache_key, file, size) of every indexed line, least recently used first.
        Size is 0 when the file is missing."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT cache_key, relative_path FROM audio_cache "
                "ORDER BY COALESCE(last_used_at, created_at), rowid"
            ).fetchall()
        files = []
        for cache_key, rel_path in rows:
            path = self.base_dir / rel_path
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            files.append((cache_key, path, size))
        return files

    def total_bytes(self) -> int:
        """Size of the audio files the index refers to."""
        return sum(size for _key, _path, size in self._indexed_files())

    def count(self) -> int:
        """How many spoken lines are cached."""
        return sum(1 for _key, path, _size in self._indexed_files() if path.exists())

    def _remove(self, entries: list[tuple[str, Path, int]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM audio_cache WHERE cache_key = ?", [(key,) for key, _, _ in entries]
            )
        for _key, path, _size in entries:
            path.unlink(missing_ok=True)

    def prune(self, max_bytes: int) -> int:
        """Removes the least recently used lines until the cache is under max_bytes.
        Returns how many were removed."""
        files = self._indexed_files()
        total = sum(size for _key, _path, size in files)
        doomed = []
        for entry in files:
            if total <= max_bytes:
                break
            doomed.append(entry)
            total -= entry[2]
        if doomed:
            self._remove(doomed)
        return len(doomed)

    def clear(self) -> int:
        """Removes every cached line and the game folders left empty. The index file
        and the UDP token stay. Returns how many lines were removed."""
        files = self._indexed_files()
        self._remove(files)
        for folder in self.base_dir.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        return len(files)

    def purge_speaker(self, game_id: str, speaker: str) -> int:
        """Deletes all cached audio files and DB rows for a specific character."""
        speaker_upper = speaker.strip().upper()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT relative_path FROM audio_cache WHERE game_id = ? AND speaker = ?",
                (game_id, speaker_upper),
            ).fetchall()
            conn.execute(
                "DELETE FROM audio_cache WHERE game_id = ? AND speaker = ?",
                (game_id, speaker_upper),
            )

        deleted = 0
        for (rel_path,) in rows:
            file_path = self.base_dir / rel_path
            if file_path.exists():
                file_path.unlink()
                deleted += 1
        return deleted