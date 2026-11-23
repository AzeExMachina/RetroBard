# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import cv2
import numpy as np

from retrobard.adapters.capture.win32_window import (
    Win32WindowSource,
    WindowInfo,
    find_window,
    list_windows,
)
from retrobard.adapters.producers.ocr_watcher import preprocess_retro_frame
from retrobard.app.session import profile_path as game_profile_path
from retrobard.app.setup import (
    binding_for,
    is_blank,
    is_multi_window_program,
    load_or_create_profile,
    needs_title_filter,
)
from retrobard.config import AppSettings, GameProfile, NormRect, WindowBinding


def choose_window(
    existing: WindowBinding | None, query: str | None = None
) -> WindowBinding | None:
    """Asks which open window is the game. Returns None if the user cancels."""
    if existing and not query:
        current = find_window(existing)
        if isinstance(current, WindowInfo):
            answer = input(
                f"Keep the linked game window '{current.title}' "
                f"({current.process_name})? [Y/n] "
            )
            if answer.strip().lower() in ("", "y", "yes"):
                return existing

    open_windows = list_windows()
    windows = open_windows
    if query:
        needle = query.strip().lower()
        windows = [
            w
            for w in windows
            if needle in w.title.lower() or needle in w.process_name.lower()
        ]
        if len(windows) == 1:
            return _binding_for(windows[0], open_windows)

    if not windows:
        print("No matching window found. Start the game/emulator first.")
        return None

    print("\nWhich window is the game?")
    for idx, win in enumerate(windows, start=1):
        print(f"  [{idx:2d}] {win.title[:60]:<60} ({win.process_name})")

    while True:
        answer = input("Enter a number, or [q] to quit: ").strip().lower()
        if answer == "q":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(windows):
            return _binding_for(windows[int(answer) - 1], open_windows)


def _binding_for(window: WindowInfo, open_windows: list[WindowInfo]) -> WindowBinding | None:
    """Binds by executable (titles change with the loaded ROM / FPS counter), plus a
    title filter when several windows share it. None if the user cancels."""
    if not needs_title_filter(window, open_windows):
        return binding_for(window, open_windows)
    exe = window.process_name
    print(
        f"Several windows belong to {exe}. Type part of the game window's title "
        f"that stays the same, such as the game's name."
    )
    answer = input(f"Title filter (Enter = {window.title!r}, - = none, q = cancel): ").strip()
    if answer.lower() == "q":
        return None
    title_filter = window.title if not answer else "" if answer == "-" else answer
    if not title_filter.strip():
        if is_multi_window_program(window):
            print("Cancelled: this program needs a title filter.")
            return None
        print(f"Without a title filter, nothing is read while more than one {exe} window is open.")
        if input("Link without a filter? [y/N] ").strip().lower() not in ("y", "yes"):
            return None
    return binding_for(window, open_windows, title_filter)


def _select_region(title: str, frame_bgr: np.ndarray, min_size: int) -> NormRect | None:
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    x, y, w, h = (int(v) for v in cv2.selectROI(title, frame_bgr, showCrosshair=True, fromCenter=False))
    cv2.destroyWindow(title)
    if w <= min_size or h <= min_size:
        return None
    frame_h, frame_w = frame_bgr.shape[:2]
    return NormRect.from_pixels(x, y, w, h, frame_w, frame_h)


def run_interactive_calibrator(
    settings: AppSettings, game: str, window_query: str | None = None
) -> None:
    """Terminal/OpenCV version of the setup flow, built on the shared setup functions."""
    profile_path = game_profile_path(settings, game)
    profile = load_or_create_profile(settings, game)

    binding = choose_window(profile.window, window_query)
    if binding is None:
        print("Exited calibration without saving.")
        return
    profile.window = binding

    # Only the chosen game window is ever captured, never the whole screen
    source = Win32WindowSource(binding)
    try:
        _calibrate_with_source(profile, profile_path, source)
    finally:
        source.close()
        cv2.destroyAllWindows()


def _calibrate_with_source(
    profile: GameProfile, profile_path: Path, source: Win32WindowSource
) -> None:
    first_frame = source.grab()
    if first_frame is None:
        print("Could not capture the game window. Make sure it is open and not minimised.")
        return
    if is_blank(first_frame):
        # Some renderers hand back an empty surface instead of the game picture
        print(
            "The capture of the game window came back blank (a single flat colour), "
            "so there is nothing to calibrate on. Nothing was saved."
        )
        return
    window_bgr = cv2.cvtColor(first_frame, cv2.COLOR_BGRA2BGR)
    print(
        "\nA snapshot of the game window opens in a separate window.\n"
        "Drag a box around the dialogue area on that snapshot, then press ENTER."
    )

    # Step 1a: Main Dialogue Box ROI
    region = _select_region(
        "Step 1a: Drag MAIN Dialogue Box & Press ENTER (ESC to keep existing)",
        window_bgr,
        min_size=20,
    )
    if region:
        profile.ocr.region = region
    if profile.ocr.region is None:
        print("No dialogue box selected. Exited calibration without saving.")
        return

    # Step 1b: Optional Speaker Nameplate Box ROI
    speaker_region = _select_region(
        "Step 1b: Drag SPEAKER Nameplate & Press ENTER (Press ESC if none)",
        window_bgr,
        min_size=10,
    )
    if speaker_region:
        profile.ocr.speaker_region = speaker_region

    # Step 2: Live Tuner
    tuner_win = "Step 2: Live Tuner | [T] Test OCR | [X] Clear Speaker Box | [S] Save | [Q] Quit"
    cv2.namedWindow(tuner_win, cv2.WINDOW_NORMAL)

    init_spk_thresh = (
        profile.ocr.speaker_binary_threshold
        if profile.ocr.speaker_binary_threshold is not None
        else profile.ocr.binary_threshold
    )

    cv2.createTrackbar("Threshold", tuner_win, profile.ocr.binary_threshold, 255, lambda _: None)
    cv2.createTrackbar("Spk Thresh", tuner_win, init_spk_thresh, 255, lambda _: None)
    cv2.createTrackbar("Upscale", tuner_win, profile.ocr.upscale_factor, 5, lambda _: None)
    cv2.createTrackbar(
        "Mask %", tuner_win, int(profile.ocr.cursor_mask_ratio * 100), 40, lambda _: None
    )

    reader = None
    proc_dlg: np.ndarray | None = None
    proc_spk: np.ndarray | None = None

    print("\n--- Calibration Controls ---")
    print("  [T] Run EasyOCR on Speaker + Dialogue boxes")
    print("  [X] Disable/Remove separate Speaker Box")
    print("  [S] Save settings to", profile_path)
    print("  [Q] or [ESC] Quit without saving\n")

    while True:
        thresh_val = cv2.getTrackbarPos("Threshold", tuner_win)
        spk_thresh_val = cv2.getTrackbarPos("Spk Thresh", tuner_win)
        upscale_val = max(1, cv2.getTrackbarPos("Upscale", tuner_win))
        mask_pct = cv2.getTrackbarPos("Mask %", tuner_win) / 100.0

        window_frame = source.grab()
        if window_frame is not None:
            # Process Main Dialogue Box
            proc_dlg = preprocess_retro_frame(
                profile.ocr.region.crop(window_frame),
                upscale_factor=upscale_val,
                binary_threshold=thresh_val,
                cursor_mask_ratio=mask_pct,
            )
            preview_dlg = cv2.cvtColor(proc_dlg, cv2.COLOR_GRAY2BGR)
            ph, pw, _ = preview_dlg.shape
            if mask_pct > 0:
                mh = int(ph * (mask_pct * 1.5))
                mw = int(pw * mask_pct)
                cv2.rectangle(preview_dlg, (pw - mw, ph - mh), (pw - 8, ph - 8), (0, 0, 255), 2)

            # Process Speaker Box (if enabled) and stack above Main Dialogue Preview
            proc_spk = None
            if profile.ocr.speaker_region:
                proc_spk = preprocess_retro_frame(
                    profile.ocr.speaker_region.crop(window_frame),
                    upscale_factor=upscale_val,
                    binary_threshold=spk_thresh_val,
                    cursor_mask_ratio=0.0,
                )
                preview_spk = cv2.cvtColor(proc_spk, cv2.COLOR_GRAY2BGR)
                # Pad width to match main dialogue preview so we can stack vertically
                sh_p, sw_p, _ = preview_spk.shape
                canvas = np.zeros((sh_p + 4, pw, 3), dtype=np.uint8)
                clip_w = min(sw_p, pw)
                canvas[:sh_p, :clip_w] = preview_spk[:, :clip_w]
                cv2.line(canvas, (0, sh_p + 2), (pw, sh_p + 2), (0, 200, 255), 2)
                combined_preview = np.vstack([canvas, preview_dlg])
            else:
                combined_preview = preview_dlg

            cv2.imshow(tuner_win, combined_preview)

        key = cv2.waitKey(50) & 0xFF

        if key in (ord("t"), ord("T")) and proc_dlg is not None:
            if reader is None:
                print("Loading EasyOCR model...")
                import easyocr

                reader = easyocr.Reader(["en"], gpu=True, verbose=False)

            spk_out = None
            if proc_spk is not None:
                spk_lines = reader.readtext(proc_spk, detail=0, paragraph=True)
                spk_out = " ".join(spk_lines).strip()

            dlg_lines = reader.readtext(proc_dlg, detail=0, paragraph=True)
            dlg_out = " ".join(dlg_lines).strip()
            print(f"[OCR TEST] Speaker={spk_out!r} | Dialogue={dlg_out!r}")

        elif key in (ord("x"), ord("X")):
            profile.ocr.speaker_region = None
            profile.ocr.speaker_binary_threshold = None
            print("Removed separate speaker box.")

        elif key in (ord("s"), ord("S")):
            profile.ocr.binary_threshold = thresh_val
            profile.ocr.upscale_factor = upscale_val
            profile.ocr.cursor_mask_ratio = round(mask_pct, 2)
            if profile.ocr.speaker_region:
                profile.ocr.speaker_binary_threshold = spk_thresh_val

            profile.save_to_yaml(profile_path)
            print(f"Saved calibrated profile to {profile_path}")
            break

        elif key in (ord("q"), ord("Q"), 27):
            print("Exited calibration without saving.")
            break

    cv2.destroyAllWindows()
