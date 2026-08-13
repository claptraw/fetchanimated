from __future__ import annotations

import re
from pathlib import Path

import beets

from beetsplug.fetchanimated import (
    PLUGIN_VERSION,
    FetchAnimatedPlugin,
    HlsVariant,
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
