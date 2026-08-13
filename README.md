# fetchanimated

`fetchanimated` is a [beets](https://beets.io/) plugin for downloading Apple Music animated album artwork directly into the matching album folders in your existing music library.

It uses the public m8tec artwork API to resolve Apple Music animated artwork and can save square and tall artwork as animated WebP and/or MP4 files.

## Features

* Manual or automatic download of Apple Music animated artwork through the public m8tec API.
* Supports both **square** and **tall** artwork variants.
* Saves artwork as **animated WebP**, **MP4**, or any combination of both formats and variants.
* Freely configurable target resolution and WebP quality.
* Automatically checks which artwork resolutions are actually available and selects the exact or nearest matching source resolution.
* Lossless MP4 output is possible because the selected Apple HLS video stream is remuxed without video re-encoding. WebP output is encoded at the configured quality.
* Currently observed artwork variants reach up to **2160×2160** for square artwork and **2048×2732** for tall artwork. Actual availability depends on the album; `fetchanimated` never upscales a lower-resolution source.
* Search individual albums by album title, artist + album title, all albums by an artist, or process the complete beets library.
* Automatically fetch artwork for newly imported albums when `auto: yes` is enabled.
* Saves artwork directly into the album directory. The default square WebP filename is `cover.webp`, which works well with Navidrome and clients that support animated artwork such as Narjo.
* Existing artwork files are preserved unless overwriting is explicitly requested.
* Dry-run mode lets you check matches and available resolutions before anything is written.
* When beets moves an album, `fetchanimated` can move the corresponding animated artwork files with it.
* Works alongside beets FetchArt without replacing or modifying your normal static album cover.

## Important: beets library database

`fetchanimated` works from the **beets library database**. It does not scan arbitrary music folders to discover albums.

The albums you want to process therefore need to exist in your beets database, and the paths stored by beets should still point to the actual album directories on disk. This is especially important if files or folders have been moved manually outside beets.

## Installation

### 1. Install the plugin file — recommended

Locate the directory that contains your existing beets `config.yaml` and create a `beetsplug` directory next to it:

```text
beets-config/
├── config.yaml
└── beetsplug/
    └── fetchanimated.py
```

Copy `src/beetsplug/fetchanimated.py` from this repository into that directory.

Then point beets to the directory containing the plugin. Use the path that is valid **inside the environment where beets runs**:

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

Finally, copy the complete `fetchanimated:` configuration from [`config.example.yaml`](config.example.yaml) into your `config.yaml`. The full configuration is also shown below.

### 2. Install FFmpeg

FFmpeg is required for downloading/remuxing the animated video and for creating animated WebP files.

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

Verify the installation:

```bash
ffmpeg -version
```

For WebP output, also verify that the FFmpeg build includes `libwebp`:

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

Add `fetchanimated` to your existing plugin list and configure the plugin with the complete block below.

```yaml
plugins: fetchart embedart fetchanimated

pluginpath:
  - /path/to/beets-config/beetsplug

fetchanimated:
  auto: yes
  fetch_for_asis: no

  api_url: https://artwork.m8tec.top

  save_square_webp: yes
  save_square_mp4: no
  save_tall_mp4: no
  save_tall_webp: no

  square_webp_filename: cover.webp
  square_mp4_filename: cover.mp4
  tall_mp4_filename: cover-tall.mp4
  tall_webp_filename: cover-tall.webp

  square_target_width: 768
  tall_target_width: 830
  resolution_policy: nearest

  webp_quality: 80
  ffmpeg: ffmpeg

  api_timeout: 60
  api_request_delay_seconds: 3.0
  api_error_backoff_seconds: 30.0
  manifest_timeout: 20
  ffmpeg_timeout: 600

  overwrite: no
  move_with_album: yes
  batch_delay_seconds: 0.25

  full_library_log: ""
  limit_log: ""

  protect_fetchart_filesystem: yes
```

## Output formats

All four output types can be enabled independently or together:

| Setting            | Output                                 |
| ------------------ | -------------------------------------- |
| `save_square_webp` | Square animated WebP → `cover.webp`    |
| `save_square_mp4`  | Square MP4 → `cover.mp4`               |
| `save_tall_webp`   | Tall animated WebP → `cover-tall.webp` |
| `save_tall_mp4`    | Tall MP4 → `cover-tall.mp4`            |

The default configuration downloads only the square animated WebP:

```yaml
save_square_webp: yes
save_square_mp4: no
save_tall_webp: no
save_tall_mp4: no
```

For example, to download both the square and tall artwork as animated WebP files:

```yaml
save_square_webp: yes
save_square_mp4: no
save_tall_webp: yes
save_tall_mp4: no
```

That creates both:

```text
cover.webp
cover-tall.webp
```

in the album directory.

You can also enable all four outputs at the same time.

## Resolution and quality

You specify the resolution you would **like** to receive:

```yaml
square_target_width: 768
tall_target_width: 830
resolution_policy: nearest
```

For every artwork, `fetchanimated` checks the resolutions that Apple actually provides.

With the default `nearest` policy:

1. If the exact requested width exists, it is selected.
2. Otherwise, the closest available width is selected.
3. If two available widths are equally close, the larger one is preferred.

The selected video is kept at its existing source resolution; the plugin does not upscale it to your requested width.

Available policies:

* `nearest` — closest available width to your target.
* `at_most` — largest available width that does not exceed your target; falls back to `nearest` if needed.
* `at_least` — smallest available width that meets or exceeds your target; falls back to `nearest` if needed.
* `highest` — always use the highest resolution offered for that artwork.

Observed artwork variants currently reach up to:

* Square: **2160×2160**
* Tall: **2048×2732**

Not every album provides every resolution or both artwork shapes.

For WebP output, quality is configured separately:

```yaml
webp_quality: 80
```

MP4 output uses the selected source video stream without video re-encoding. WebP output is created from that selected stream with FFmpeg/libwebp at the configured quality.

## Automatic artwork downloads

With:

```yaml
auto: yes
```

`fetchanimated` automatically looks for animated artwork when beets successfully imports an album.

If artwork is found, the enabled output files are written into the final album directory. If no animated artwork is available or the external artwork service is temporarily unavailable, the beets import itself is allowed to continue.

By default, albums imported with beets' `asis` mode are skipped. To include them:

```yaml
fetch_for_asis: yes
```

## Command line: practical examples

`beet fetchanimated` accepts normal beets album queries. These commands operate on albums already present in your beets database.

### Find artwork for every album by an artist

```bash
beet fetchanimated albumartist:"Daft Punk"
```

This processes all matching Daft Punk albums in your beets library. For every album where animated artwork is found, the enabled artwork files are placed directly into that album's existing directory.

### Find artwork for one album

```bash
beet fetchanimated album:"Hybrid Theory"
```

This searches the matching `Hybrid Theory` album in your beets library and, if successful, saves the enabled animated artwork directly into that album folder.

### Use album + artist when the album title is ambiguous

```bash
beet fetchanimated album:"Hybrid Theory" albumartist:"Linkin Park"
```

This is the safest way to target a specific release when multiple albums in your library share a similar or identical title.

### Preview what would happen without writing files

```bash
beet fetchanimated --dry-run albumartist:"Daft Punk"
```

Use `--dry-run` when you want to check whether artwork is found and which source resolution would be selected without creating or replacing any files.

### Process only a small batch

```bash
beet fetchanimated --limit 10
```

This processes at most ten albums. It is useful for testing your configuration before running against a large library.

You can combine a limit with a query:

```bash
beet fetchanimated --limit 5 albumartist:"Daft Punk"
```

### Replace artwork that already exists

```bash
beet fetchanimated --force album:"Hybrid Theory" albumartist:"Linkin Park"
```

By default, existing enabled artwork files are left untouched. Use `--force` when you intentionally want to recreate or replace them for that command.

For permanent automatic replacement behavior:

```yaml
overwrite: yes
```

### Process the complete beets library

```bash
beet fetchanimated --full-library
```

This is useful for the first artwork backfill after installing the plugin or after enabling an additional output format.

A queryless command is also supported:

```bash
beet fetchanimated
```

but `--full-library` is clearer and recommended for an intentional full-library run.

If `full_library_log` or `limit_log` is configured, the corresponding batch run also writes a persistent summary report.

## Existing artwork files

The default behavior is deliberately safe:

```yaml
overwrite: no
```

If an enabled output already exists in the album folder, it is not replaced. This makes it possible to run `fetchanimated` repeatedly without constantly regenerating artwork you already have.

Use `--force` for a one-time replacement or `overwrite: yes` if you intentionally want automatic overwriting.

## FetchArt compatibility

`fetchanimated` and beets FetchArt can be used together.

FetchArt continues to manage your normal static album artwork, such as `cover.jpg`, while `fetchanimated` manages the animated artwork files.

With the recommended default:

```yaml
protect_fetchart_filesystem: yes
```

the two plugins do not get in each other's way: `fetchanimated` keeps its configured animated WebP files separate from FetchArt's normal static-cover handling.

Your existing static artwork is not replaced or modified.

For normal setups, leave this option enabled.

## Moving albums with beets

With:

```yaml
move_with_album: yes
```

known animated artwork files follow the album when beets later moves that album to a different directory, provided the destination artwork file does not already exist.

## Troubleshooting and common messages

For more detail while testing:

```bash
beet -vv fetchanimated --dry-run album:"Hybrid Theory" albumartist:"Linkin Park"
```

| Message                                                          | What it means / what to do                                                                                                                                                                                                          |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `no Apple Motion Artwork found`                                  | The m8tec resolver did not return animated artwork for that album. A normal API `404` is treated this way rather than as a fatal error. The release may simply have no animated artwork, or the resolver may not currently find it. |
| `requested Apple Motion Artwork variant unavailable`             | The album was found, but the requested square/tall artwork could not be obtained. That shape may not exist, or its Apple HLS stream may currently be unavailable.                                                                   |
| `square variant unavailable` / `tall variant unavailable`        | During a dry run, that specific artwork shape could not be resolved to a usable stream. Other enabled variants may still work.                                                                                                      |
| `artwork API error; album skipped`                               | The external artwork API returned a non-404 HTTP error or could not be reached. This is usually a temporary service/network problem; retry later.                                                                                   |
| `artwork API HTTP 429 ...`                                       | The external service is rate-limiting requests. Wait and retry later; the plugin also uses configurable request delays/backoff.                                                                                                     |
| `could not read HLS manifest ... HTTP Error 404`                 | The artwork resolver returned an HLS URL that Apple no longer serves at that location, or the URL is temporarily unavailable. Retry later.                                                                                          |
| `ffmpeg not found ...`                                           | FFmpeg is not available where beets is running. Install it in the same system/container or configure the correct executable path with `ffmpeg:`.                                                                                    |
| `WebP encode` failed or `conversion did not produce a WebP file` | Check that your FFmpeg build includes the `libwebp` encoder.                                                                                                                                                                        |
| `WebP is not animated; discarding it`                            | FFmpeg produced a WebP that did not contain animation, so the plugin refuses to save it as valid animated artwork.                                                                                                                  |
| `all enabled animated artwork files exist`                       | Nothing is wrong. All configured outputs are already present and are being preserved. Use `--force` if you intentionally want to replace them.                                                                                      |
| `no new animated artwork file saved`                             | Artwork was found but no requested output was successfully written. Run with `-vv` to see the underlying FFmpeg, manifest, or filesystem warning.                                                                                   |
| `could not write ...` / permission errors                        | The beets process does not have permission to write to that album directory or destination filename.                                                                                                                                |

## External API

The default resolver is:

```yaml
api_url: https://artwork.m8tec.top
```

The resolver is an external service and is not operated by this project. Availability and behavior can therefore change independently of `fetchanimated`.

## What fetchanimated does not change

* It does not modify audio metadata or tags.
* It does not change album identity, artist metadata, track/disc numbering, or audio filenames.
* It does not replace your static `cover.jpg`.
* It does not replace beets FetchArt or EmbedArt.
* It does not upscale the selected animated source video.

## Credits

`fetchanimated` uses the public artwork API provided by [m8tec's Apple Music Animated Artwork Downloader](https://github.com/m8tec/apple-music-animated-artworks). Thanks to m8tec for making the resolver and API available for use by other projects.

## AI-assisted development

AI-assisted tools were used during development and documentation. AI-generated or AI-suggested changes included in releases were reviewed and tested by the maintainer. Responsibility for the released code remains with the maintainer.

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

Users are responsible for complying with the terms of service and laws that apply to the services and media they access. `fetchanimated` does not include Apple Music artwork in this repository and is not intended to bypass DRM, authentication, paywalls, or other access controls.
