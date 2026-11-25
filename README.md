# RetroBard

Real-time voice acting for retro games: reads the dialogue box of a game window and speaks it.

## Privacy model

RetroBard runs on your computer and is linked to **one game window**:

- Frames are taken from that window's own surface, not from the screen, so other
  applications are never captured (even if they overlap the game).
- To let you pick the game, the Setup tab and `calibrate` list the titles of your open
  windows. They are shown only to you and never saved or sent.
- Dialogs are never linked. If several windows match the linked program, nothing is read
  until you add a title filter on the Setup tab.
- The dialogue box is stored as a fraction of the game window, so it follows the
  window when you move or resize it. If the window is closed or minimised, nothing is read.
- There is no global keyboard hook. Skip keys / gamepad buttons are only polled while
  the game window is focused, and only the keys listed in the profile.
- Dialogue text is not stored in the cache index and is only printed with `--verbose`.
  The spoken audio of each line stays in the cache until you clear it (Options tab)
  or the cache size limit removes it.
- With `--tts elevenlabs`, the dialogue text read from the game is sent to ElevenLabs
  with your own API key. ElevenLabs may use it to improve its models unless you opt out
  in your ElevenLabs account (Terms and Privacy > Data use). ElevenLabs requires users
  to be 18 or older.
- With `--tts custom`, the dialogue text is sent to the voice server you set up (see
  [Custom voice server](#custom-voice-server)). A server on your own computer keeps it
  there; an online service follows its own privacy terms.
- RetroBard has no telemetry, accounts or servers, and sends nothing to its developer.

The voices are AI-generated.

Window capture is currently Windows-only.

## Calibration

Start the game/emulator, then link its window and select the dialogue box:

```uv run retrobard calibrate --game chrono_trigger --window snes9x```

Omit `--window` to pick from a list of open windows. This writes `window:` and
`ocr.region` into `profiles/<game>.yaml`.

## Teaching the game's letters

A retro game draws each letter with the same pixels every time, so RetroBard can
read the text exactly by matching letter shapes instead of using a general OCR model.
This takes a few milliseconds per box and does not load EasyOCR or torch.

```uv run retrobard teach --game chrono_trigger```

Play through some dialogue while it collects boxes (Ctrl+C when you have enough).
It then shows each letter shape it does not know yet, drawn with `#`, and you type
which character it is. Type `!ignore` for things that are not letters (cursor, icons),
press Enter to skip one, `!quit` to save and stop. Run it again any time to add
letters it has not seen; the result is stored in `profiles/<game>.font.json`.

`run` uses the taught font automatically when one exists (`ocr.engine: auto`), and
falls back to EasyOCR otherwise. Set `ocr.engine` to `glyph` or `easyocr` to force one.

Notes:

- The emulator must keep the picture's aspect ratio and centre it. If it stretches the
  picture, set `ocr.picture` in the profile to where the picture sits in the window.
- `ocr.native_width` / `ocr.native_height` default to 256x224 (SNES).
- Text that is mostly not in the taught font is ignored rather than guessed at.
- Choice prompts ("Will you rest?" followed by "Yes" / "No") speak only the question;
  the answer lines are skipped. Set `dialogue_rules.skip_choices: false` to read them.

## Audio

ElevenLabs audio is requested as raw PCM (`ELEVENLABS_OUTPUT_FORMAT=pcm_24000` by
default) and starts playing while it is still arriving. Voices with a pitch shift or
an effect (robot, monster, cave) need the whole clip first, so they start slightly later.
A line that is skipped before it starts is never requested, so it costs no credits.

Set `ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128` in `.env` to go back to complete MP3 clips.
Audio cached in one format is not reused for the other.

## Custom voice server

Besides ElevenLabs, RetroBard can use any text-to-speech server that speaks the
OpenAI speech API (`POST <address>/audio/speech`): OpenAI itself, or a free server on
your own computer such as [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI),
Speaches or LocalAI. On the Play tab choose **Custom server (OpenAI-compatible)** and
press **Server...** to set the address, model, default voice and (if needed) an API key.
**Test** asks the server for a sample line. From the command line use `--tts custom`.

The settings are saved in `custom_tts.json` (`%APPDATA%\RetroBard` in the packaged app);
the key goes to Windows Credential Manager, or set `CUSTOM_TTS_API_KEY` in `.env`.

ElevenLabs voice IDs mean nothing to other servers, so the profile has a separate
`custom_voices` section with the same layout as `voices`. Characters without a custom
voice use its `default_narrator`, or the server's default voice:

```yaml
custom_voices:
  default_narrator: bm_george
  characters:
    CRONO: am_adam
    FROG: {voice_id: bm_lewis, speed: 0.9}
```

Pitch and effects are applied on your computer, so they work with every server. The
ElevenLabs-only settings (stability, style...) are ignored.

## Sending dialogue over UDP

With `--source udp` (or `both`), a helper on the same computer, such as an emulator
script that reads the game's text from memory, can send lines directly. Each packet
is one JSON object sent to `127.0.0.1:5005`:

```json
{"token": "<contents of .retrobard_cache/udp_token>", "event": "speak", "text": "MARLE: Come on!", "speaker": "MARLE"}
```

- `event` is `speak` (default), `stop` (cut the current line) or `clear_context`
  (the dialogue box closed). `speaker` is optional.
- `token` is required. RetroBard creates a random one in `.retrobard_cache/udp_token`
  on first use; set `UDP_TOKEN` in `.env` to choose your own.
- Only loopback addresses are accepted, text is limited to 1000 characters, and speak
  events are limited to a burst of 10, refilling at 2 per second.
- Malformed packets, or ones with the wrong token, are counted and ignored.

The token keeps out other users and anything that cannot read your files. A program
running as you can read the token file, so it is not protection against that.

## Testing

Tests were made using snes9x-1.62.3-win32-x64 and Chrono Trigger (USA).
RetroBard does not include games or emulators.

### Environment

#### Mock
Run the command ```uv run retrobard speak "* CRONO: Wait up, Marle!" --game chrono_trigger --tts mock```

#### ElevenLabs
Run the command ```uv run retrobard speak "* CRONO: Wait up, Marle!" --game chrono_trigger --tts elevenlabs```

### OCR

#### Mock
Run the command ```uv run retrobard run --game chrono_trigger --source ocr --tts mock```

#### ElevenLabs
Run the command ```uv run retrobard run --game chrono_trigger --source ocr --tts elevenlabs```

Add `--verbose` to also print the text that is read and spoken.

## Building the tester package

```uv run --extra dev python packaging/build.py```

This draws the icon, runs PyInstaller (`packaging/retrobard.spec`, one folder, no
console window) and writes `dist/RetroBard-<version>-windows.zip` with
`packaging/README-testers.txt` inside. Bump the version in both `pyproject.toml` and
`src/retrobard/__init__.py` before each build (a test checks they match).

The packaged app keeps the profiles and letters from `profiles/` read-only inside
the app, and stores everything the user creates in `%APPDATA%\RetroBard`
(profiles, the letters they teach, cache, `settings.env`, `logs/`). On first launch
it copies the shipped `.yaml` profiles there. Run from source, nothing changes:
files stay in `./profiles`, `./.retrobard_cache` and `.env`.

Testers enter their own ElevenLabs key with the "ElevenLabs key..." button on the
Play tab; it is stored in Windows Credential Manager. `ELEVENLABS_API_KEY` in the
environment or `.env` still takes precedence.

## Releasing a new version

While working, note each user-visible change under `## [Unreleased]` in
`CHANGELOG.md`. To release:

1. Bump the version in `pyproject.toml` and `src/retrobard/__init__.py`, then run `uv lock`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.2.3] - <date>`, start a new
   empty `## [Unreleased]` above it, and update the links at the bottom. A test checks
   the current version has a section.
3. Commit, then tag and push the tag:

   ```git tag v0.2.3```

   ```git push origin v0.2.3```

`.github/workflows/release.yml` then checks the tag matches the version, takes the
release notes from that version's `CHANGELOG.md` section, runs the tests, builds the zip, publishes a GitHub Release with the zip and its `.sha256`,
and pushes the build to itch.io with butler (channel `windows`). The itch.io step
needs a `BUTLER_API_KEY` repository secret and is skipped without it.

The app's "Check for updates" button (About window and tray menu) reads the latest
GitHub Release only when pressed. It downloads the zip, checks its SHA-256, unpacks
it next to the app folder, and a PowerShell script swaps the folders once the app
has closed (keeping the old one if that fails; log in `%APPDATA%\RetroBard\logs\update.log`).
Copies installed by the itch.io app are left to the itch.io app.

## Merging letters from testers

Testers export the letters they taught from the Letters tab ("Export letters...").
Merge their files into `profiles/<game>.font.json`:

```uv run retrobard merge-letters --game chrono_trigger ann.json bob.json```

It shows what would change; add `--write` to save. A shape is added only when every
tester who taught it agrees. Shapes they disagree on, or that contradict your font,
are printed (drawn with `#`) and left out so you can decide.

## License

RetroBard © 2026 Davide Aldegheri (Aze). Licensed under GPL-3.0-or-later, see LICENSE. Each build lists its third-party components in THIRD-PARTY-NOTICES.txt.

## Disclaimer

RetroBard is an independent fan project. It is not affiliated with, endorsed or sponsored by
Square Enix, Nintendo or ElevenLabs. Chrono Trigger © Square Enix. Super Nintendo Entertainment
System and SNES are trademarks of Nintendo. ElevenLabs is a trademark of ElevenLabs.
RetroBard includes no games, ROMs or BIOS files: use it only with games you own.
Voices are generated by AI.
