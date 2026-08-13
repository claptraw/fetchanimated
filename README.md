# fetchanimated

`fetchanimated` is a standalone [beets](https://beets.io/) plugin that downloads Apple Music motion artwork and stores it as animated sidecar files next to an album.

It is deliberately independent from beets' static cover-art state: it does **not** require or modify `cover.jpg`, `album.artpath`, or embedded artwork.

## Features

- Fetches square and tall Apple Music motion artwork through the public m8tec artwork resolver.
- Reads Apple HLS master manifests and automatically selects an actually available stream resolution.
- Supports `nearest`, `at_most`, `at_least`, and `highest` resolution policies.
- Remuxes the selected HLS video stream losslessly to MP4 when MP4 output is enabled.
- Encodes animated WebP with FFmpeg/libwebp while preserving the selected stream's native frame cadence.
- Never overwrites an existing sidecar unless `overwrite: yes` or `--force` is used.
- Integrates with normal beets album imports through standard import-pipeline hooks.
- Provides a `beet fetchanimated` command for queries, dry runs, limited batches, and full-library backfills.
- Can move known motion-art sidecars along with albums during later `beet move` operations.
- Optionally protects beets FetchArt from considering animated `cover*.webp` sidecars as static cover candidates.

## Requirements

- Python 3.10+
- beets 2.x
- FFmpeg available on `PATH` (or configured with an explicit executable path)
- An FFmpeg build with the `libwebp` encoder when WebP output is enabled
- Network access to the configured artwork resolver and the HLS URLs it returns

## Installing FFmpeg

FFmpeg is installed separately from this plugin.

### Windows

Using Windows Package Manager (`winget`):

```powershell
winget install --id Gyan.FFmpeg --exact
```

Open a new terminal after installation and verify it:

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders | findstr libwebp
```

### macOS

Using [Homebrew](https://brew.sh/):

```bash
brew install ffmpeg
```

Then verify it:

```bash
ffmpeg -version
ffmpeg -hide_banner -encoders | grep libwebp
```

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install ffmpeg
```

Then verify it:

```bash
ffmpeg -version
ffmpeg -hide_banner -encoders | grep libwebp
```

If `libwebp` is not listed, install an FFmpeg build that includes the `libwebp` encoder or disable the WebP outputs and use MP4 output only.

## Installation

The plugin must be installed into the same Python environment as beets. After installation, add `fetchanimated` to the `plugins` list in your beets configuration.

### Option A: install the release wheel (recommended)

Download the `.whl` file from the matching GitHub Release and install it:

```bash
python -m pip install ./beets_fetchanimated-0.1-py3-none-any.whl
```

If beets is managed with `pipx`, inject the wheel into the existing beets environment instead:

```bash
pipx inject beets ./beets_fetchanimated-0.1-py3-none-any.whl
```

Then enable the plugin in your beets configuration:

```yaml
plugins: fetchart embedart fetchanimated
```

`fetchart` and `embedart` are optional; they are shown only as a common setup.

Verify that beets can load the plugin:

```bash
beet help fetchanimated
```

### Option B: install from a source checkout

From the repository root:

```bash
python -m pip install .
```

Or, when beets is managed with `pipx`:

```bash
pipx inject beets .
```

Then add `fetchanimated` to the beets plugin list as shown above.

### Option C: manual single-file installation

Copy:

```text
src/beetsplug/fetchanimated.py
```

into a directory of your choice, for example `~/beets-plugins/`, and configure beets:

```yaml
pluginpath:
  - ~/beets-plugins

plugins: fetchanimated
```

If you use the versioned standalone file from a GitHub Release, rename `fetchanimated-0.1.py` to `fetchanimated.py` before placing it in `pluginpath`.

## Configuration

Start with [`config.example.yaml`](config.example.yaml). A minimal configuration is:

```yaml
plugins: fetchanimated

fetchanimated:
  auto: yes
  save_square_webp: yes
  ffmpeg: ffmpeg
```

The default output is `cover.webp` in each album directory.

### Output formats

Each output can be enabled independently:

```yaml
fetchanimated:
  save_square_webp: yes
  save_square_mp4: no
  save_tall_mp4: no
  save_tall_webp: no
```

Default filenames:

| Variant | Format | Filename |
|---|---|---|
| square | WebP | `cover.webp` |
| square | MP4 | `cover.mp4` |
| tall | MP4 | `cover-tall.mp4` |
| tall | WebP | `cover-tall.webp` |

Filenames must be plain filenames, not paths. Invalid values fall back to the built-in safe defaults.

### Resolution selection

```yaml
fetchanimated:
  square_target_width: 768
  tall_target_width: 830
  resolution_policy: nearest
```

Policies:

- `nearest`: choose the available width closest to the target; ties prefer the larger stream.
- `at_most`: prefer the largest available stream not wider than the target; otherwise fall back to `nearest`.
- `at_least`: prefer the smallest available stream at least as wide as the target; otherwise fall back to `nearest`.
- `highest`: always choose the highest-resolution stream advertised by the HLS master.

The plugin selects from the resolutions Apple actually advertises in the HLS master; it does not upscale the video spatially.

### Existing files

Existing sidecars are preserved by default:

```yaml
fetchanimated:
  overwrite: no
```

Set `overwrite: yes` to replace enabled outputs automatically, or use `--force` for one manual command.

### Optional report files

Report-file output is disabled by default so the plugin has no machine-specific path assumptions:

```yaml
fetchanimated:
  full_library_log: ""
  limit_log: ""
```

Set either option to any writable path if persistent batch reports are desired.

## Automatic imports

With `auto: yes`, the plugin participates only in album imports. It prepares motion artwork during the import pipeline and places the prepared files after beets finishes its filesystem manipulation for that import task.

By default, `asis` imports are skipped. Enable them with:

```yaml
fetchanimated:
  fetch_for_asis: yes
```

Animated-art failures are logged as warnings and do not deliberately turn an otherwise successful beets import into a failed import.

## Command line

### Query selected albums

```bash
beet fetchanimated artist:"Daft Punk"
```

Any normal beets album query can be used.

### Dry run

Resolve artwork and HLS stream selection without writing files:

```bash
beet fetchanimated --dry-run artist:"Daft Punk"
```

### Force replacement

Replace existing files for the currently enabled outputs:

```bash
beet fetchanimated --force artist:"Daft Punk"
```

### Limit a batch

```bash
beet fetchanimated --limit 10
```

If `limit_log` is configured, an explicit `--limit` run appends a report there.

### Full library

```bash
beet fetchanimated --full-library
```

A queryless `beet fetchanimated` is also accepted as a compatibility alias for a full-library run.

If `full_library_log` is configured, the run appends a human-readable report with saved, complete, unavailable, partial, and error counts.

## Optional programmatic API

Advanced integrations can ask the loaded plugin to ensure sidecars for an album that already exists in the beets library:

```python
result = plugin.ensure_album_assets(lib, album_id, force=False)
```

The helper uses only the standard beets `Library` object and album ID. It is not required for normal imports or for the `beet fetchanimated` command. The singular `ensure_album_asset(...)` alias is retained for backward compatibility.

## Interaction with FetchArt

beets FetchArt can consider local `cover.*` files as static artwork candidates. Because the default animated sidecar is `cover.webp`, `fetchanimated` can narrowly filter its configured animated WebP filenames from FetchArt's **filesystem** source:

```yaml
fetchanimated:
  protect_fetchart_filesystem: yes
```

This does not modify `cover.jpg`, `album.artpath`, embedded images, or FetchArt's online sources.

Disable the protection only if you intentionally want FetchArt to see those animated WebP files:

```yaml
fetchanimated:
  protect_fetchart_filesystem: no
```

## Moving albums

With the default:

```yaml
fetchanimated:
  move_with_album: yes
```

when beets moves the final audio file out of an old album directory, the plugin moves any known configured motion-art sidecars to the new album directory if the destination file does not already exist.

## API and rate limiting

The default resolver is:

```yaml
fetchanimated:
  api_url: https://artwork.m8tec.top
```

The service is external to this project. Availability, response behavior, and rate limits can change independently of the plugin.

The plugin spaces resolver searches using `api_request_delay_seconds` and backs off after resolver transport/service errors using `api_error_backoff_seconds`.

## What the plugin does not do

- It does not modify audio metadata.
- It does not modify album identity, track/disc numbering, paths, or filenames of audio files.
- It does not replace beets FetchArt or EmbedArt.
- It does not use static cover art as an input or dependency.
- It does not upscale video to the configured target width; it chooses an existing Apple HLS variant.

## Troubleshooting

Run beets verbosely:

```bash
beet -vv fetchanimated --dry-run artist:"Daft Punk"
```

Common causes of skipped output are:

- no motion artwork returned for the release;
- the requested square/tall variant is unavailable;
- the artwork resolver is temporarily unavailable;
- an HLS manifest cannot be read;
- FFmpeg is missing or not executable;
- the installed FFmpeg lacks `libwebp` when WebP output is enabled;
- the album directory is not writable.

## Development

Install the project with development dependencies:

```bash
python -m pip install -e ".[dev]"
pytest
python -m build
```

The test suite is intentionally network-free. It checks core HLS parsing/selection, configuration behavior, package version consistency, and guards against reintroducing machine-specific or private configuration into the public source tree.

## Credits

Animated-artwork discovery is powered by the public [m8tec Apple Music Animated Artworks](https://github.com/m8tec/apple-music-animated-artworks) resolver at [`artwork.m8tec.top`](https://artwork.m8tec.top/). Thanks to m8tec for making the resolver available and documenting the Apple Music animated-artwork workflow.

This project is independent from m8tec and is not affiliated with or endorsed by m8tec.

## Development disclosure

AI-assisted tools were used during development and documentation. All AI-generated or AI-suggested changes included in releases were reviewed and tested by the maintainer. Responsibility for the released code remains with the maintainer.

## License

MIT. See [`LICENSE`](LICENSE). The MIT License applies to this project's software only; it does not grant rights to third-party artwork, trademarks, services, or other content.

## Legal and third-party services

This is an independent, unofficial project and is not affiliated with or endorsed by Apple, Apple Music, beets, or m8tec.

The plugin uses the third-party m8tec resolver and accesses media URLs returned by that service. Those external services and the artwork they expose are not part of this project and may be subject to their own terms, copyright, access restrictions, rate limits, and availability.

Apple's Media Services Terms contain restrictions on automated scraping or extraction of service content and on circumventing security technologies. Users are responsible for reviewing and complying with the terms and laws applicable to their use and jurisdiction. Nothing in this repository grants rights to Apple Music content. This project is not intended to circumvent DRM, authentication, or other access controls.
