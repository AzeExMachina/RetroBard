# Changelog

All notable changes to RetroBard are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

When you release, rename **Unreleased** to the new version and date. The release
workflow publishes that version's section as the GitHub release notes, which the
app's "Check for updates" window shows.

## [Unreleased]

### Added
- RetroBard opens the game you used last. If that game's profile is gone, it
  opens the first game in the list.
- When two characters talk in the same dialogue box ("RANDI:..." then
  "POPOI:..." on a later line), each is spoken in their own voice, one after the
  other. If the game adds the second character to a box already spoken, only the
  new part is said.

### Changed
- Every voice RetroBard ships is now an ElevenLabs Infinity voice, which
  ElevenLabs never retires (apart from voices designed for one character). The
  narrator stays Adam, in his Infinity version. With a paid ElevenLabs plan you can
  still pick any Voice Library voice in your game profile.
- Profiles still using the retiring Adam or Bella voices are moved to their
  Infinity versions at startup.

### Fixed
- Collecting dialogue on the Letters tab now works in games whose text box is
  see-through, with scenery showing between the letters (such as Secret of Mana).
  RetroBard notices this the first time you collect and reads only the white
  letter pixels for that game. Reading aloud on the Play tab works for these
  boxes too, even while scenery moves behind them.
- Letters are read correctly when the emulator draws the game at a whole-number
  zoom with a black border around it, instead of filling the window, also in
  dark scenes where part of the picture's edge is black.
- Speaker names written flush against the text ("MAN:Hang on!") are recognised,
  so those lines are spoken instead of skipped.
- In games that draw capital I and lowercase l the same way, words like
  "I'll", "help", "village" and "letter" are read with the right letter.
- Words starting with a capital I, such as "Its", "Into" or "Instead", are no
  longer split into "I ts" and similar.
- Free ElevenLabs plans cannot use Voice Library voices through the API. When a
  replacement voice is refused, the Default voice it replaced speaks instead (these
  work on free plans until ElevenLabs removes them on 31 December 2026), with one
  notice saying so. A voice that cannot be used at all now gives one clear message
  instead of an error report on every line.

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
[0.3.0]: https://github.com/AzeExMachina/RetroBard/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/AzeExMachina/RetroBard/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AzeExMachina/RetroBard/releases/tag/v0.2.1
