# Changelog

All notable changes to RetroBard are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

When you release, rename **Unreleased** to the new version and date. The release
workflow publishes that version's section as the GitHub release notes, which the
app's "Check for updates" window shows.

## [Unreleased]

### Added
- RetroBard can read the dialogue straight from the game's memory instead of
  the screen, for **Secret of Mana** and **Chrono Trigger** (USA versions)
  played in RetroArch with the Snes9x core. The text is exact, even in
  see-through text boxes, and there is no dialogue box to select and no
  letters to teach. Turn on **Network Commands** in RetroArch's Network
  settings, then set **Read dialogue from** on the Play tab to **Game memory
  (RetroArch)**. The first time you press Start, choose the game's ROM file: the
  text is read from there, so RetroBard checks that it is the exact version
  the profile was made for. Its location is remembered on this computer, and
  the file never leaves it. From the command line, `retrobard set-rom` does the
  same.
- A new Quick Start page explains how to set up reading from game memory.
- Game profiles can give a character a second name that speaks with the same
  voice. In Chrono Trigger, Marle is called "GIRL" until she gives her name, and
  those lines now use Marle's voice instead of a random one.
- Chrono Trigger no longer speaks the place names shown when you enter an area
  ("Leene Square", "Residence"), and they no longer make the last speaker say
  them. Other games are not affected.
- `retrobard find-text`, for people making game profiles, finds where a game
  keeps a line of dialogue in its memory or ROM, given the text on screen.

### Changed
- Each game remembers where its dialogue is read from (the game window, a
  helper script or game memory). **Game memory** is only offered for the games
  that support it.
- When reading from game memory, the Letters tab and the Setup tab's dialogue
  area and preview are switched off, since they aren't needed. Linking the
  window on the Setup tab is still useful: pick RetroArch there so the skip keys
  only work while the game is in front.
- Profiles you already have are brought up to date on the next start with new
  settings from the shipped ones, such as reading from game memory, character
  second names and the place-name rule. Settings you changed yourself stay
  as they are.

### Fixed
- A line with a speaker's name but nothing to say ("RANDI: !?") is still
  silent, but the unnamed lines after it are now spoken in that character's
  voice instead of falling back to the narrator or a stranger.
- The Windows build could include a copy of OpenSSL that doesn't belong to
  Python, so the packaged app closed at start with "DLL load failed while
  importing _ssl". The build now uses Python's own copy, and refuses to package
  any other.

## [0.4.0] - 2026-11-26

### Added
- RetroBard opens the game you used last. If that game's profile is gone, it
  opens the first game in the list.
- When two characters talk in the same dialogue box ("RANDI:..." then
  "POPOI:..." on a later line), each is spoken in their own voice, one after the
  other. If the game adds the second character to a box already spoken, only the
  new part is said.
- Chrono Trigger and Secret of Mana now come with voices for the free Kokoro voice
  server, so every Chrono Trigger character keeps their own voice on a custom
  server. Profiles you already have get these voices on the next start; Kokoro
  voices you set yourself stay as they are.
- Secret of Mana now has voices for 27 characters, from the three heroes to
  Thanatos and the Dark Lich, on both ElevenLabs and Kokoro.
- The Quick Start and README list the Snes9x display settings that let RetroBard
  read the text reliably: Stretch Image with 8:7 aspect ratio and Integer Scaling
  on, filters and shaders off. Without Stretch Image, Snes9x shows the game at its
  smallest size, which causes many missed lines.
- The Letters tab has a **Discard** button on each shape, for shapes that are
  clearly misread (letters run together or cut off), and a **Discard all** button
  next to Save letters. Discarded shapes are not taught; they come back if they are
  collected again.

### Changed
- The Voice list on the Play tab now starts with the custom server (such as the
  free Kokoro server), then ElevenLabs, then Silent test.
- Every voice RetroBard ships is now an ElevenLabs Infinity voice, which
  ElevenLabs never retires (apart from voices designed for one character). The
  narrator stays Adam, in his Infinity version. With a paid ElevenLabs plan you can
  still pick any Voice Library voice in your game profile.
- Profiles still using the retiring Adam or Bella voices are moved to their
  Infinity versions at startup.

### Fixed
- Pressing Start with a custom voice server (such as Kokoro) after opening the
  Server settings could freeze or close the app ("Fatal Python error: Aborted").
  This was a bug in Python 3.13.0; RetroBard is now built with Python 3.13.16.
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
