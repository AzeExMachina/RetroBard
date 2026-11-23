# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.domain.models import AudioEffect, VoiceProfile


class TestSqliteDiskCache:
    def test_cache_miss_returns_none(self, tmp_path: Path) -> None:
        cache = SqliteDiskCache(tmp_path)
        assert cache.get("chrono_trigger", "CRONO", "Hello!", "voice_1") is None

    def test_put_and_get_roundtrip_with_case_normalization(
        self, tmp_path: Path
    ) -> None:
        cache = SqliteDiskCache(tmp_path)
        fake_mp3 = b"ID3_FAKE_MP3_BYTES_12345"

        cache.put("chrono_trigger", "CRONO", "Wait up, Marle!", "voice_1", fake_mp3)

        # Lookup with different casing and surrounding whitespace hits the same key
        retrieved = cache.get(
            "chrono_trigger", "CRONO", "  wait up, marle! \n", "voice_1"
        )
        assert retrieved == fake_mp3

    def test_voice_profile_signature_reuses_cache_for_local_pitch_and_effects(
        self, tmp_path: Path
    ) -> None:
        cache = SqliteDiskCache(tmp_path)
        fake_mp3 = b"GATO_CACHED_MP3"

        base_profile = VoiceProfile(voice_id="v_callum", speed=0.9, stability=0.8)
        shifted_profile = VoiceProfile(
            voice_id="v_callum",
            speed=0.9,
            stability=0.8,
            pitch_semitones=-4.0,
            effect=AudioEffect.ROBOT,
        )
        different_api_speed = VoiceProfile(
            voice_id="v_callum", speed=1.15, stability=0.8
        )

        cache.put(
            "chrono_trigger",
            "GATO",
            "I have metal joints!",
            base_profile.signature(),
            fake_mp3,
        )

        # Changing local pitch/effect MUST hit the existing cache (0 API credits)
        assert (
            cache.get(
                "chrono_trigger",
                "GATO",
                "I have metal joints!",
                shifted_profile.signature(),
            )
            == fake_mp3
        )

        # Changing ElevenLabs API speed MUST trigger a cache miss
        assert (
            cache.get(
                "chrono_trigger",
                "GATO",
                "I have metal joints!",
                different_api_speed.signature(),
            )
            is None
        )

    def test_purge_speaker_deletes_only_target_character(
        self, tmp_path: Path
    ) -> None:
        cache = SqliteDiskCache(tmp_path)
        cache.put("chrono_trigger", "FROG", "Line 1", "v_frog", b"frog_mp3_1")
        cache.put("chrono_trigger", "FROG", "Line 2", "v_frog", b"frog_mp3_2")
        cache.put("chrono_trigger", "LUCCA", "Line 1", "v_lucca", b"lucca_mp3_1")

        deleted_count = cache.purge_speaker("chrono_trigger", "frog")
        assert deleted_count == 2

        # Frog's lines are gone, Lucca's line remains intact
        assert cache.get("chrono_trigger", "FROG", "Line 1", "v_frog") is None
        assert (
            cache.get("chrono_trigger", "LUCCA", "Line 1", "v_lucca") == b"lucca_mp3_1"
        )

    def test_missing_mp3_file_on_disk_handles_gracefully(
        self, tmp_path: Path
    ) -> None:
        cache = SqliteDiskCache(tmp_path)
        cache.put("chrono_trigger", "CRONO", "Test", "v1", b"mp3_data")

        # Manually delete the .mp3 file behind SQLite's back
        for mp3_file in (tmp_path / "chrono_trigger").glob("*.mp3"):
            mp3_file.unlink()

        assert cache.get("chrono_trigger", "CRONO", "Test", "v1") is None

class TestCacheHousekeeping:
    def test_prune_removes_the_least_recently_used_first(self, tmp_path: Path) -> None:
        cache = SqliteDiskCache(tmp_path)
        for line in ("a", "b", "c"):
            cache.put("ct", "CRONO", line, "v1", b"x" * 100)
        assert cache.get("ct", "CRONO", "a", "v1")  # "a" is now the most recently used

        assert cache.prune(max_bytes=200) == 1

        assert cache.get("ct", "CRONO", "b", "v1") is None
        assert cache.get("ct", "CRONO", "a", "v1") and cache.get("ct", "CRONO", "c", "v1")
        assert cache.count() == 2 and cache.total_bytes() == 200

    def test_put_keeps_the_cache_under_its_limit(self, tmp_path: Path) -> None:
        cache = SqliteDiskCache(tmp_path, max_bytes=250)
        for line in ("a", "b", "c", "d"):
            cache.put("ct", "CRONO", line, "v1", b"x" * 100)
        assert cache.total_bytes() <= 250
        assert cache.get("ct", "CRONO", "d", "v1") is not None

    def test_clear_keeps_the_index_and_the_udp_token(self, tmp_path: Path) -> None:
        cache = SqliteDiskCache(tmp_path)
        (tmp_path / "udp_token").write_text("secret", encoding="utf-8")
        cache.put("ct", "CRONO", "a", "v1", b"x")
        cache.put("other_game", "MARLE", "b", "v2", b"y")

        assert cache.clear() == 2

        assert cache.count() == 0 and cache.total_bytes() == 0
        assert (tmp_path / "index.sqlite3").exists()
        assert (tmp_path / "udp_token").read_text(encoding="utf-8") == "secret"
        assert not (tmp_path / "ct").exists() and not (tmp_path / "other_game").exists()
        cache.put("ct", "CRONO", "a", "v1", b"x")  # still usable afterwards
        assert cache.count() == 1

    def test_old_database_gets_the_new_column(self, tmp_path: Path) -> None:
        import sqlite3

        with sqlite3.connect(tmp_path / "index.sqlite3") as conn:
            conn.execute(
                "CREATE TABLE audio_cache (cache_key TEXT PRIMARY KEY, game_id TEXT NOT NULL, "
                "speaker TEXT NOT NULL, voice_id TEXT NOT NULL, dialogue_text TEXT NOT NULL, "
                "relative_path TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO audio_cache VALUES ('k', 'ct', 'CRONO', 'v1', '', 'ct/k.mp3', "
                "'2025-01-01T00:00:00+00:00')"
            )
        (tmp_path / "ct").mkdir()
        (tmp_path / "ct" / "k.mp3").write_bytes(b"old")

        cache = SqliteDiskCache(tmp_path)
        cache.put("ct", "CRONO", "new", "v1", b"new!")

        assert cache.count() == 2
        assert cache.prune(max_bytes=4) == 1  # the old line, without last_used_at, goes first
        assert not (tmp_path / "ct" / "k.mp3").exists()

    def test_missing_file_during_get_returns_none(self, tmp_path: Path) -> None:
        cache = SqliteDiskCache(tmp_path, file_ext="pcm")
        cache.put("ct", "CRONO", "Test", "v1", b"pcm")
        next((tmp_path / "ct").glob("*.pcm")).unlink()
        assert cache.get("ct", "CRONO", "Test", "v1") is None
        assert cache.count() == 0
