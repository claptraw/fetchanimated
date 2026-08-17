from __future__ import annotations

import re
from pathlib import Path

import beets

from beetsplug.fetchanimated import (
    PLUGIN_VERSION,
    FetchAnimatedPlugin,
    HlsVariant,
    NumericSuffixMismatch,
    ResolverResult,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "beetsplug" / "fetchanimated.py"
PYPROJECT = ROOT / "pyproject.toml"


def plugin() -> FetchAnimatedPlugin:
    return FetchAnimatedPlugin()


def test_version_matches_package_metadata() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    assert match is not None
    assert match.group(1) == PLUGIN_VERSION


def test_public_source_has_no_machine_specific_defaults() -> None:
    text = SOURCE.read_text(encoding="utf-8").casefold()
    assert re.search(r"/(?:mnt|config)/", text) is None
    assert "192.168." not in text


def test_default_ffmpeg_uses_path_lookup() -> None:
    instance = plugin()
    assert instance.ffmpeg_path == "ffmpeg"


def test_standard_import_stage_is_enabled_by_default() -> None:
    instance = plugin()
    assert instance.import_stages == [instance.fetch_animated]


def test_programmatic_existing_album_api_remains_available() -> None:
    instance = plugin()
    assert callable(instance.ensure_album_assets)
    assert callable(instance.ensure_album_asset)


def test_item_copied_preserves_existing_sidecar_without_overwrite(tmp_path: Path) -> None:
    instance = plugin()
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()

    source_audio = source_dir / "01.flac"
    destination_audio = destination_dir / "01.flac"
    source_audio.write_bytes(b"audio")
    destination_audio.write_bytes(b"audio")
    (source_dir / "cover.webp").write_bytes(b"source-sidecar")

    instance.item_copied(None, source_audio, destination_audio)
    assert (destination_dir / "cover.webp").read_bytes() == b"source-sidecar"

    (source_dir / "cover.webp").write_bytes(b"new-source-sidecar")
    instance.item_copied(None, source_audio, destination_audio)
    assert (destination_dir / "cover.webp").read_bytes() == b"source-sidecar"


def test_parse_master_variants_prefers_higher_bandwidth_duplicate() -> None:
    text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=768x768
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=768x768
high/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=1024x1024
large/index.m3u8
"""
    variants = FetchAnimatedPlugin._parse_master_variants(
        text, "https://example.test/master.m3u8"
    )
    by_width = {variant.width: variant for variant in variants}
    assert by_width[768].bandwidth == 200000
    assert by_width[768].url == "https://example.test/high/index.m3u8"
    assert by_width[1024].url == "https://example.test/large/index.m3u8"


def test_nearest_resolution_tie_prefers_larger_stream() -> None:
    instance = plugin()
    instance.config["resolution_policy"].set("nearest")
    variants = [
        HlsVariant("https://example.test/640.m3u8", 640, 640, 100),
        HlsVariant("https://example.test/896.m3u8", 896, 896, 100),
    ]
    selected = instance._choose_variant(variants, 768)
    assert selected is not None
    assert selected.width == 896


def test_at_most_resolution_policy() -> None:
    instance = plugin()
    instance.config["resolution_policy"].set("at_most")
    variants = [
        HlsVariant("https://example.test/640.m3u8", 640, 640, 100),
        HlsVariant("https://example.test/768.m3u8", 768, 768, 100),
        HlsVariant("https://example.test/1024.m3u8", 1024, 1024, 100),
    ]
    selected = instance._choose_variant(variants, 800)
    assert selected is not None
    assert selected.width == 768


def test_at_least_resolution_policy() -> None:
    instance = plugin()
    instance.config["resolution_policy"].set("at_least")
    variants = [
        HlsVariant("https://example.test/640.m3u8", 640, 640, 100),
        HlsVariant("https://example.test/896.m3u8", 896, 896, 100),
        HlsVariant("https://example.test/1024.m3u8", 1024, 1024, 100),
    ]
    selected = instance._choose_variant(variants, 800)
    assert selected is not None
    assert selected.width == 896


def test_highest_resolution_policy() -> None:
    instance = plugin()
    instance.config["resolution_policy"].set("highest")
    variants = [
        HlsVariant("https://example.test/768.m3u8", 768, 768, 500),
        HlsVariant("https://example.test/1024.m3u8", 1024, 1024, 100),
    ]
    selected = instance._choose_variant(variants, 768)
    assert selected is not None
    assert selected.width == 1024


def test_invalid_output_filename_falls_back_to_safe_default() -> None:
    instance = plugin()
    instance.config["square_webp_filename"].set("../private/cover.webp")
    assert instance.square_webp_filename == "cover.webp"


def test_numeric_suffix_guard_is_narrow() -> None:
    conflict = FetchAnimatedPlugin._numeric_suffix_conflict
    assert conflict("Gangsta Art", "Gangsta Art 2") is True
    assert conflict("Gangsta Art 2", "Gangsta Art") is True
    assert conflict("Gangsta Art 2", "Gangsta Art 3") is True

    assert conflict("DS4EVER", "DRIP SEASON 4EVER") is False
    assert conflict("LONG.LIVE.A$AP", "LONG LIVE A$AP") is False
    assert conflict("Gangsta Art 2", "Gangsta Art 2 Deluxe") is False
    assert conflict("Gangsta Art", "Gangsta Art Deluxe") is False


class _FakeItem:
    def __init__(self, tracktotal: int = 0) -> None:
        self.tracktotal = tracktotal
        self.title = "Example Track"
        self.artist = "Example Artist"


class _FakeAlbum:
    def __init__(
        self,
        album: str,
        albumartist: str,
        *,
        year: int = 0,
        tracktotal: int = 0,
    ) -> None:
        self.album = album
        self.albumartist = albumartist
        self.year = year
        self._items = [_FakeItem(tracktotal)]

    def items(self):
        return iter(self._items)


def test_numeric_suffix_fallback_selects_requested_numbered_album() -> None:
    instance = plugin()
    album = _FakeAlbum("Example Album 2", "Example Artist", year=2026, tracktotal=12)
    results = [
        {
            "collectionId": 1,
            "collectionName": "Example Album",
            "artistName": "Example Artist",
            "trackCount": 12,
            "releaseDate": "2026-01-01T00:00:00Z",
        },
        {
            "collectionId": 2,
            "collectionName": "Example Album 2",
            "artistName": "Example Artist & Friends",
            "trackCount": 12,
            "releaseDate": "2026-01-01T00:00:00Z",
        },
        {
            "collectionId": 3,
            "collectionName": "Example Album 3",
            "artistName": "Example Artist",
            "trackCount": 12,
            "releaseDate": "2026-01-01T00:00:00Z",
        },
    ]
    selected = instance._select_exact_itunes_candidate(
        album, "Example Artist", "Example Album 2", results
    )
    assert selected is not None
    assert selected["collectionId"] == 2


def test_numeric_suffix_fallback_uses_tracktotal_to_avoid_guessing() -> None:
    instance = plugin()
    album = _FakeAlbum("Example Album 2", "Example Artist", tracktotal=18)
    results = [
        {
            "collectionId": 18,
            "collectionName": "Example Album 2",
            "artistName": "Example Artist",
            "trackCount": 18,
        },
        {
            "collectionId": 21,
            "collectionName": "Example Album 2",
            "artistName": "Example Artist",
            "trackCount": 21,
        },
    ]
    selected = instance._select_exact_itunes_candidate(
        album, "Example Artist", "Example Album 2", results
    )
    assert selected is not None
    assert selected["collectionId"] == 18


def test_resolve_album_recovers_after_numeric_suffix_mismatch(monkeypatch) -> None:
    instance = plugin()
    album = _FakeAlbum("Example Album 2", "Example Artist")
    expected = ResolverResult(
        square_url="https://example.test/square.m3u8",
        tall_url=None,
        api_artist="Example Artist",
        api_album="Example Album 2",
    )

    def mismatched_search(*args, **kwargs):
        raise NumericSuffixMismatch("Example Artist", "Example Album")

    called = {}

    def exact_fallback(album_arg, artist_arg, album_name_arg):
        called["value"] = (album_arg, artist_arg, album_name_arg)
        return expected

    monkeypatch.setattr(instance, "_api_search", mismatched_search)
    monkeypatch.setattr(instance, "_resolve_numeric_suffix_exact", exact_fallback)

    result = instance._resolve_album(album)
    assert result == expected
    assert called["value"] == (album, "Example Artist", "Example Album 2")


def test_retry_parser_uses_only_latest_eligible_report(tmp_path: Path) -> None:
    report = tmp_path / "fetchanimated.log"
    report.write_text(
        "\n".join(
            [
                "fetchanimated v0.1.1 full-library report",
                "ERRORS (1)",
                "- Old Artist - Old Album -- old timeout",
                "",
                "fetchanimated v0.1.2 retry-errors report",
                "ERRORS (2)",
                "- Artist One - Album One -- HTTP 504",
                "- Artist-Two - Album - With - Hyphens -- read timeout",
                "",
            ]
        ),
        encoding="utf-8",
    )

    instance = plugin()
    assert instance._load_retry_error_labels(str(report)) == [
        "Artist One - Album One",
        "Artist-Two - Album - With - Hyphens",
    ]


def test_retry_report_is_disabled_by_default() -> None:
    instance = plugin()
    assert instance.config["retry_errors_log"].get(str) == ""
