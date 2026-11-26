# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import time
from pathlib import Path

import typer

from retrobard.adapters.audio.pygame_out import PygameAudioPlayer
from retrobard.adapters.cache.sqlite_disk import SqliteDiskCache
from retrobard.adapters.capture.win32_window import Win32WindowSource
from retrobard.adapters.ocr.teacher import collect_dialogue_frames, teach_font
from retrobard.adapters.producers.calibrator import run_interactive_calibrator
from retrobard.adapters.tts.voice_designer import ElevenLabsVoiceDesigner
from retrobard.app.session import (
    InputSource,
    SessionError,
    SessionOptions,
    TTSBackend,
    VoiceSession,
    build_orchestrator,
    choose_rom,
    load_profile,
    profile_for_backend,
    profile_path,
)
from retrobard.app.setup import assign_voice as assign_voice_to_character
from retrobard.app.setup import load_font
from retrobard.config import AppSettings, GameProfile
from retrobard.credentials import elevenlabs_api_key
from retrobard.domain.models import DialogueEvent, EventType
from retrobard.domain.status import make_console_status

app = typer.Typer(help="RetroBard: Real-time AI voice acting pipeline for retro RPGs.")


def _fail(message: str) -> typer.Exit:
    typer.echo(message)
    return typer.Exit(code=1)


@app.command()
def gui() -> None:
    """Open the RetroBard window (setup, letter teaching, start/stop, tray icon)."""
    from retrobard.gui.main import main

    raise typer.Exit(code=main())


@app.command()
def calibrate(
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
    window: str | None = typer.Option(
        None,
        "--window",
        "-w",
        help="Part of the game window's title or exe name, e.g. 'snes9x' (asks if omitted)",
    ),
) -> None:
    """Link the game window, select its dialogue box, tune OCR thresholds, and save YAML."""
    settings = AppSettings()
    run_interactive_calibrator(settings, game, window_query=window)


@app.command()
def teach(
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
    boxes: int = typer.Option(
        25, "--boxes", "-n", help="How many dialogue boxes to collect before naming letters"
    ),
    frames: Path | None = typer.Option(
        None, "--frames", help="Use saved PNG frames of the game window instead of playing"
    ),
) -> None:
    """Teach RetroBard the game's letters: play through some dialogue, then name
    each new letter shape once. Run again any time to add letters it has not seen."""
    settings = AppSettings()
    try:
        profile = load_profile(settings, game)
    except SessionError as exc:
        raise _fail(str(exc)) from exc
    if profile.ocr.region is None or (profile.window is None and frames is None):
        raise _fail(f"Link the game window first. Run: retrobard calibrate --game {game}")

    game_font = load_font(settings, game)

    if frames is not None:
        import cv2

        collected = [cv2.imread(str(p)) for p in sorted(frames.glob("*.png"))]
        collected = [f for f in collected if f is not None]
    else:
        game_window = Win32WindowSource(profile.window)
        typer.echo(
            f"Play the game and step through dialogue. Collecting {boxes} boxes "
            f"(Ctrl+C when you have enough)..."
        )
        try:
            text_color = profile.ocr.text_color
            collected = collect_dialogue_frames(
                game_window,
                profile.ocr,
                boxes,
                echo=typer.echo,
                choose_text_color=not game_font.font.glyphs,
            )
            if profile.ocr.text_color != text_color:
                profile.save_to_yaml(profile_path(settings, game))
        finally:
            game_window.close()

    if not collected:
        raise _fail("No dialogue boxes were collected.")

    taught, remaining = teach_font(
        profile.ocr, game_font.font, game_font.path, collected, echo=typer.echo
    )
    typer.echo(
        f"\nSaved {game_font.path}: {len(game_font.font.glyphs)} letter shapes known "
        f"({taught} added now, {remaining} left unnamed)."
    )


@app.command()
def speak(
    text: str = typer.Argument(..., help="Raw dialogue line, e.g. 'CRONO: Wait up!'"),
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
    tts: TTSBackend = typer.Option(TTSBackend.MOCK, "--tts", help="TTS provider"),
) -> None:
    """Test the full Parser -> Cache -> TTS -> Audio pipeline from the terminal."""
    settings = AppSettings()
    try:
        profile = profile_for_backend(settings, load_profile(settings, game), tts)
        orchestrator = build_orchestrator(
            settings, profile, tts, log_text=True, status=make_console_status(verbose=True)
        )
    except SessionError as exc:
        raise _fail(str(exc)) from exc

    event = DialogueEvent(event_type=EventType.SPEAK, raw_text=text, source="cli")
    orchestrator._handle_speak(event)

    # Let the clip finish playing before the program exits
    while orchestrator.player.is_busy():
        time.sleep(0.05)
    time.sleep(0.15)


@app.command()
def run(
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
    tts: TTSBackend = typer.Option(TTSBackend.MOCK, "--tts", help="TTS provider"),
    source: InputSource = typer.Option(
        InputSource.OCR, "--source", "-s", help="Dialogue event producer"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also print the dialogue text that is read and spoken"
    ),
) -> None:
    """Start RetroBard reading the linked game window, a UDP listener, or both."""
    session = VoiceSession(
        AppSettings(),
        SessionOptions(game=game, tts=tts, source=source, verbose=verbose),
        status=make_console_status(verbose=verbose),
    )
    try:
        session.start()
    except SessionError as exc:
        raise _fail(str(exc)) from exc

    try:
        # Everything runs on background threads; keep the main thread alive for Ctrl+C
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        typer.echo("\nShutting down RetroBard...")
    finally:
        session.stop()


@app.command("set-rom")
def set_rom(
    rom: Path = typer.Argument(..., help="Your ROM file of the game"),
    game: str = typer.Option("secret_of_mana", "--game", "-g", help="Profile YAML name"),
) -> None:
    """Choose the ROM file for a game whose dialogue is read from its ROM.

    The path is saved for you only, not in the game's profile. The file must be
    the exact version the profile was made for."""
    settings = AppSettings()
    try:
        choose_rom(settings, game, rom)
    except SessionError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"ROM for {game} saved (in {settings.rom_paths_file}).")


@app.command("find-text")
def find_text(
    text: str = typer.Argument(
        ..., help="A piece of the line on screen right now, typed exactly, e.g. 'Welcome to'"
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="Search this file (a ROM or a saved dump) instead of RetroArch"
    ),
    save: Path | None = typer.Option(
        None, "--save", help="Also save the memory read from RetroArch to this file"
    ),
    size: str = typer.Option(
        "0x20000", "--size", help="Bytes of work RAM to read (SNES: 0x20000 = 128 KB)"
    ),
    port: int = typer.Option(55355, "--port", help="RetroArch network command port"),
    command: str = typer.Option(
        "read_core_ram", "--command", help="read_core_ram or read_core_memory"
    ),
    max_hits: int = typer.Option(20, "--max-hits", help="Matches to show per layout"),
) -> None:
    """Find where the game keeps a line of dialogue, without knowing its text encoding.

    Open a dialogue box in RetroArch, then run this with a few words you can see.
    Use words of one case where possible, and avoid long words that might be
    stored compressed."""
    from retrobard.adapters.producers.retroarch_memory import (
        COMMANDS,
        RetroArchError,
        RetroArchMemoryClient,
    )
    from retrobard.app.memory_search import (
        PREVIEW_AFTER,
        PREVIEW_BEFORE,
        SNES_WRAM_START,
        check_search_text,
        dump_ram,
        relative_search,
        strip_copier_header,
    )

    problem = check_search_text(text)
    if problem:
        raise _fail(problem)

    header = 0
    if file is not None:
        try:
            data, header = strip_copier_header(file.read_bytes())
        except OSError as exc:
            raise _fail(f"Could not read {file}: {exc}") from exc
        where = f"{file.name}" + (" (512-byte copier header skipped)" if header else "")
    else:
        if command not in COMMANDS:
            raise _fail(f"--command must be one of: {', '.join(COMMANDS)}")
        try:
            wanted = int(size, 0)
        except ValueError as exc:
            raise _fail(f"--size {size!r} is not a number (e.g. 0x20000)") from exc
        client = RetroArchMemoryClient("127.0.0.1", port, chunk_bytes=4096, command=command)
        try:
            typer.echo("Reading memory from RetroArch...")
            # RetroArch answers about one request per frame, so ask for big pieces
            data = dump_ram(client, wanted, chunk=4096)
        except RetroArchError as exc:
            raise _fail(
                f"Could not read RetroArch's memory ({exc}). Is a game running and "
                f"Network Commands turned on?"
            ) from exc
        finally:
            client.close()
        where = f"RetroArch work RAM ({len(data) // 1024} KB)"
        if save is not None:
            save.write_bytes(data)
            typer.echo(f"Saved the memory to {save}")

    is_ram = file is None and command == "read_core_ram"
    total = 0
    for stride in (1, 2):
        hits = relative_search(data, text, stride=stride)
        total += len(hits)
        if not hits:
            continue
        layout = "1 byte per letter" if stride == 1 else "2 bytes per letter (letter + attribute)"
        typer.echo(f"\n{len(hits)} match(es) in {where}, {layout}:")
        for hit in hits[:max_hits]:
            place = f"offset 0x{hit.offset + header:X}"
            if is_ram:
                place += f" (SNES ${SNES_WRAM_START + hit.offset:06X})"
            letters = ", ".join(
                f"'{first}' = {base:02X}"
                for first, base in (("A", hit.upper_a), ("a", hit.lower_a))
                if base is not None
            )
            preview = hit.decode(
                data,
                hit.offset - PREVIEW_BEFORE * stride,
                hit.offset + (len(text) + PREVIEW_AFTER) * stride,
            )
            typer.echo(f"  {place}  {letters}")
            typer.echo(f"    {preview}")
        if len(hits) > max_hits:
            typer.echo(f"  ... {len(hits) - max_hits} more (type more of the line to narrow it)")

    if total == 0:
        typer.echo(
            f"No match in {where}. Try a shorter piece of the line (common words may be "
            f"stored compressed), or search the ROM file with --file."
        )


@app.command()
def purge(
    speaker: str = typer.Argument(..., help="Character name to purge, e.g. 'FROG'"),
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
) -> None:
    """Delete cached audio files for a specific character after changing their voice_id."""
    settings = AppSettings()
    cache = SqliteDiskCache(settings.cache_dir)
    count = cache.purge_speaker(game, speaker)
    typer.echo(f"Purged {count} cached voice lines for {speaker.upper()} in {game}.")

@app.command("design-voice")
def design_voice(
    character: str = typer.Argument(
        ..., help="Character key in YAML, e.g. 'FROG', 'MOM', 'LUCCA', or 'MAGUS'"
    ),
    prompt: str = typer.Option(
        ...,
        "--prompt",
        "-p",
        help="Voice description (20+ chars), e.g. 'A warm, energetic 1990s anime mother with a caring yet stern tone.'",
    ),
    sample_text: str | None = typer.Option(
        None,
        "--text",
        "-t",
        help="Optional custom audition script (will be auto-padded if under 100 chars).",
    ),
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
) -> None:
    """
    Generate 3 custom voice previews from a text prompt, audition them in the terminal,
    and save your chosen voice directly to your ElevenLabs library and game YAML.
    """
    import pygame

    settings = AppSettings()
    game_profile_path = profile_path(settings, game)
    profile = GameProfile.from_yaml(game_profile_path)
    char_key = character.strip().upper()

    designer = ElevenLabsVoiceDesigner(api_key=elevenlabs_api_key(settings))
    player = PygameAudioPlayer()

    def _play_and_wait(audio_bytes: bytes) -> None:
        player.play_bytes(audio_bytes)
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(15)

    while True:
        typer.echo(f"\n🎨 Generating 3 voice previews for [{char_key}]...")
        previews = designer.generate_previews(prompt, sample_text)

        for prev in previews:
            typer.echo(
                f"  ▶ Playing Preview [{prev.index}] (id={prev.generated_voice_id[:10]}...)"
            )
            _play_and_wait(prev.audio_bytes)

        while True:
            choice = (
                typer.prompt(
                    "\nChoose [1/2/3] to save, [r1/r2/r3] to replay, [g] to regenerate, or [q] to quit"
                )
                .strip()
                .lower()
            )

            if choice in ("1", "2", "3"):
                selected = previews[int(choice) - 1]
                voice_label = f"{profile.game_id}_{char_key}"
                typer.echo(f"💾 Saving '{voice_label}' to your ElevenLabs Voice Library...")

                new_voice_id = designer.save_to_library(
                    voice_name=voice_label,
                    voice_description=prompt,
                    generated_voice_id=selected.generated_voice_id,
                )

                # Save the new voice and drop cached lines so it takes effect
                purged = assign_voice_to_character(
                    settings, game, profile, char_key, new_voice_id
                )

                typer.echo(
                    f"✅ Mapped {char_key} -> '{new_voice_id}' in {game_profile_path} "
                    f"(purged {purged} old cached lines)."
                )
                return

            elif choice in ("r1", "r2", "r3"):
                idx = int(choice[1]) - 1
                typer.echo(f"  ▶ Replaying Preview [{idx + 1}]...")
                _play_and_wait(previews[idx].audio_bytes)

            elif choice == "g":
                break  # Break inner loop to generate 3 fresh takes

            elif choice == "q":
                typer.echo("Cancelled without saving.")
                return

@app.command("assign-voice")
def assign_voice(
    character: str = typer.Argument(
        ..., help="Character key in YAML, e.g. 'FROG', 'MOM', 'LUCCA'"
    ),
    search: str | None = typer.Option(
        None,
        "--search",
        "-s",
        help="Filter voices by tag (e.g. 'female', 'british', 'characters', 'generated')",
    ),
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
) -> None:
    """
    [Free-Tier Friendly] Browse and audition voices in your account (0 credits)
    and assign one to a character in your game YAML.
    """
    import pygame

    settings = AppSettings()
    game_profile_path = profile_path(settings, game)
    profile = GameProfile.from_yaml(game_profile_path)
    char_key = character.strip().upper()

    designer = ElevenLabsVoiceDesigner(api_key=elevenlabs_api_key(settings))
    player = PygameAudioPlayer()

    voices = designer.list_available_voices(search=search)
    if not voices:
        typer.echo(f"No voices matched filter: {search!r}")
        return

    typer.echo(f"\n🎙️ Available Voices for [{char_key}] ({len(voices)} found):")
    for idx, v in enumerate(voices, start=1):
        tag_str = ", ".join(f"{k}={val}" for k, val in v.labels.items())
        typer.echo(
            f"  [{idx:2d}] {v.name:<14} ({v.category}) | {tag_str} | id={v.voice_id[:10]}..."
        )

    while True:
        cmd = (
            typer.prompt(
                "\nEnter [p<num>] to preview for free (e.g. 'p1'), [<num>] to assign & save, or [q] to quit"
            )
            .strip()
            .lower()
        )

        if cmd == "q":
            return

        if cmd.startswith("p") and cmd[1:].isdigit():
            idx = int(cmd[1:]) - 1
            if 0 <= idx < len(voices):
                target = voices[idx]
                if not target.preview_url:
                    typer.echo("  No preview URL available for this voice.")
                    continue
                typer.echo(f"  ▶ Playing free preview for {target.name} (0 credits)...")
                audio_bytes = designer.download_free_preview(target.preview_url)
                player.play_bytes(audio_bytes)
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(15)
            continue

        if cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(voices):
                selected = voices[idx]
                purged = assign_voice_to_character(
                    settings, game, profile, char_key, selected.voice_id
                )
                typer.echo(
                    f"✅ Assigned {selected.name} ('{selected.voice_id}') to {char_key} "
                    f"in {game_profile_path} (purged {purged} cached lines)."
                )
                return


if __name__ == "__main__":
    app()

@app.command("merge-letters")
def merge_letters_command(
    files: list[Path] = typer.Argument(..., help="Letter files exported by testers (.json)"),
    game: str = typer.Option("chrono_trigger", "--game", "-g", help="Profile YAML name"),
    write: bool = typer.Option(
        False, "--write", help="Save the result (without it, only show what would change)"
    ),
) -> None:
    """Add letters exported by testers to the game's font. Shapes testers disagree
    on, or that contradict your font, are listed and left out."""
    from retrobard.adapters.ocr.font_merge import (
        Contribution,
        describe_char,
        merge_letters,
        render_shape,
    )
    from retrobard.adapters.ocr.glyph_reader import GlyphFont
    from retrobard.app.session import font_path

    settings = AppSettings()
    target = font_path(settings, game)
    contributions = []
    for path in files:
        if not path.exists():
            raise _fail(f"File not found: {path}")
        contributions.append(Contribution(name=path.name, font=GlyphFont.load(path)))

    result = merge_letters(GlyphFont.load(target), contributions)

    for warning in result.warnings:
        typer.echo(f"Warning: {warning}")
    typer.echo(
        f"{len(result.added)} new shape(s) added, {result.already_known} already known, "
        f"{len(result.conflicts)} conflict(s) left out."
    )
    for key, char in sorted(result.added.items(), key=lambda item: item[1]):
        typer.echo(f"  + {describe_char(char)}")
    for key, by_char in result.conflicts.items():
        typer.echo("\nConflict, not added:")
        typer.echo(render_shape(key))
        for char, names in by_char.items():
            typer.echo(f"  {describe_char(char)}: {', '.join(names)}")

    if not write:
        typer.echo("\nNothing saved. Run again with --write to save.")
        return
    result.merged.save(target)
    typer.echo(f"\nSaved {target} ({len(result.merged.glyphs)} letter shapes).")
