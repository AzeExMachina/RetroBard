# Changelog

All notable changes to RetroBard are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

When you release, rename **Unreleased** to the new version and date. The release
workflow publishes that version's section as the GitHub release notes, which the
app's "Check for updates" window shows.

## [Unreleased]

## [0.3.1] - 2026-11-25

### Added
- "What's new" button in the About window and the tray menu. It shows this
  changelog, read from the copy shipped with the app, so it works offline.
- After an update, RetroBard says once (in a tray notification) that it was updated;
  clicking the notification opens "What's new". A first install shows nothing.

## [0.3.0] - 2026-11-25

### Added
- "Check for updates" button in the About window and the tray menu. It asks GitHub
  for the latest release only when pressed, and can download the new version,
  check its SHA-256, and replace the app folder (settings, profiles and letters in
  `%APPDATA%\RetroBard` are kept). Copies installed by the itch.io app are left to
  the itch.io app.
- Release workflow: pushing a `v*` tag runs the tests, builds the Windows zip,
  publishes a GitHub Release with the zip and its `.sha256`, and pushes the build
  to itch.io with butler.
- On startup, the user's game profiles are brought up to date: retired ElevenLabs
  voices are swapped for their replacements, and characters a newer shipped profile
  voices are added. Characters the user already set up are not changed.

## [0.2.2] - 2026-11-25

### Fixed
- The "Export letters..." button no longer crashes: it opens the save dialog again.

### Changed
- ElevenLabs Default voices, which stop working on 2026-12-31, are replaced with
  their non-expiring replacements in the Chrono Trigger profile and the built-in
  defaults. Both NPC voice pools now have five voices.
- Chrono Trigger profile: the Queen uses Marle's voice, Magus and Ayla have voices,
  and more letter shapes are taught.

## [0.2.1] - 2026-11-23

First public version.

### Added
- Reads a retro game's dialogue box from one linked game window and speaks it with
  AI voices, a voice per character.
- Voices from ElevenLabs (your own API key) or any OpenAI-compatible speech server
  (OpenAI, Kokoro-FastAPI, Speaches, LocalAI), plus a silent test mode.
- Letter-shape reader that learns a game's font ("Letters" tab, `teach`), with
  EasyOCR as an optional fallback.
- Window app with Play, Setup, Letters and Options tabs, a quick start guide and a
  tray icon; command line with `calibrate`, `teach`, `run` and `merge-letters`.
- Per-character pitch shift and effects, a cache of spoken lines, and skip keys or
  gamepad buttons read only while the game window is focused.
- Chrono Trigger profile and taught font for snes9x.
- Windows tester package built with PyInstaller.

[Unreleased]: https://github.com/AzeExMachina/RetroBard/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/AzeExMachina/RetroBard/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/AzeExMachina/RetroBard/compare/176c2df...v0.3.0
[0.2.2]: https://github.com/AzeExMachina/RetroBard/compare/3ac7c42...176c2df
[0.2.1]: https://github.com/AzeExMachina/RetroBard/tree/3ac7c42
