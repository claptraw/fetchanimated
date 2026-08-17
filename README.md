# 🌀 fetchanimated

fetchanimated is a [beets](https://beets.io/) plugin for downloading Apple Music animated album artwork directly into the matching album folders in your existing music library.

It uses the public m8tec artwork API and supports square and tall artwork as animated WebP and/or MP4 files.

<div align="center">

<table>
  <tr>
    <td align="center" width="50%"><strong>Square Cover</strong></td>
    <td align="center" width="50%"><strong>Tall Cover</strong></td>
  </tr>
  <tr>
    <td align="center">
      <img src=".github/assets/fetchanimated-square.gif"
           alt="Square animated cover preview"
           style="max-width: 100%; border-radius: 8px;">
    </td>
    <td align="center">
      <img src=".github/assets/fetchanimated-tall.gif"
           alt="Tall animated cover preview"
           style="max-width: 100%; border-radius: 8px;">
    </td>
  </tr>
</table>

</div>

## Features

- Manual or automatic animated-artwork downloads via the public m8tec API.
- **Square** and **tall** artwork variants.
- **Animated WebP**, **MP4**, or any combination of formats and variants.
- Configurable target resolution and WebP quality; up to **2160×2160** square and **2048×2732** tall.
- Smart source selection: exact requested resolution when available, otherwise the nearest matching size.
- Search for a specific album, artist + album, all albums by an artist, or artworks for your entire library.
- Automatic artwork fetching when beets imports a new album.
- Saves directly into the album folder
- Works seamlessly with Navidrome and clients supporting animated cover art (e.g. Narjo or Sonamp).
- Existing artwork is preserved unless you explicitly overwrite it.
- Dry-run mode to preview matches and selected resolutions without writing files.
- Animated artwork can follow albums when beets moves the albums.
- Works alongside beets `fetchart` without replacing your normal static cover.

## Prerequisites: beets and up-to-date beets library database file

fetchanimated is a plugin for [beets](https://beets.io/) and requires a working beets installation to function.

If you want to use this album artwork functionality as a standalone tool, I recommend using [m8tec's apple music animated artworks](https://github.com/m8tec/apple-music-animated-artworks).

fetchanimated relies on the **beets library database**. It does not scan your local music folders to discover albums.

The albums you want to process need to exist in your beets database, and the paths stored by beets should point to the actual album directories on disk.

If you are unsure whether the database still matches your files, first preview a beets update without moving files:

```bash
beet update -p -M
```

If the preview looks correct, apply it with:

```bash
beet update -M
```
An up-to-date beets database file is necessary to make sure that fetchanimated can move the animated artwork in the correct album folder on its own.

`beet update` refreshes existing library entries from files and reflects deletions. It does **not** discover brand-new files or automatically find files that were manually moved to an unknown location outside beets; those need to be imported or corrected separately.

## Installation

### 1. Install the plugin file - recommended method

Locate the directory that contains your beets `config.yaml` and create a `beetsplug` folder next to it:

```text
beets-config/
├── config.yaml
└── beetsplug/
    └── fetchanimated.py
```

Copy `src/beetsplug/fetchanimated.py` from this repository into that folder.

Then add a `pluginpath` entry to your **`config.yaml`** so beets knows where to find the plugin. Use the path that is valid **inside the environment where beets runs**:

```yaml
pluginpath:
  - /path/to/beets-config/beetsplug
```

If you already have a `pluginpath`, add the new directory to the existing list instead of replacing it.

Next, add `fetchanimated` to your existing `plugins` line. For example:

```yaml
plugins: fetchart embedart fetchanimated
```

Do not remove your existing plugins; just add `fetchanimated`.

Finally, copy the complete `fetchanimated:` section from [`config.example.yaml`](config.example.yaml) into your `config.yaml`. The full configuration with explaining comments is also shown below.

### 2. Install FFmpeg

FFmpeg is required for the MP4/HLS handling and for creating animated WebP files.

#### Debian / Ubuntu

```bash
sudo apt update
sudo apt install ffmpeg
```

#### macOS with Homebrew

```bash
brew install ffmpeg
```

#### Windows with WinGet

```powershell
winget install --id Gyan.FFmpeg -e
```

If beets runs inside Docker or another container, FFmpeg must also be available **inside that container/environment**. Installing FFmpeg only on the host is not sufficient.

Verify the installation in the container:

```bash
ffmpeg -version
```

For WebP output, also verify that the FFmpeg build includes `libwebp`.

Linux/macOS:

```bash
ffmpeg -hide_banner -encoders | grep libwebp
```

Windows:

```powershell
ffmpeg -hide_banner -encoders | findstr libwebp
```

### 3. Verify that beets loads fetchanimated

If beets is running as a long-lived container or service, restart that container/service after adding the plugin. A normal command-line beets installation loads plugins when `beet` starts, so no separate restart is usually required.

Then run:

```bash
beet version
```

`fetchanimated` should appear in the list of loaded plugins.

## Configuration

Add fetchanimated to your existing plugin list and copy the complete configuration block below into `config.yaml`. Adjust the values you want to change, but keep the full section so all available options remain visible.

```yaml
# Add fetchanimated to your existing plugin list, for example:
plugins: fetchart embedart fetchanimated

# Required when fetchanimated.py is installed manually.
pluginpath:
  - /path/to/beets-config/beetsplug

fetchanimated:
  # Fetch animated artwork automatically for album imports accepted by beets.
  auto: yes

  # Also fetch when an album is imported "as-is" without autotagging changes.
  fetch_for_asis: no

  # Public artwork resolver used by the plugin.
  api_url: https://artwork.m8tec.top

  # Output formats. Can be enabled independently or in any combination.
  save_square_webp: yes
  save_square_mp4: no
  save_tall_mp4: no
  save_tall_webp: no

  # Output filenames written next to the album's audio files.
  square_webp_filename: cover.webp
  square_mp4_filename: cover.mp4
  tall_mp4_filename: cover-tall.mp4
  tall_webp_filename: cover-tall.webp

  # Desired target widths. fetchanimated checks which source resolutions are
  # actually available and selects one according to resolution_policy.
  # Observed Square range (informational only):
  #   min: 360x360
  #   max: 2160x2160
  #
  # Observed Tall range (informational only):
  #   min: 310x414
  #   max: 2048x2732 
  square_target_width: 768
  tall_target_width: 830
  resolution_policy: nearest

  # Animated WebP encoding quality (0-100).
  webp_quality: 80

  # FFmpeg executable. Use an absolute path here if ffmpeg is not on PATH.
  ffmpeg: ffmpeg

  # Network and subprocess limits.
  api_timeout: 60
  api_request_delay_seconds: 3.0
  api_error_backoff_seconds: 30.0
  manifest_timeout: 20
  ffmpeg_timeout: 600

  # Existing animated artwork files are preserved by default.
  # Choose yes if you want to override an existing animated artwork file.
  overwrite: no

  # Move known animated artwork files when beets moves the album.
  move_with_album: yes

  # Small delay between albums during batch commands.
  batch_delay_seconds: 0.25

  # Optional persistent batch reports. Empty disables report-file output.
  full_library_log: ""
  limit_log: ""
  retry_errors_log: ""

  # Keep animated WebP files separate from fetchart's static cover handling.
  protect_fetchart_filesystem: yes
```

## Output formats

All four output types can be enabled independently or together:

| Setting | Output |
|---|---|
| `save_square_webp` | Square animated WebP → `cover.webp` |
| `save_square_mp4` | Square MP4 → `cover.mp4` |
| `save_tall_webp` | Tall animated WebP → `cover-tall.webp` |
| `save_tall_mp4` | Tall MP4 → `cover-tall.mp4` |

The default configuration downloads only the square animated WebP:

```yaml
save_square_webp: yes
save_square_mp4: no
save_tall_webp: no
save_tall_mp4: no
```

For example, this downloads **both square and tall artwork as animated WebP**:

```yaml
save_square_webp: yes
save_square_mp4: no
save_tall_webp: yes
save_tall_mp4: no
```

That creates both files in the album directory:

```text
cover.webp
cover-tall.webp
```

Any combination is valid, including all four outputs at the same time.

## Resolution and quality

You configure the resolution you would **like** to receive:

```yaml
square_target_width: 768
tall_target_width: 830
resolution_policy: nearest
```

fetchanimated checks which source resolutions are actually available for that artwork and then selects the best match.

With the default `nearest` policy:

1. an exact requested width is used when available;
2. otherwise, the nearest available width is selected;
3. on an exact tie, the larger source is preferred.

The selected video keeps its real source resolution; fetchanimated does not upscale it.

Available policies:

- `nearest` - closest available width to your target.
- `at_most` - largest available width that does not exceed your target; falls back to `nearest` if needed.
- `at_least` - smallest available width that meets or exceeds your target; falls back to `nearest` if needed.
- `highest` - always use the highest available resolution.

Observed artwork variants currently reach up to:

- Square: **2160×2160**
- Tall: **2048×2732**

Actual availability depends on the album and artwork variant.

For WebP output, quality is configured separately:

```yaml
webp_quality: 80
```

MP4 output remuxes the selected source video without video re-encoding. WebP output is encoded from that selected source with FFmpeg/libwebp at the configured quality.

## Automatic artwork downloads

With:

```yaml
auto: yes
```

fetchanimated automatically looks for animated artwork when beets successfully imports an album.

If an animated artwork is found, the configured output files are written into the final album directory. If no animated artwork is available or the external artwork service is temporarily unavailable, the beets import itself continues.

By default, albums imported with beets' `asis` mode are skipped. To include them:

```yaml
fetch_for_asis: yes
```

## Command line: practical examples

`beet fetchanimated` accepts normal beets album queries and works with albums already present in your beets database.

### Search all albums by an artist

```bash
beet fetchanimated albumartist:"Daft Punk"
```

Use this to search animated artwork for every Daft Punk album currently in your beets library. Successful results are saved directly into the corresponding album folder.

### Search one exact album

```bash
beet fetchanimated 'album:=~Hybrid Theory'
```

Use beets' case-insensitive exact-match operator `:=~` when you want one specific album title rather than a broader substring query. If a match is found, the configured artwork files are written directly into that album's existing library folder.

### Search by exact album + artist

```bash
beet fetchanimated 'album:=~Hybrid Theory' 'albumartist:=~Linkin Park'
```

Use both exact fields when an album title is ambiguous or you want to target one exact artist/album combination. Normal beets substring queries remain available when you intentionally want a broader selection.

### Preview without writing files

```bash
beet fetchanimated --dry-run albumartist:"Daft Punk"
```

Use `--dry-run` to check which albums match, whether animated artwork is available, and which source resolution would be selected. No artwork files are written or replaced.

### Test with a small batch

```bash
beet fetchanimated --limit 10
```

Use this to test your configuration on at most ten albums before starting a larger run. Use any number you want.

You can combine a limit with a query:

```bash
beet fetchanimated --limit 5 albumartist:"Daft Punk"
```

### Overwrite existing artwork once

```bash
beet fetchanimated --force album:"Hybrid Theory" albumartist:"Linkin Park"
```

Existing enabled artwork is preserved by default. Use `--force` when you intentionally want to recreate or replace it this time only.

For permanent automatic replacement behavior:

```yaml
overwrite: yes
```

### Backfill the complete beets library

```bash
beet fetchanimated --full-library
```

Use this for an initial animated-artwork backfill or after enabling additional output formats.

A full-library run can take **several hours or even days** on a large library. Each album may require API/HLS requests and FFmpeg encoding for WebP. fetchanimated also deliberately spaces API requests and backs off after API errors to reduce service timeouts and rate-limit problems.

A queryless command is also supported:

```bash
beet fetchanimated
```

but `--full-library` is clearer and recommended for an intentional full-library run.

### Retry only previous API errors

```bash
beet fetchanimated --retry-errors /path/to/fetchanimated-full-library.log
```

`--retry-errors` reads the `ERRORS` section from the most recent eligible full-library or retry-errors report and retries only those albums. Confirmed `NOT FOUND` entries are not queried again. Logged album labels must map to exactly one current Beets album; missing or ambiguous labels are skipped rather than guessed.

You can first test a smaller retry batch:

```bash
beet fetchanimated \
  --retry-errors /path/to/fetchanimated-full-library.log \
  --limit 20
```

If a retry report is enabled, you can later feed that report back into `--retry-errors` to retry only the technical errors that still remain.

## Persistent batch reports

Reports after `--limit`, `--full-library`, and `--retry-errors` runs are optional. To enable them, set actual file paths in `config.yaml`:

```yaml
fetchanimated:
  full_library_log: /path/to/fetchanimated-full-library.log
  limit_log: /path/to/fetchanimated-limit.log
  retry_errors_log: /path/to/fetchanimated-retry-errors.log
```

Leaving either value empty disables that report.

The report contains the run summary, including:

- processed albums;
- albums with new artwork;
- saved/updated files;
- skipped/already-complete albums;
- artwork not found or unavailable;
- partial results;
- errors.

It also lists the affected album names for not-found, partial, and error cases, which is useful when you want to retry individual albums manually after temporary API problems.

`full_library_log` is written for `--full-library` runs. `limit_log` is written for explicit `--limit` runs. `retry_errors_log` is written for `--retry-errors` runs and also records any logged labels that could not be matched uniquely to the current Beets library.

## Existing artwork files

The default behavior is deliberately safe:

```yaml
overwrite: no
```

If an enabled output already exists in the album folder, it is not replaced.

Use `--force` for a one-time replacement or `overwrite: yes` if you intentionally want automatic overwriting.

## fetchart compatibility

fetchanimated and beets `fetchart` can be used together without getting in each other's way.

`fetchart` continues to manage your normal static album artwork such as `cover.jpg`, while fetchanimated manages the animated artwork files.

With the recommended default:

```yaml
protect_fetchart_filesystem: yes
```

fetchanimated keeps its configured animated WebP files separate from `fetchart`'s normal static-cover handling. Your existing static artwork is not replaced or modified.

For normal setups, leave this option enabled.

## Moving albums with beets

With:

```yaml
move_with_album: yes
```

known animated artwork files follow the album when beets later moves that album to a different directory, provided the destination artwork file does not already exist.

## Troubleshooting and common messages

For more detail while testing, run beets in verbose mode:

```bash
beet -vv fetchanimated --dry-run album:"Hybrid Theory" albumartist:"Linkin Park"
```

| Message | What it means / what to do |
|---|---|
| `no Apple Motion Artwork found` | No animated artwork was returned for that album. A normal API `404` is treated as “not found,” not as a fatal error. |
| `requested Apple Motion Artwork variant unavailable` | The album was found, but the requested square/tall variant could not be obtained. That shape or stream may not exist. |
| `square variant unavailable` / `tall variant unavailable` | During a dry run, that specific artwork shape could not be resolved. Other enabled variants may still work. |
| `artwork API error; album skipped` | The external artwork API returned an error or could not be reached. Retry later, or use `--retry-errors` with a saved batch report. |
| `artwork API HTTP 429 ...` | The service is rate-limiting requests. Wait and retry later. |
| `could not read HLS manifest ... HTTP Error 404` | The returned Apple HLS URL is no longer available at that location or is temporarily unavailable. Retry later. |
| `ffmpeg not found ...` | FFmpeg is not available where beets runs. Install it in the same host/container or configure the correct `ffmpeg:` path. |
| `WebP encode` failed / `conversion did not produce a WebP file` | Check that the installed FFmpeg build includes the `libwebp` encoder. |
| `WebP is not animated; discarding it` | FFmpeg produced a non-animated WebP, so `fetchanimated` refuses to save it as valid animated artwork. |
| `all enabled animated artwork files exist` | All requested files are already present and are being preserved. Use `--force` if you intentionally want to replace them. |
| `no new animated artwork file saved` | Artwork was found but no requested output was successfully written. Run with `-vv` for the underlying warning. |
| `could not write ...` / permission errors | The beets process cannot write to the album directory or destination file. Check filesystem/container permissions. |

## External API

The default resolver is:

```yaml
api_url: https://artwork.m8tec.top
```

The resolver is an external service and is not operated by this project. Availability and behavior can therefore change independently of fetchanimated.

fetchanimated deliberately leaves normal album resolution to m8tec. For clear numbered-title collisions where m8tec resolves an otherwise identical neighbouring album with a different or missing standalone numeric suffix at the very end, for example `Gangsta Art` vs `Gangsta Art 2`, fetchanimated can recover the specifically requested numbered release instead of using the wrong artwork. This improves on m8tec's default search behavior for that narrow class of matches. It is not a general fuzzy/exact matcher: names such as `DS4EVER`, `DRIP SEASON 4EVER`, `LONG.LIVE.A$AP`, and edition suffixes are not rejected merely because their spelling differs.

## What fetchanimated does not change

- It does not modify audio metadata or tags.
- It does not change album identity, artist metadata, track/disc numbering, or audio filenames.
- It does not replace your static `cover.jpg`.
- It does not replace beets `fetchart` or `embedart`.
- It does not upscale the selected animated source video.

## Credits

fetchanimated uses the public artwork API provided by [m8tec's Apple Music Animated Artwork Downloader](https://github.com/m8tec/apple-music-animated-artworks). Thanks to m8tec for making the resolver and API available for use by other projects.

## AI-assisted development

AI-assisted tools were used during development and documentation. AI-generated or AI-suggested changes included in releases were reviewed and tested by the maintainer.

## Development

For development and testing from a source checkout:

```bash
python -m pip install -e ".[dev]"
pytest
python -m build
```

The test suite is network-free and covers core configuration, HLS parsing/selection, and package consistency.

## License

The source code in this repository is released under the MIT License. See [`LICENSE`](LICENSE).

## Legal disclaimer

This is an independent, unofficial project and is not affiliated with or endorsed by Apple, Apple Music, beets, or m8tec.

Apple Music artwork and other third-party media remain the property of their respective rights holders. The MIT License for this project's source code does not grant any rights to Apple Music content or other third-party media retrieved through external services.

Users are responsible for complying with the terms of service and laws that apply to the services and media they access. fetchanimated does not include Apple Music artwork in this repository and is not intended to bypass DRM, authentication, paywalls, or other access controls.
