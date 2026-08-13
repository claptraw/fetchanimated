# Changelog

All notable changes to this project are documented here.

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
