# Changelog

All notable changes to this project are documented here.

## 0.2 - 2026-08-17

### Fixed

- Improved handling of numbered album titles such as `Album`, `Album 2`, and `Album 3`: when m8tec resolves the request to the wrong neighbouring numbered album, fetchanimated now retrieves the artwork for the specifically requested album instead.
- This is a targeted improvement over m8tec's default search behavior for these clear numbered-title collisions; unrelated spelling variants and normal edition suffixes keep their existing behavior.

## 0.1.2 - 2026-08-16

### Added

- Added `--retry-errors PATH` to retry only albums listed in the `ERRORS` section of the most recent eligible full-library or retry-errors report.
- Added optional `retry_errors_log` reporting, including unresolved current-library labels that are skipped rather than guessed.
- Added a narrow numeric-suffix safety guard for clear sequel cache collisions such as `Album` vs `Album 2` or `Album 2` vs `Album 3`.

### Safety

- The numeric guard is intentionally not a general album-title matcher. Alphanumeric names such as `DS4EVER` / `DRIP SEASON 4EVER`, punctuation variants such as `LONG.LIVE.A$AP`, and normal edition suffixes remain outside this veto.
- Retry mode never re-queries confirmed `NOT FOUND` entries from the source report and only processes logged labels that map to exactly one current Beets album.

### Preserved

- m8tec remains the normal Apple Music artwork resolver.
- Existing HLS selection, native-FPS WebP encoding, optional output formats, existing-file protection, fetchart isolation, import hooks, move/copy handling, API pacing/backoff, and batch report behavior remain unchanged.

## 0.1.1 - 2026-08-13

### Changed

- Formatting updated.

## 0.1 - 2026-08-13

First standalone public release.

### Changed

- Removed custom-importer-specific coupling while retaining a generic optional
  programmatic existing-album API.
- Kept the standard beets album import pipeline as the automatic integration path.
- Changed the FFmpeg default from a machine-specific absolute path to `ffmpeg` on `PATH`.
- Disabled persistent report files by default instead of assuming a machine-specific configuration directory.
- Added standalone packaging, example configuration, documentation, tests, and GitHub Actions CI.

### Fixed

- Preserve known existing motion-art sidecars when beets copies album items to a
  new directory, without replacing a sidecar that already exists at the destination.

### Preserved

- m8tec album artwork resolution behavior.
- Square/tall Apple HLS discovery and resolution selection.
- Lossless HLS-to-MP4 remuxing.
- Animated WebP encoding behavior.
- Existing-file protection and `--force` behavior.
- Atomic sidecar placement.
- fetchart filesystem protection for configured animated WebP sidecars.
- Sidecar handling during later beets moves.
- Query, dry-run, limit, and full-library command behavior.
