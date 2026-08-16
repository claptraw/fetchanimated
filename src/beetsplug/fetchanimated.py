"""Fetch Apple Music animated album artwork for beets.

fetchanimated v0.1.2 is a standalone beets plugin intentionally isolated from static album artwork:

* It never requires or modifies ``cover.jpg``, ``album.artpath`` or embedded
  artwork.
* Square and tall Apple Music motion artwork are resolved independently via the
  m8tec API.
* For each requested variant, the Apple HLS master manifest is inspected and
  the actually available stream resolution nearest to the configured target
  width is selected automatically.
* The selected HLS stream is remuxed losslessly to a temporary MP4. MP4 outputs
  are kept only when enabled. WebP outputs are encoded from that selected stream
  with libwebp, infinite looping and the configured quality.
* Existing output files are never changed unless ``overwrite: yes`` or the
  manual ``--force`` option is used.
* Automatic and interactive beets album imports use standard beets import hooks.
* A standalone ``beet fetchanimated`` command supports queries, dry runs,
  limited batches, full-library backfills, and retries of prior API errors.

Because beets fetchart accepts WebP files from its filesystem source and local
files named ``cover.*`` can become static artwork candidates, this plugin
narrowly excludes its configured animated WebP sidecars from that fetchart
filesystem source. The existing static cover.jpg/embedart workflow remains
independent and unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from beets import importer, plugins, ui, util

if TYPE_CHECKING:
    from beets.importer import ImportSession, ImportTask
    from beets.library import Album, Library


PLUGIN_VERSION = "0.1.2"


@dataclass(frozen=True)
class ResolverResult:
    square_url: str | None
    tall_url: str | None
    api_artist: str | None = None
    api_album: str | None = None


@dataclass(frozen=True)
class HlsVariant:
    url: str
    width: int | None = None
    height: int | None = None
    bandwidth: int = 0

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "source"


@dataclass(frozen=True)
class AssetSpec:
    key: str
    variant: str
    format: str
    filename: str


@dataclass
class PreparedAsset:
    path: str
    spec: AssetSpec
    selected_resolution: str

    def cleanup(self) -> None:
        if not self.path:
            return
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


@dataclass
class PreparedBundle:
    assets: list[PreparedAsset]

    def cleanup(self) -> None:
        for asset in self.assets:
            asset.cleanup()
        self.assets.clear()


class ArtworkApiUnavailable(RuntimeError):
    """Transient/transport failure from the public artwork API.

    This is deliberately distinct from a normal 404/no-artwork result so one
    broken request aborts only the current album's fallback attempts.
    """


class FetchAnimatedPlugin(plugins.BeetsPlugin):
    """Fetch optional Apple Music motion-art sidecars for albums."""

    def __init__(self) -> None:
        super().__init__()
        self.config.add(
            {
                "auto": True,
                "fetch_for_asis": False,
                "api_url": "https://artwork.m8tec.top",
                "save_square_webp": True,
                "save_square_mp4": False,
                "save_tall_mp4": False,
                "save_tall_webp": False,
                "square_webp_filename": "cover.webp",
                "square_mp4_filename": "cover.mp4",
                "tall_mp4_filename": "cover-tall.mp4",
                "tall_webp_filename": "cover-tall.webp",
                "square_target_width": 768,
                "tall_target_width": 830,
                "resolution_policy": "nearest",
                "webp_quality": 80,
                "ffmpeg": "ffmpeg",
                "api_timeout": 60,
                "api_request_delay_seconds": 3.0,
                "api_error_backoff_seconds": 30.0,
                "manifest_timeout": 20,
                "ffmpeg_timeout": 600,
                "overwrite": False,
                "move_with_album": True,
                "batch_delay_seconds": 0.25,
                "full_library_log": "",
                "limit_log": "",
                "retry_errors_log": "",
                "protect_fetchart_filesystem": True,
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15"
                ),
            }
        )

        self._prepared_tasks: dict[Any, PreparedBundle] = {}
        self._last_api_request_at = 0.0
        self._fetchart_protected = False
        self._protect_fetchart_filesystem()

        if self.config["auto"].get(bool):
            self.import_stages = [self.fetch_animated]
            self.register_listener("import_task_files", self.assign_animated)

        if self.config["move_with_album"].get(bool):
            self.register_listener("item_copied", self.item_copied)
            self.register_listener("item_moved", self.item_moved)

        self.register_listener("cli_exit", self.cleanup_prepared)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    def _filename(self, key: str, fallback: str, extension: str) -> str:
        value = self.config[key].get(str).strip()
        if (
            not value
            or value in {".", ".."}
            or os.path.basename(value) != value
            or not value.casefold().endswith(extension.casefold())
        ):
            self._log.warning(
                "fetchanimated: invalid {} {!r}; using {!r}",
                key,
                value,
                fallback,
            )
            return fallback
        return value

    @property
    def square_webp_filename(self) -> str:
        return self._filename("square_webp_filename", "cover.webp", ".webp")

    @property
    def square_mp4_filename(self) -> str:
        return self._filename("square_mp4_filename", "cover.mp4", ".mp4")

    @property
    def tall_mp4_filename(self) -> str:
        return self._filename("tall_mp4_filename", "cover-tall.mp4", ".mp4")

    @property
    def tall_webp_filename(self) -> str:
        return self._filename("tall_webp_filename", "cover-tall.webp", ".webp")

    @property
    def square_target_width(self) -> int:
        return max(1, min(8192, self.config["square_target_width"].get(int)))

    @property
    def tall_target_width(self) -> int:
        return max(1, min(8192, self.config["tall_target_width"].get(int)))

    @property
    def resolution_policy(self) -> str:
        value = self.config["resolution_policy"].get(str).strip().casefold()
        allowed = {"nearest", "at_most", "at_least", "highest"}
        if value not in allowed:
            self._log.warning(
                "fetchanimated: invalid resolution_policy {!r}; using nearest",
                value,
            )
            return "nearest"
        return value

    @property
    def webp_quality(self) -> int:
        return max(0, min(100, self.config["webp_quality"].get(int)))

    @property
    def ffmpeg_path(self) -> str:
        return self.config["ffmpeg"].get(str).strip() or "ffmpeg"

    def _all_specs(self) -> list[AssetSpec]:
        return [
            AssetSpec(
                "save_square_webp",
                "square",
                "webp",
                self.square_webp_filename,
            ),
            AssetSpec(
                "save_square_mp4",
                "square",
                "mp4",
                self.square_mp4_filename,
            ),
            AssetSpec(
                "save_tall_mp4",
                "tall",
                "mp4",
                self.tall_mp4_filename,
            ),
            AssetSpec(
                "save_tall_webp",
                "tall",
                "webp",
                self.tall_webp_filename,
            ),
        ]

    def _enabled_specs(self) -> list[AssetSpec]:
        return [spec for spec in self._all_specs() if self.config[spec.key].get(bool)]

    # ------------------------------------------------------------------
    # Keep static fetchart independent from animated WebP sidecars
    # ------------------------------------------------------------------

    def _motion_webp_names(self) -> set[str]:
        return {
            self.square_webp_filename.casefold(),
            self.tall_webp_filename.casefold(),
        }

    def _protect_fetchart_filesystem(self) -> None:
        """Exclude only fetchanimated WebPs from fetchart filesystem input."""
        if self._fetchart_protected:
            return
        if not self.config["protect_fetchart_filesystem"].get(bool):
            return

        try:
            loaded = list(plugins.find_plugins())
        except Exception as exc:
            self._log.debug(
                "fetchanimated: could not inspect loaded plugins: {}", exc
            )
            return

        fetchart = next(
            (plugin for plugin in loaded if getattr(plugin, "name", "") == "fetchart"),
            None,
        )
        if fetchart is None:
            return

        protected_any = False
        motion_names = self._motion_webp_names()
        for source in getattr(fetchart, "sources", []) or []:
            if getattr(source, "ID", "") != "filesystem":
                continue
            if getattr(source, "_fetchanimated_v1_protected", False):
                protected_any = True
                continue

            original_get = source.get

            def filtered_get(
                album: Any,
                plugin: Any,
                paths: Any,
                *,
                _original_get: Any = original_get,
                _motion_names: set[str] = motion_names,
            ):
                for candidate in _original_get(album, plugin, paths):
                    candidate_path = getattr(candidate, "path", None)
                    if candidate_path:
                        try:
                            basename = os.path.basename(
                                os.fsdecode(candidate_path)
                            ).casefold()
                        except Exception:
                            basename = ""
                        if basename in _motion_names:
                            self._log.debug(
                                "fetchanimated: excluding animated sidecar {} "
                                "from fetchart filesystem candidates",
                                basename,
                            )
                            continue
                    yield candidate

            source.get = filtered_get
            source._fetchanimated_v1_protected = True
            protected_any = True

        self._fetchart_protected = protected_any

    # ------------------------------------------------------------------
    # Album/path helpers
    # ------------------------------------------------------------------

    def _album_dir(self, album: Album) -> str | None:
        """Return current album directory without inspecting static artwork."""
        try:
            album_path = getattr(album, "path", None)
            if album_path:
                return util.syspath(album_path)
            for item in album.items():
                if getattr(item, "path", None):
                    return os.path.dirname(util.syspath(item.path))
                break
        except Exception as exc:
            self._log.debug("fetchanimated: cannot inspect album path: {}", exc)
        return None

    def _destination(self, album: Album, spec: AssetSpec) -> str | None:
        album_dir = self._album_dir(album)
        if not album_dir:
            return None
        return os.path.join(album_dir, spec.filename)

    def _effective_force(self, force: bool) -> bool:
        return bool(force or self.config["overwrite"].get(bool))

    def _pending_specs(self, album: Album, *, force: bool) -> list[AssetSpec]:
        pending: list[AssetSpec] = []
        for spec in self._enabled_specs():
            destination = self._destination(album, spec)
            if force or not (destination and os.path.isfile(destination)):
                pending.append(spec)
        return pending

    # ------------------------------------------------------------------
    # Upstream artwork resolution
    # ------------------------------------------------------------------

    def _search_artist(self, album: Album) -> str | None:
        """Return one artist value for m8tec's normal metadata search.

        The public m8tec endpoint already owns Apple Music result matching. To
        avoid inventing a second search policy here, fetchanimated performs one
        metadata lookup per Beets album using its album artist. Only when the
        album artist is blank do we fall back to the first non-empty track
        artist so the required m8tec ``artist`` parameter can still be supplied.
        """
        artist = self._text(getattr(album, "albumartist", ""))
        if artist:
            return artist
        try:
            for item in album.items():
                artist = self._text(getattr(item, "artist", ""))
                if artist:
                    return artist
        except Exception as exc:
            self._log.debug(
                "fetchanimated: cannot determine search artist: {}", exc
            )
        return None

    def _search_title_hint(self, album: Album) -> str | None:
        try:
            for item in album.items():
                title = self._text(getattr(item, "title", ""))
                if title:
                    return title
        except Exception:
            pass
        return None

    @staticmethod
    def _title_tokens(value: str) -> list[str]:
        """Normalize a title only for the narrow numeric-suffix safety check."""
        folded = unicodedata.normalize("NFKD", value.casefold())
        ascii_value = folded.encode("ascii", "ignore").decode("ascii")
        return re.findall(r"[a-z0-9]+", ascii_value)

    @classmethod
    def _numeric_suffix_conflict(cls, requested: str, returned: str) -> bool:
        """Detect only an otherwise-identical standalone numeric suffix mismatch.

        Examples rejected: ``Gangsta Art`` vs ``Gangsta Art 2`` and
        ``Gangsta Art 2`` vs ``Gangsta Art 3``. Alphanumeric names such as
        ``DS4EVER`` / ``DRIP SEASON 4EVER`` and punctuation-only differences
        such as ``LONG.LIVE.A$AP`` remain outside this guard.
        """
        requested_tokens = cls._title_tokens(requested)
        returned_tokens = cls._title_tokens(returned)
        if not requested_tokens or not returned_tokens:
            return False
        if requested_tokens == returned_tokens:
            return False

        if (
            len(requested_tokens) == len(returned_tokens)
            and len(requested_tokens) >= 2
            and requested_tokens[:-1] == returned_tokens[:-1]
            and requested_tokens[-1].isdigit()
            and returned_tokens[-1].isdigit()
        ):
            return requested_tokens[-1] != returned_tokens[-1]

        if len(requested_tokens) + 1 == len(returned_tokens):
            return (
                requested_tokens == returned_tokens[:-1]
                and returned_tokens[-1].isdigit()
            )
        if len(returned_tokens) + 1 == len(requested_tokens):
            return (
                returned_tokens == requested_tokens[:-1]
                and requested_tokens[-1].isdigit()
            )
        return False

    def _wait_before_api_request(self) -> None:
        """Space public API searches without delaying the first request."""
        delay = max(0.0, self.config["api_request_delay_seconds"].get(float))
        if delay <= 0:
            self._last_api_request_at = time.monotonic()
            return
        now = time.monotonic()
        if self._last_api_request_at > 0:
            remaining = delay - (now - self._last_api_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_api_request_at = time.monotonic()

    def _request_json(self, url: str, timeout: int) -> dict[str, Any] | None:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.config["user_agent"].get(str).strip()
                or f"fetchanimated/{PLUGIN_VERSION}"
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if not isinstance(payload, dict):
            return None
        return payload

    def _api_search(
        self,
        artist: str,
        album_name: str,
        title_hint: str | None,
    ) -> ResolverResult | None:
        base = self.config["api_url"].get(str).strip().rstrip("/")
        params: dict[str, str] = {"artist": artist, "album": album_name}
        if title_hint:
            params["title"] = title_hint
        url = f"{base}/api/v1/artwork/search?{urllib.parse.urlencode(params)}"

        timeout = max(1, self.config["api_timeout"].get(int))
        self._wait_before_api_request()
        try:
            payload = self._request_json(url, timeout)
        except urllib.error.HTTPError as exc:
            # _request_json already turns a genuine 404/no-artwork into None.
            # Any other HTTP failure is transient/service-level and must stop
            # this album's search attempt.
            raise ArtworkApiUnavailable(
                f"artwork API HTTP {exc.code} for {artist} - {album_name}"
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ArtworkApiUnavailable(
                f"artwork API unavailable for {artist} - {album_name}: {exc}"
            ) from exc

        if payload is None:
            return None

        square_url = payload.get("url")
        tall_url = payload.get("url_tall")
        if not isinstance(square_url, str) or not square_url.startswith(
            ("https://", "http://")
        ):
            square_url = None
        if not isinstance(tall_url, str) or not tall_url.startswith(
            ("https://", "http://")
        ):
            tall_url = None
        if not square_url and not tall_url:
            return None

        api_artist = payload.get("artist")
        api_album = payload.get("album")
        api_artist = api_artist if isinstance(api_artist, str) else None
        api_album = api_album if isinstance(api_album, str) else None

        # Keep m8tec as the resolver, but veto one proven cache-collision class:
        # an otherwise identical album title with a different/missing standalone
        # numeric suffix at the very end (e.g. "Gangsta Art" vs "Gangsta Art 2").
        # This is intentionally not a general title matcher.
        if api_album and self._numeric_suffix_conflict(album_name, api_album):
            self._log.warning(
                "fetchanimated: rejecting numeric-suffix mismatch for {} - {} "
                "(m8tec returned {} - {})",
                artist,
                album_name,
                api_artist or "?",
                api_album,
            )
            return None

        # Do not apply any broader fetchanimated-specific title/artist matcher.
        # The m8tec /api/v1/artwork/search endpoint still owns normal Apple
        # Music release resolution and cache policy.
        return ResolverResult(
            square_url=square_url,
            tall_url=tall_url,
            api_artist=api_artist,
            api_album=api_album,
        )

    def _resolve_album(self, album: Album) -> ResolverResult | None:
        album_name = self._text(getattr(album, "album", ""))
        if not album_name:
            return None
        artist = self._search_artist(album)
        if not artist:
            return None
        title_hint = self._search_title_hint(album)
        return self._api_search(artist, album_name, title_hint)

    # ------------------------------------------------------------------
    # HLS resolution discovery and automatic variant selection
    # ------------------------------------------------------------------

    def _fetch_text_url(self, url: str) -> str | None:
        timeout = max(1, self.config["manifest_timeout"].get(int))
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.config["user_agent"].get(str).strip()
                or f"fetchanimated/{PLUGIN_VERSION}"
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            self._log.warning(
                "fetchanimated: could not read HLS manifest {}: {}", url, exc
            )
            return None

    @staticmethod
    def _parse_master_variants(text: str, master_url: str) -> list[HlsVariant]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        by_resolution: dict[tuple[int | None, int | None], HlsVariant] = {}

        for index, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            if index + 1 >= len(lines):
                continue
            next_line = lines[index + 1]
            if not next_line or next_line.startswith("#"):
                continue

            resolution_match = re.search(
                r"(?:^|[:,\s])RESOLUTION=(\d+)x(\d+)", line, re.IGNORECASE
            )
            bandwidth_match = re.search(
                r"(?:^|[:,\s])BANDWIDTH=(\d+)", line, re.IGNORECASE
            )

            width = int(resolution_match.group(1)) if resolution_match else None
            height = int(resolution_match.group(2)) if resolution_match else None
            bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
            variant = HlsVariant(
                url=urllib.parse.urljoin(master_url, next_line),
                width=width,
                height=height,
                bandwidth=bandwidth,
            )

            key = (width, height)
            existing = by_resolution.get(key)
            if existing is None or variant.bandwidth > existing.bandwidth:
                by_resolution[key] = variant

        return list(by_resolution.values())

    def _choose_variant(
        self,
        variants: list[HlsVariant],
        target_width: int,
    ) -> HlsVariant | None:
        if not variants:
            return None

        with_size = [variant for variant in variants if variant.width]
        if not with_size:
            return max(variants, key=lambda variant: variant.bandwidth)

        policy = self.resolution_policy
        if policy == "highest":
            return max(
                with_size,
                key=lambda variant: (
                    (variant.width or 0) * (variant.height or 0),
                    variant.bandwidth,
                ),
            )

        if policy == "at_most":
            eligible = [
                variant
                for variant in with_size
                if int(variant.width or 0) <= target_width
            ]
            if eligible:
                return max(
                    eligible,
                    key=lambda variant: (variant.width or 0, variant.bandwidth),
                )

        if policy == "at_least":
            eligible = [
                variant
                for variant in with_size
                if int(variant.width or 0) >= target_width
            ]
            if eligible:
                minimum_width = min(int(variant.width or 0) for variant in eligible)
                same_width = [
                    variant
                    for variant in eligible
                    if int(variant.width or 0) == minimum_width
                ]
                return max(same_width, key=lambda variant: variant.bandwidth)

        # Default and fallback: closest width. If two widths are equally far
        # away, prefer the larger Apple stream, then the higher bandwidth.
        return min(
            with_size,
            key=lambda variant: (
                abs(int(variant.width or 0) - target_width),
                -int(variant.width or 0),
                -variant.bandwidth,
            ),
        )

    def _select_hls_stream(
        self,
        master_url: str,
        target_width: int,
        variant_name: str,
    ) -> HlsVariant | None:
        """Resolve a master URL to the chosen media playlist.

        The first master manifest determines the user-facing resolution choice.
        If the selected child is itself another master, it is resolved
        recursively without changing the already chosen size unless another
        sized variant layer is present.
        """
        current_url = master_url
        selected: HlsVariant | None = None

        for depth in range(4):
            manifest = self._fetch_text_url(current_url)
            if manifest is None:
                return None

            if "#EXT-X-STREAM-INF" not in manifest:
                if selected is None:
                    selected = HlsVariant(url=current_url)
                else:
                    selected = HlsVariant(
                        url=current_url,
                        width=selected.width,
                        height=selected.height,
                        bandwidth=selected.bandwidth,
                    )
                return selected

            variants = self._parse_master_variants(manifest, current_url)
            choice = self._choose_variant(variants, target_width)
            if choice is None:
                self._log.warning(
                    "fetchanimated: {} HLS master contains no playable variant",
                    variant_name,
                )
                return None

            if selected is None or choice.width:
                selected = choice
            current_url = choice.url

        self._log.warning(
            "fetchanimated: {} HLS nesting exceeded safety limit", variant_name
        )
        return None

    # ------------------------------------------------------------------
    # FFmpeg
    # ------------------------------------------------------------------

    def _ffmpeg_available(self) -> bool:
        executable = self.ffmpeg_path
        if os.path.sep in executable:
            return os.path.isfile(executable) and os.access(executable, os.X_OK)
        return shutil.which(executable) is not None

    @staticmethod
    def _safe_unlink(path: str | None) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _run_ffmpeg(self, command: list[str], label: str) -> bool:
        timeout = max(10, self.config["ffmpeg_timeout"].get(int))
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log.warning("fetchanimated: {} failed: {}", label, exc)
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip()
            self._log.warning(
                "fetchanimated: {} exited with {}: {}",
                label,
                result.returncode,
                detail[-1500:],
            )
            return False
        return True

    def _temp_path(self, suffix: str) -> str:
        fd, path = tempfile.mkstemp(prefix="fetchanimated-", suffix=suffix)
        os.close(fd)
        self._safe_unlink(path)
        return path

    def _remux_hls_to_mp4(
        self,
        stream: HlsVariant,
        variant_name: str,
    ) -> str | None:
        if not self._ffmpeg_available():
            self._log.warning(
                "fetchanimated: ffmpeg not found at {!r}; skipping animated art",
                self.ffmpeg_path,
            )
            return None

        output = self._temp_path(f".{variant_name}.mp4")
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            stream.url,
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            output,
        ]
        if not self._run_ffmpeg(command, f"{variant_name} HLS remux"):
            self._safe_unlink(output)
            return None

        try:
            if os.path.getsize(output) < 1024:
                raise OSError("output too small")
        except OSError:
            self._log.warning(
                "fetchanimated: {} HLS remux produced no usable MP4",
                variant_name,
            )
            self._safe_unlink(output)
            return None
        return output

    def _encode_webp(
        self,
        mp4_path: str,
        variant_name: str,
    ) -> str | None:
        if not self._ffmpeg_available():
            return None
        output = self._temp_path(f".{variant_name}.webp")

        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            mp4_path,
            "-an",
            "-c:v",
            "libwebp",
            "-loop",
            "0",
            "-q:v",
            str(self.webp_quality),
            output,
        ]
        if not self._run_ffmpeg(command, f"{variant_name} WebP encode"):
            self._safe_unlink(output)
            return None

        try:
            size_bytes = os.path.getsize(output)
            if size_bytes < 1024:
                raise OSError("output too small")
            with open(output, "rb") as handle:
                header = handle.read(min(size_bytes, 4 * 1024 * 1024))
        except OSError as exc:
            self._log.warning(
                "fetchanimated: could not validate Animated WebP: {}", exc
            )
            self._safe_unlink(output)
            return None

        if not header.startswith(b"RIFF") or b"WEBP" not in header[:16]:
            self._log.warning(
                "fetchanimated: conversion did not produce a WebP file"
            )
            self._safe_unlink(output)
            return None
        if b"ANIM" not in header and b"ANMF" not in header:
            self._log.warning(
                "fetchanimated: WebP is not animated; discarding it"
            )
            self._safe_unlink(output)
            return None
        return output

    # ------------------------------------------------------------------
    # Asset preparation / placement
    # ------------------------------------------------------------------

    def _target_width_for(self, variant_name: str) -> int:
        return (
            self.square_target_width
            if variant_name == "square"
            else self.tall_target_width
        )

    @staticmethod
    def _resolver_url_for(
        resolver: ResolverResult,
        variant_name: str,
    ) -> str | None:
        return resolver.square_url if variant_name == "square" else resolver.tall_url

    def _prepare_variant_assets(
        self,
        resolver: ResolverResult,
        specs: list[AssetSpec],
        variant_name: str,
    ) -> list[PreparedAsset]:
        master_url = self._resolver_url_for(resolver, variant_name)
        if not master_url:
            return []

        stream = self._select_hls_stream(
            master_url,
            self._target_width_for(variant_name),
            variant_name,
        )
        if stream is None:
            return []

        self._log.info(
            "fetchanimated: {} target width {} -> Apple HLS {}",
            variant_name,
            self._target_width_for(variant_name),
            stream.resolution,
        )

        mp4_path = self._remux_hls_to_mp4(stream, variant_name)
        if not mp4_path:
            return []

        wants_mp4 = any(spec.format == "mp4" for spec in specs)
        wants_webp = any(spec.format == "webp" for spec in specs)
        prepared: list[PreparedAsset] = []

        try:
            if wants_webp:
                webp_path = self._encode_webp(mp4_path, variant_name)
                if webp_path:
                    for spec in specs:
                        if spec.format == "webp":
                            prepared.append(
                                PreparedAsset(
                                    path=webp_path,
                                    spec=spec,
                                    selected_resolution=stream.resolution,
                                )
                            )
                            # Only one WebP output exists per variant.
                            break

            if wants_mp4:
                for spec in specs:
                    if spec.format == "mp4":
                        prepared.append(
                            PreparedAsset(
                                path=mp4_path,
                                spec=spec,
                                selected_resolution=stream.resolution,
                            )
                        )
                        mp4_path = ""  # ownership transferred to PreparedAsset
                        break
        finally:
            # If MP4 is not a requested persistent output, it is always
            # temporary and is removed immediately after WebP encoding.
            self._safe_unlink(mp4_path)

        return prepared

    def _prepare_assets(
        self,
        album: Album,
        pending_specs: list[AssetSpec],
        resolver: ResolverResult | None = None,
    ) -> PreparedBundle | None:
        if not pending_specs:
            return PreparedBundle([])

        # Normal imports omit ``resolver`` and therefore keep the established
        # lookup behavior. Batch runs can pass the result
        # they already resolved so a successful album is not queried twice.
        if resolver is None:
            resolver = self._resolve_album(album)
        if resolver is None:
            return None

        prepared: list[PreparedAsset] = []
        try:
            for variant_name in ("square", "tall"):
                variant_specs = [
                    spec for spec in pending_specs if spec.variant == variant_name
                ]
                if not variant_specs:
                    continue
                prepared.extend(
                    self._prepare_variant_assets(
                        resolver,
                        variant_specs,
                        variant_name,
                    )
                )
        except Exception:
            for asset in prepared:
                asset.cleanup()
            raise

        return PreparedBundle(prepared)

    def _place_asset(
        self,
        album: Album,
        prepared: PreparedAsset,
        *,
        force: bool,
    ) -> bool:
        album_dir = self._album_dir(album)
        if not album_dir:
            prepared.cleanup()
            self._log.warning(
                "fetchanimated: cannot determine final album directory for {} - {}",
                self._text(getattr(album, "albumartist", "")),
                self._text(getattr(album, "album", "")),
            )
            return False

        destination = os.path.join(album_dir, prepared.spec.filename)
        if os.path.exists(destination) and not force:
            prepared.cleanup()
            return False

        try:
            os.makedirs(album_dir, exist_ok=True)
            fd, staging = tempfile.mkstemp(
                prefix=f".{prepared.spec.filename}.",
                suffix=".tmp",
                dir=album_dir,
            )
            try:
                with os.fdopen(fd, "wb") as target, open(
                    prepared.path, "rb"
                ) as source:
                    shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
                os.replace(staging, destination)
            except Exception:
                self._safe_unlink(staging)
                raise
            finally:
                prepared.cleanup()
            return True
        except Exception as exc:
            prepared.cleanup()
            self._log.warning(
                "fetchanimated: could not write {}: {}", destination, exc
            )
            return False

    def _place_bundle(
        self,
        album: Album,
        bundle: PreparedBundle,
        *,
        force: bool,
    ) -> list[PreparedAsset]:
        placed: list[PreparedAsset] = []
        remaining = list(bundle.assets)
        bundle.assets.clear()
        for prepared in remaining:
            if self._place_asset(album, prepared, force=force):
                placed.append(prepared)
        return placed

    # ------------------------------------------------------------------
    # Normal beets importer (automatic and manual album imports)
    # ------------------------------------------------------------------

    def fetch_animated(self, session: ImportSession, task: ImportTask) -> None:
        try:
            self._protect_fetchart_filesystem()
            if not task.is_album:
                return
            if task.choice_flag == importer.Action.ASIS:
                if not self.config["fetch_for_asis"].get(bool):
                    return
            elif task.choice_flag not in (
                importer.Action.APPLY,
                importer.Action.RETAG,
            ):
                return

            force = self._effective_force(False)
            pending = self._pending_specs(task.album, force=force)
            if not pending:
                return

            bundle = self._prepare_assets(task.album, pending)
            if bundle is not None and bundle.assets:
                self._prepared_tasks[task] = bundle
        except Exception as exc:
            self._log.warning(
                "fetchanimated: unexpected import-stage error; import continues: {}",
                exc,
            )

    def assign_animated(self, session: ImportSession, task: ImportTask) -> None:
        bundle = self._prepared_tasks.pop(task, None)
        if bundle is None:
            return
        try:
            self._place_bundle(
                task.album,
                bundle,
                force=self._effective_force(False),
            )
        except Exception as exc:
            bundle.cleanup()
            self._log.warning(
                "fetchanimated: placement failed; import remains successful: {}",
                exc,
            )

    # ------------------------------------------------------------------
    # Optional programmatic existing-album API
    # ------------------------------------------------------------------

    def ensure_album_assets(
        self,
        lib: Library,
        album_id: int,
        *,
        force: bool = False,
    ) -> str:
        """Ensure enabled animated sidecars for one existing beets album."""
        self._protect_fetchart_filesystem()
        if not self.config["auto"].get(bool) and not force:
            return "Animated artwork skipped: fetchanimated.auto is disabled"

        album = lib.get_album(int(album_id))
        if album is None:
            return "Animated artwork skipped: beets album not found"

        effective_force = self._effective_force(force)
        enabled = self._enabled_specs()
        if not enabled:
            return "Animated artwork skipped: no output formats are enabled"

        pending = self._pending_specs(album, force=effective_force)
        if not pending:
            return "Animated artwork: all enabled files already exist"

        try:
            bundle = self._prepare_assets(album, pending)
            if bundle is None:
                return "Animated artwork: no Apple Motion Artwork found"
            if not bundle.assets:
                return "Animated artwork: requested Apple variant unavailable"

            placed = self._place_bundle(album, bundle, force=effective_force)
            if not placed:
                return "Animated artwork: no new file saved"

            details: list[str] = []
            for asset in placed:
                destination = self._destination(album, asset.spec)
                detail = f"{asset.spec.filename} [{asset.selected_resolution}]"
                if destination:
                    try:
                        mib = os.path.getsize(destination) / (1024 * 1024)
                        detail += f" ({mib:.1f} MiB)"
                    except OSError:
                        pass
                details.append(detail)
            return "Animated artwork: saved: " + ", ".join(details)
        except Exception as exc:
            self._log.warning(
                "fetchanimated: existing-album artwork error for album {}: {}",
                album_id,
                exc,
            )
            return f"Animated artwork warning: {exc}"

    # Singular alias keeps earlier integrations backward-compatible.
    def ensure_album_asset(
        self,
        lib: Library,
        album_id: int,
        *,
        force: bool = False,
    ) -> str:
        return self.ensure_album_assets(lib, album_id, force=force)

    def cleanup_prepared(self, **kwargs: Any) -> None:
        for bundle in list(self._prepared_tasks.values()):
            bundle.cleanup()
        self._prepared_tasks.clear()

    # ------------------------------------------------------------------
    # Keep all known motion sidecars with albums on later beet copy/move runs
    # ------------------------------------------------------------------

    def item_copied(self, item: Any, source: Any, destination: Any) -> None:
        try:
            source_path = util.syspath(source)
            destination_path = util.syspath(destination)
            source_dir = os.path.dirname(source_path)
            destination_dir = os.path.dirname(destination_path)
            if not source_dir or not destination_dir or source_dir == destination_dir:
                return

            os.makedirs(destination_dir, exist_ok=True)
            for filename in dict.fromkeys(spec.filename for spec in self._all_specs()):
                source_asset = os.path.join(source_dir, filename)
                if not os.path.isfile(source_asset):
                    continue
                destination_asset = os.path.join(destination_dir, filename)
                if os.path.exists(destination_asset):
                    continue
                shutil.copy2(source_asset, destination_asset)
        except Exception as exc:
            self._log.warning(
                "fetchanimated: sidecar copy failed; item copy continues: {}",
                exc,
            )

    def item_moved(self, item: Any, source: Any, destination: Any) -> None:
        try:
            source_path = util.syspath(source)
            destination_path = util.syspath(destination)
            source_dir = os.path.dirname(source_path)
            destination_dir = os.path.dirname(destination_path)
            if not source_dir or not destination_dir or source_dir == destination_dir:
                return

            audio_extensions = {
                ".aac",
                ".aif",
                ".aiff",
                ".alac",
                ".ape",
                ".flac",
                ".m4a",
                ".mp3",
                ".ogg",
                ".opus",
                ".wav",
                ".wma",
                ".wv",
            }
            try:
                source_entries = os.listdir(source_dir)
            except OSError:
                source_entries = []

            remaining_audio = any(
                os.path.isfile(os.path.join(source_dir, name))
                and Path(name).suffix.casefold() in audio_extensions
                for name in source_entries
            )
            if remaining_audio:
                return

            os.makedirs(destination_dir, exist_ok=True)
            for filename in dict.fromkeys(spec.filename for spec in self._all_specs()):
                source_asset = os.path.join(source_dir, filename)
                if not os.path.isfile(source_asset):
                    continue
                destination_asset = os.path.join(destination_dir, filename)
                if os.path.exists(destination_asset):
                    continue
                shutil.move(source_asset, destination_asset)
        except Exception as exc:
            self._log.warning(
                "fetchanimated: sidecar move failed; item move continues: {}",
                exc,
            )

    # ------------------------------------------------------------------
    # Manual command / full-library backfill
    # ------------------------------------------------------------------

    def _dry_run_variant(
        self,
        resolver: ResolverResult,
        variant_name: str,
    ) -> HlsVariant | None:
        master_url = self._resolver_url_for(resolver, variant_name)
        if not master_url:
            return None
        return self._select_hls_stream(
            master_url,
            self._target_width_for(variant_name),
            variant_name,
        )

    def _album_label(self, album: Album) -> str:
        return (
            f"{self._text(getattr(album, 'albumartist', ''))} - "
            f"{self._text(getattr(album, 'album', ''))}"
        ).strip(" -")

    def _load_retry_error_labels(self, report_path: str) -> list[str] | None:
        """Read ERRORS from the most recent eligible fetchanimated report.

        Labels stay intact and are matched against the exact current Beets
        ``Album Artist - Album`` label. Ambiguous or missing current-library
        matches are never guessed.
        """
        path = os.path.expanduser(report_path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            self._log.warning(
                "fetchanimated: cannot read retry source report {}: {}",
                path,
                exc,
            )
            ui.print_(f"fetchanimated: cannot read retry source report: {path}")
            return None

        report_starts = [
            index
            for index, line in enumerate(lines)
            if line.startswith("fetchanimated v")
            and (
                " full-library report" in line
                or " retry-errors report" in line
            )
        ]
        if not report_starts:
            ui.print_("fetchanimated: retry source contains no eligible report")
            return None

        current = lines[report_starts[-1] :]
        error_index = next(
            (
                index
                for index, line in enumerate(current)
                if line.startswith("ERRORS (")
            ),
            None,
        )
        if error_index is None:
            return []

        labels: list[str] = []
        seen: set[str] = set()
        for line in current[error_index + 1 :]:
            if not line.strip():
                break
            if not line.startswith("- ") or line == "- none":
                continue
            body = line[2:]
            if " -- " not in body:
                continue
            label, _reason = body.rsplit(" -- ", 1)
            label = label.strip()
            if label and label not in seen:
                labels.append(label)
                seen.add(label)
        return labels

    def commands(self) -> list[ui.Subcommand]:
        cmd = ui.Subcommand(
            "fetchanimated",
            help="download configured Apple Music animated artwork sidecars",
        )
        cmd.parser.add_option(
            "-f",
            "--force",
            dest="force",
            action="store_true",
            default=False,
            help="replace existing files for currently enabled output formats",
        )
        cmd.parser.add_option(
            "-n",
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=False,
            help="resolve artwork and HLS resolution only; write nothing",
        )
        cmd.parser.add_option(
            "--limit",
            dest="limit",
            type="int",
            default=0,
            help="process at most N matching albums (0 = unlimited)",
        )
        cmd.parser.add_option(
            "--full-library",
            dest="full_library",
            action="store_true",
            default=False,
            help="process the complete Beets album library and write its report",
        )
        cmd.parser.add_option(
            "--retry-errors",
            dest="retry_errors_log",
            metavar="PATH",
            default=None,
            help=(
                "retry only albums listed in the ERRORS section of the most "
                "recent full-library or retry-errors report at PATH"
            ),
        )

        def func(lib: Library, opts: Any, args: list[str]) -> None:
            limit = max(0, int(opts.limit or 0))
            full_library = bool(opts.full_library)
            retry_errors_log = self._text(opts.retry_errors_log)

            if retry_errors_log:
                if full_library:
                    ui.print_(
                        "fetchanimated: --retry-errors cannot be combined with --full-library"
                    )
                    return
                if args:
                    ui.print_(
                        "fetchanimated: --retry-errors cannot be combined with a Beets query"
                    )
                    return

                retry_labels = self._load_retry_error_labels(retry_errors_log)
                if retry_labels is None:
                    return
                if not retry_labels:
                    ui.print_(
                        "fetchanimated: the most recent eligible report contains no errors"
                    )
                    return

                retry_set = set(retry_labels)
                current_matches: dict[str, list[Album]] = {
                    label: [] for label in retry_labels
                }
                for album in lib.albums():
                    label = self._album_label(album)
                    if label in retry_set:
                        current_matches[label].append(album)

                retry_albums: list[Album] = []
                unresolved_labels: list[str] = []
                for label in retry_labels:
                    matches = current_matches.get(label, [])
                    if len(matches) == 1:
                        retry_albums.append(matches[0])
                    else:
                        unresolved_labels.append(label)
                        if len(matches) > 1:
                            self._log.warning(
                                "fetchanimated: retry label {!r} matches {} current "
                                "Beets albums; skipping ambiguous retry",
                                label,
                                len(matches),
                            )

                if unresolved_labels:
                    self._log.warning(
                        "fetchanimated: {} retry-error album label(s) could not be "
                        "matched uniquely to the current Beets library",
                        len(unresolved_labels),
                    )

                ui.print_(
                    f"fetchanimated: retrying {len(retry_albums)} album(s) from "
                    f"{retry_errors_log}"
                )
                if unresolved_labels:
                    ui.print_(
                        f"fetchanimated: {len(unresolved_labels)} logged error album(s) "
                        "could not be matched uniquely to the current library"
                    )

                self.batch_fetch(
                    lib,
                    retry_albums,
                    force=bool(opts.force),
                    dry_run=bool(opts.dry_run),
                    limit=limit,
                    full_library_run=False,
                    limit_run=False,
                    query_args=[],
                    retry_run=True,
                    retry_source_log=retry_errors_log,
                    retry_requested=len(retry_labels),
                    retry_unmatched=unresolved_labels,
                )
                return

            if full_library and args:
                ui.print_(
                    "fetchanimated: --full-library cannot be combined with a Beets query"
                )
                return
            if full_library and limit:
                ui.print_(
                    "fetchanimated: --full-library cannot be combined with --limit"
                )
                return

            # Keep the old queryless command as a compatibility alias for a
            # full-library run. The explicit --full-library form is preferred.
            full_library_run = full_library or (not args and limit == 0)
            self.batch_fetch(
                lib,
                lib.albums(args),
                force=bool(opts.force),
                dry_run=bool(opts.dry_run),
                limit=limit,
                full_library_run=full_library_run,
                limit_run=(limit > 0),
                query_args=list(args),
            )

        cmd.func = func
        return [cmd]

    def _write_full_library_log(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        dry_run: bool,
        processed: int,
        saved_albums: int,
        saved_files: int,
        complete_albums: int,
        no_art_entries: list[tuple[str, str]],
        partial_entries: list[tuple[str, str]],
        error_entries: list[tuple[str, str]],
    ) -> str | None:
        """Append one human-readable report for a full-library CLI run.

        Logging is deliberately outside the normal per-album code path. Failure
        to write the report never changes artwork results or the command status.
        An empty ``full_library_log`` setting disables this report.
        """
        raw_path = self.config["full_library_log"].get(str).strip()
        if not raw_path:
            return None
        path = os.path.expanduser(raw_path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            enabled_names = ", ".join(spec.filename for spec in self._enabled_specs())
            lines = [
                "=" * 72,
                f"fetchanimated v{PLUGIN_VERSION} full-library report",
                f"started:  {started_at.astimezone().isoformat(timespec='seconds')}",
                f"finished: {finished_at.astimezone().isoformat(timespec='seconds')}",
                f"mode: {'dry-run' if dry_run else 'write'}",
                f"enabled outputs: {enabled_names or '-'}",
                "",
                f"processed albums: {processed}",
                f"albums with new artwork: {saved_albums}",
                f"saved/updated files: {saved_files}",
                f"skipped/already complete albums: {complete_albums}",
                f"not found/requested artwork unavailable: {len(no_art_entries)}",
                f"partially completed albums: {len(partial_entries)}",
                f"errors: {len(error_entries)}",
                "",
            ]

            lines.append(
                f"NOT FOUND / REQUESTED ARTWORK UNAVAILABLE ({len(no_art_entries)})"
            )
            if no_art_entries:
                lines.extend(f"- {label} -- {reason}" for label, reason in no_art_entries)
            else:
                lines.append("- none")

            if partial_entries:
                lines.extend(["", f"PARTIAL ({len(partial_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in partial_entries)

            if error_entries:
                lines.extend(["", f"ERRORS ({len(error_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in error_entries)

            lines.append("")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return path
        except Exception as exc:
            self._log.warning(
                "fetchanimated: could not write full-library report {}: {}",
                path,
                exc,
            )
            return None

    def _write_limit_log(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        dry_run: bool,
        limit: int,
        query_args: list[str],
        processed: int,
        saved_albums: int,
        saved_files: int,
        complete_albums: int,
        no_art_entries: list[tuple[str, str]],
        partial_entries: list[tuple[str, str]],
        error_entries: list[tuple[str, str]],
    ) -> str | None:
        """Append one human-readable report for an explicit --limit run.

        Report writing is deliberately isolated from artwork processing. A
        failure to write this file never changes any artwork result.
        """
        raw_path = self.config["limit_log"].get(str).strip()
        if not raw_path:
            return None
        path = os.path.expanduser(raw_path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            enabled_names = ", ".join(spec.filename for spec in self._enabled_specs())
            query_text = " ".join(query_args).strip() or "<all albums>"
            lines = [
                "=" * 72,
                f"fetchanimated v{PLUGIN_VERSION} limit report",
                f"started:  {started_at.astimezone().isoformat(timespec='seconds')}",
                f"finished: {finished_at.astimezone().isoformat(timespec='seconds')}",
                f"mode: {'dry-run' if dry_run else 'write'}",
                f"limit: {limit}",
                f"query: {query_text}",
                f"enabled outputs: {enabled_names or '-'}",
                "",
                f"processed albums: {processed}",
                f"albums with new artwork: {saved_albums}",
                f"saved/updated files: {saved_files}",
                f"skipped/already complete albums: {complete_albums}",
                f"not found/requested artwork unavailable: {len(no_art_entries)}",
                f"partially completed albums: {len(partial_entries)}",
                f"errors: {len(error_entries)}",
                "",
            ]

            lines.append(
                f"NOT FOUND / REQUESTED ARTWORK UNAVAILABLE ({len(no_art_entries)})"
            )
            if no_art_entries:
                lines.extend(f"- {label} -- {reason}" for label, reason in no_art_entries)
            else:
                lines.append("- none")

            if partial_entries:
                lines.extend(["", f"PARTIAL ({len(partial_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in partial_entries)

            if error_entries:
                lines.extend(["", f"ERRORS ({len(error_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in error_entries)

            lines.append("")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return path
        except Exception as exc:
            self._log.warning(
                "fetchanimated: could not write limit report {}: {}",
                path,
                exc,
            )
            return None

    def _write_retry_errors_log(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        dry_run: bool,
        source_log: str,
        requested: int,
        limit: int,
        unmatched_labels: list[str],
        processed: int,
        saved_albums: int,
        saved_files: int,
        complete_albums: int,
        no_art_entries: list[tuple[str, str]],
        partial_entries: list[tuple[str, str]],
        error_entries: list[tuple[str, str]],
    ) -> str | None:
        """Append one report for a retry of prior batch API errors."""
        raw_path = self.config["retry_errors_log"].get(str).strip()
        if not raw_path:
            return None
        path = os.path.expanduser(raw_path)
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            enabled_names = ", ".join(spec.filename for spec in self._enabled_specs())
            lines = [
                "=" * 72,
                f"fetchanimated v{PLUGIN_VERSION} retry-errors report",
                f"started:  {started_at.astimezone().isoformat(timespec='seconds')}",
                f"finished: {finished_at.astimezone().isoformat(timespec='seconds')}",
                f"mode: {'dry-run' if dry_run else 'write'}",
                f"source log: {source_log}",
                f"source error albums: {requested}",
                f"retry limit: {limit if limit else 'unlimited'}",
                f"unresolved current-library labels: {len(unmatched_labels)}",
                f"enabled outputs: {enabled_names or '-'}",
                "",
                f"processed albums: {processed}",
                f"albums with new artwork: {saved_albums}",
                f"saved/updated files: {saved_files}",
                f"skipped/already complete albums: {complete_albums}",
                f"not found/requested artwork unavailable: {len(no_art_entries)}",
                f"partially completed albums: {len(partial_entries)}",
                f"errors: {len(error_entries)}",
                "",
            ]

            lines.append(
                f"NOT FOUND / REQUESTED ARTWORK UNAVAILABLE ({len(no_art_entries)})"
            )
            if no_art_entries:
                lines.extend(f"- {label} -- {reason}" for label, reason in no_art_entries)
            else:
                lines.append("- none")

            if partial_entries:
                lines.extend(["", f"PARTIAL ({len(partial_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in partial_entries)

            if error_entries:
                lines.extend(["", f"ERRORS ({len(error_entries)})"])
                lines.extend(f"- {label} -- {reason}" for label, reason in error_entries)

            if unmatched_labels:
                lines.extend(["", f"UNRESOLVED ({len(unmatched_labels)})"])
                lines.extend(f"- {label}" for label in unmatched_labels)

            lines.append("")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            return path
        except Exception as exc:
            self._log.warning(
                "fetchanimated: could not write retry-errors report {}: {}",
                path,
                exc,
            )
            return None

    def batch_fetch(
        self,
        lib: Library,
        albums: Iterable[Album],
        *,
        force: bool = False,
        dry_run: bool = False,
        limit: int = 0,
        full_library_run: bool = False,
        limit_run: bool = False,
        query_args: list[str] | None = None,
        retry_run: bool = False,
        retry_source_log: str = "",
        retry_requested: int = 0,
        retry_unmatched: list[str] | None = None,
    ) -> None:
        self._protect_fetchart_filesystem()
        effective_force = self._effective_force(force)
        delay = max(0.0, self.config["batch_delay_seconds"].get(float))
        enabled = self._enabled_specs()

        if not enabled:
            ui.print_("fetchanimated: no output formats are enabled")
            return

        started_at = datetime.now(timezone.utc)
        processed = 0
        saved_albums = 0
        saved_files = 0
        complete_albums = 0
        no_art_albums = 0
        partial_albums = 0
        error_albums = 0
        no_art_entries: list[tuple[str, str]] = []
        partial_entries: list[tuple[str, str]] = []
        error_entries: list[tuple[str, str]] = []

        for album in albums:
            if limit and processed >= limit:
                break
            if processed and delay:
                time.sleep(delay)
            processed += 1

            label = self._album_label(album)

            pending = self._pending_specs(album, force=effective_force)
            if not pending:
                complete_albums += 1
                ui.print_(f"{label}: all enabled animated artwork files exist")
                continue

            try:
                resolver = self._resolve_album(album)
                if resolver is None:
                    no_art_albums += 1
                    no_art_entries.append((label, "no Apple Motion Artwork found"))
                    ui.print_(f"{label}: no Apple Motion Artwork found")
                    continue

                if dry_run:
                    planned_any = False
                    for variant_name in ("square", "tall"):
                        specs = [
                            spec for spec in pending if spec.variant == variant_name
                        ]
                        if not specs:
                            continue
                        stream = self._dry_run_variant(resolver, variant_name)
                        if stream is None:
                            ui.print_(
                                f"{label}: {variant_name} variant unavailable"
                            )
                            continue
                        planned_any = True
                        names = ", ".join(spec.filename for spec in specs)
                        ui.print_(
                            f"{label}: {variant_name} target "
                            f"{self._target_width_for(variant_name)} -> "
                            f"{stream.resolution}; would create {names}"
                        )
                    if not planned_any:
                        no_art_albums += 1
                        no_art_entries.append(
                            (label, "requested Apple Motion Artwork variant unavailable")
                        )
                    continue

                bundle = self._prepare_assets(
                    album,
                    pending,
                    resolver=resolver,
                )
                if bundle is None or not bundle.assets:
                    no_art_albums += 1
                    no_art_entries.append(
                        (label, "requested Apple Motion Artwork variant unavailable")
                    )
                    ui.print_(
                        f"{label}: requested Apple Motion Artwork variant unavailable"
                    )
                    continue

                placed = self._place_bundle(
                    album,
                    bundle,
                    force=effective_force,
                )
                if not placed:
                    partial_albums += 1
                    partial_entries.append((label, "no new animated artwork file saved"))
                    ui.print_(f"{label}: no new animated artwork file saved")
                    continue

                saved_albums += 1
                saved_files += len(placed)
                placed_keys = {asset.spec.key for asset in placed}
                missing_after = [
                    spec for spec in pending if spec.key not in placed_keys
                ]
                if missing_after:
                    partial_albums += 1
                    missing_names = ", ".join(spec.filename for spec in missing_after)
                    partial_entries.append(
                        (label, f"missing requested output(s): {missing_names}")
                    )

                details: list[str] = []
                for asset in placed:
                    destination = self._destination(album, asset.spec)
                    detail = f"{asset.spec.filename} [{asset.selected_resolution}]"
                    if destination:
                        try:
                            mib = os.path.getsize(destination) / (1024 * 1024)
                            detail += f" ({mib:.1f} MiB)"
                        except OSError:
                            pass
                    details.append(detail)
                ui.print_(f"{label}: saved " + ", ".join(details))

            except ArtworkApiUnavailable as exc:
                error_albums += 1
                error_entries.append((label, str(exc)))
                self._log.warning(
                    "fetchanimated: API error for {}; this album is skipped: {}",
                    label,
                    exc,
                )
                ui.print_(f"{label}: artwork API error; album skipped")
                backoff = max(
                    0.0, self.config["api_error_backoff_seconds"].get(float)
                )
                if backoff:
                    time.sleep(backoff)
            except Exception as exc:
                error_albums += 1
                error_entries.append((label, str(exc)))
                self._log.warning(
                    "fetchanimated: batch error for {}: {}", label, exc
                )
                ui.print_(f"{label}: error; skipped")

        ui.print_("")
        ui.print_(f"fetchanimated v{PLUGIN_VERSION}: processed {processed} album(s)")
        ui.print_(f"  albums with new artwork: {saved_albums}")
        ui.print_(f"  saved/updated files: {saved_files}")
        ui.print_(f"  already complete albums: {complete_albums}")
        ui.print_(f"  no requested artwork: {no_art_albums}")
        ui.print_(f"  partially completed albums: {partial_albums}")
        ui.print_(f"  errors: {error_albums}")

        if retry_run:
            report_path = self._write_retry_errors_log(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                dry_run=dry_run,
                source_log=retry_source_log,
                requested=retry_requested,
                limit=limit,
                unmatched_labels=list(retry_unmatched or []),
                processed=processed,
                saved_albums=saved_albums,
                saved_files=saved_files,
                complete_albums=complete_albums,
                no_art_entries=no_art_entries,
                partial_entries=partial_entries,
                error_entries=error_entries,
            )
            if report_path:
                ui.print_(f"  retry-errors log: {report_path}")
        elif full_library_run:
            report_path = self._write_full_library_log(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                dry_run=dry_run,
                processed=processed,
                saved_albums=saved_albums,
                saved_files=saved_files,
                complete_albums=complete_albums,
                no_art_entries=no_art_entries,
                partial_entries=partial_entries,
                error_entries=error_entries,
            )
            if report_path:
                ui.print_(f"  full-library log: {report_path}")
        elif limit_run:
            report_path = self._write_limit_log(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                dry_run=dry_run,
                limit=limit,
                query_args=list(query_args or []),
                processed=processed,
                saved_albums=saved_albums,
                saved_files=saved_files,
                complete_albums=complete_albums,
                no_art_entries=no_art_entries,
                partial_entries=partial_entries,
                error_entries=error_entries,
            )
            if report_path:
                ui.print_(f"  limit log: {report_path}")
