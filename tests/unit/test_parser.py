# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
# tests/unit/test_parser.py
import pytest

from retrobard.config import GameProfile, VoicesConfig
from retrobard.domain.models import AudioEffect
from retrobard.domain.parser import DialogueParser


class TestSpeakerExtraction:
    @pytest.mark.parametrize(
        "raw_input, expected_speaker, expected_text",
        [
            # Standard colon delimiter
            ("CRONO: Wait up, Marle!", "CRONO", "Wait up, Marle!"),
            # OCR-mangled semicolon delimiter + leading asterisk artifact
            ("* MOM; Let's get moving; now!", "MOM", "Let's get moving, now!"),
            # OCR-mangled pipe delimiter
            ("FROG | My name is Glenn!", "FROG", "My name is Glenn!"),
            # Multi-line header where colon was dropped on line 1
            ("MARLE\nWhere are we going?", "MARLE", "Where are we going?"),
            # Single-line dropped colon (ALL-CAPS speaker followed by Mixed-Case dialogue)
            ("MOM Let's get moving, now!", "MOM", "Let's get moving, now!"),
            # Name written flush against the text, over two lines (Secret of Mana)
            ("MAN:Hang on!\nI'll help you!", "MAN", "Hang on! I'll help you!"),
        ],
    )
    def test_inline_speaker_variants(
        self,
        sample_profile: GameProfile,
        raw_input: str,
        expected_speaker: str,
        expected_text: str,
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse(raw_input)

        assert result is not None
        assert result.speaker == expected_speaker
        assert result.clean_text == expected_text

    def test_fuzzy_and_deleet_speaker_matching(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)

        # '0' -> 'O' de-leeting
        res_crono = parser.parse("CR0N0: Let's go!")
        assert res_crono is not None
        assert res_crono.speaker == "CRONO"
        assert res_crono.voice_id == "voice_crono_123"

        # '1' -> 'I' / fuzzy match against MARLE
        res_marle = parser.parse("MAR1E: Watch out!")
        assert res_marle is not None
        assert res_marle.speaker == "MARLE"
        assert res_marle.voice_id == "voice_marle_456"

    def test_explicit_speaker_overrides_inline(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse("My systems are operational.", explicit_speaker="ROBO")

        assert result is not None
        assert result.speaker == "ROBO"
        assert result.voice.effect == AudioEffect.ROBOT
        assert result.voice.pitch_semitones == -0.5


class TestSpeakerFalsePositives:
    @pytest.mark.parametrize(
        "raw_input",
        [
            # An exclamation is not a speaker delimiter
            "Wait! I'm coming with you.",
            # Calling out a known character's name is not that character speaking
            "Crono! Wake up, sleepyhead!",
            # A mangled semicolon after ordinary words must not invent a speaker
            "He said; go away and never come back.",
            # A time is not a speaker followed by text
            "10:30 is when the ferry leaves.",
        ],
    )
    def test_ordinary_sentences_keep_their_full_text(
        self, sample_profile: GameProfile, raw_input: str
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse(raw_input)

        assert result is not None
        assert result.speaker == "NARRATOR"
        assert result.clean_text == raw_input.replace(";", ",")

    def test_unmapped_npc_names_are_still_detected(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse("Old Man: The bridge is out.")

        assert result is not None
        assert result.speaker == "OLD MAN"
        assert result.clean_text == "The bridge is out."


class TestMergedPronoun:
    @pytest.mark.parametrize(
        "raw_input, expected_text",
        [
            ("Iam not afraid.", "I am not afraid."),
            ("Iwill find it.", "I will find it."),
            ("Imissed you!", "I missed you!"),
            ("Idon't know.", "I don't know."),
            ("Isn't it late?", "Isn't it late?"),
            # Real words starting with I stay whole
            ("Its roots are deep.", "Its roots are deep."),
            ("Instead, go north.", "Instead, go north."),
            ("Into the forest!", "Into the forest!"),
        ],
    )
    def test_only_a_merged_pronoun_is_split(
        self, sample_profile: GameProfile, raw_input: str, expected_text: str
    ) -> None:
        result = DialogueParser(sample_profile).parse(raw_input)

        assert result is not None
        assert result.clean_text == expected_text


class TestSanitizationAndGibberishFilter:
    @pytest.mark.parametrize("line", ["Hmm...", "Shh!", "Zzz...", "Psst! Hmmmm."])
    def test_vowelless_interjections_are_dialogue(
        self, sample_profile: GameProfile, line: str
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse(line)

        assert result is not None
        assert result.clean_text == line

    def test_chronotrigger_ocr_quirks_repaired(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)
        result = parser.parse(
            "Iheard this guy pays big bucks; Have fun:"
        )

        assert result is not None
        assert result.clean_text == "I heard this guy pays big bucks, Have fun!"

    @pytest.mark.parametrize(
        "tile_noise",
        [
            "+ + L Y Y Yyvy Y\nY 4 V Y",
            "Y # # V Y # ky ky Y\nYy V h",
            "Hy YY K yyk\n00. ~0m\nH y Y ky ky Y",
            "|| -- ++ **",
            "x",
        ],
    )
    def test_rejects_map_tile_hallucinations(
        self, sample_profile: GameProfile, tile_noise: str
    ) -> None:
        parser = DialogueParser(sample_profile)
        assert parser.parse(tile_noise) is None


class TestMultiBoxContextAndVoicePools:
    def test_carries_speaker_across_continuation_boxes_and_exempts_system_msgs(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)

        # Box 1: Mom speaks with prefix
        box1 = parser.parse("MOM: Crono, wake up!")
        assert box1 is not None
        assert box1.speaker == "MOM"
        assert box1.voice_id in sample_profile.voices.female_pool

        # Box 2: Continuation box without 'MOM:' prefix inherits MOM
        box2 = parser.parse("You are going to be late for the Millennial Fair!")
        assert box2 is not None
        assert box2.speaker == "MOM"
        assert box2.voice_id == box1.voice_id

        # Mid-conversation item pop-up uses NARRATOR without erasing MOM context
        item_box = parser.parse("Got 200 G!")
        assert item_box is not None
        assert item_box.speaker == "NARRATOR"
        assert item_box.voice_id == "voice_narrator_default"

        # Box 3: Mom continues speaking after the item pop-up
        box3 = parser.parse("Don't spend it all in one place!")
        assert box3 is not None
        assert box3.speaker == "MOM"

        # Dialogue window closes -> context resets to NARRATOR
        parser.reset_context()
        unnamed_npc = parser.parse("Welcome to the Millennial Fair!")
        assert unnamed_npc is not None
        assert unnamed_npc.speaker == "NARRATOR"

    def test_an_alias_speaks_exactly_like_its_character(self, sample_profile: GameProfile) -> None:
        sample_profile.voices.aliases = {"GIRL": "MOM"}
        parser = DialogueParser(sample_profile)
        mom = parser.parse("MOM: Crono, wake up!")
        girl = parser.parse("GIRL: Ouch, that hurt!")
        assert girl is not None and mom is not None
        assert girl.speaker == "MOM"
        assert girl.voice == mom.voice
        # Unnamed lines after it carry on as her
        assert parser.parse("I'm so sorry!").speaker == "MOM"

    def test_aliases_reach_the_custom_server_voices(self, sample_profile: GameProfile) -> None:
        sample_profile.voices.aliases = {"GIRL": "MOM"}
        voices = sample_profile.voices_for_custom_server("af_heart")
        assert voices.aliases == {"GIRL": "MOM"}

    def test_a_named_line_too_short_to_speak_still_sets_the_speaker(
        self, sample_profile: GameProfile
    ) -> None:
        sample_profile.voices.narrate_unnamed = False
        parser = DialogueParser(sample_profile)
        assert parser.parse("MOM:!?") is None  # nothing to say out loud
        follow_up = parser.parse("Say what?")
        assert follow_up is not None
        assert follow_up.speaker == "MOM"

    def test_deterministic_npc_pool_and_tuning_variation(
        self, sample_profile: GameProfile
    ) -> None:
        parser = DialogueParser(sample_profile)

        # Unmapped male NPC ('TABAN') always hashes to the exact same voice & settings
        first = parser.parse("TABAN: I made this vest for Lucca!")
        second = parser.parse("TABAN: Try it on!")
        assert first is not None and second is not None
        assert first.voice_id in sample_profile.voices.male_pool
        assert first.voice == second.voice

class TestChoiceSkipping:
    @pytest.mark.parametrize(
        "raw_input, expected_text",
        [
            ("Stay the night? 10 G.\nWill you rest?\n  Yes\n  No", "Stay the night? 10 G. Will you rest?"),
            ("CRONO: Want to come along?\n> Yes\n  No", "Want to come along?"),
            ("Welcome! What do you need?\nBuy\nSell\nExit", "Welcome! What do you need?"),
            # Real Chrono Trigger read: answers end with a period
            (
                "Want to exchange 10 Silver Points\nfor 50 G?\nYes.\nNo.",
                "Want to exchange 10 Silver Points for 50 G?",
            ),
        ],
    )
    def test_choice_lines_are_not_spoken(
        self, sample_profile: GameProfile, raw_input: str, expected_text: str
    ) -> None:
        result = DialogueParser(sample_profile).parse(raw_input)

        assert result is not None
        assert result.clean_text == expected_text

    @pytest.mark.parametrize(
        "raw_input",
        [
            # A question followed by normal dialogue
            "Are you ready?\nLet's go!",
            # Only one short line after the question
            "Ready?\nYes",
            # Short lines that do not follow a question
            "Look out\nBehind you\nCrono",
            # Wrapped sentence continuing in lower case
            "Did you see\nthat thing over\nthere",
        ],
    )
    def test_regular_dialogue_is_kept(
        self, sample_profile: GameProfile, raw_input: str
    ) -> None:
        result = DialogueParser(sample_profile).parse(raw_input)

        assert result is not None
        assert result.clean_text == " ".join(raw_input.split())

    def test_can_be_disabled(self, sample_profile: GameProfile) -> None:
        sample_profile.dialogue_rules.skip_choices = False
        result = DialogueParser(sample_profile).parse("Will you rest?\nYes\nNo")

        assert result is not None
        assert result.clean_text == "Will you rest? Yes No"


class TestPlaceNames:
    @pytest.mark.parametrize(
        "banner", ["Residence", "Leene Square", "Mt. Woe", "End of Time", "Lucca's House", "Zenan Bridge"]
    )
    def test_a_place_name_is_not_spoken_or_given_to_the_last_speaker(
        self, sample_profile: GameProfile, banner: str
    ) -> None:
        sample_profile.dialogue_rules.skip_place_names = True
        parser = DialogueParser(sample_profile)
        assert parser.parse("MOM: Have fun at the fair!") is not None
        assert parser.parse(banner) is None
        # The conversation itself carries on as before
        assert parser.parse("Don't be late!").speaker == "MOM"

    @pytest.mark.parametrize(
        "line",
        [
            "Crono.",
            "Hey!!",
            "Uh oh...",
            "Over here, Crono",
            "Is it you?",
            "Go to the",
            "Leene Square\nis that way",
        ],
    )
    def test_dialogue_is_still_spoken(self, sample_profile: GameProfile, line: str) -> None:
        sample_profile.dialogue_rules.skip_place_names = True
        parser = DialogueParser(sample_profile)
        parser.parse("MOM: Have fun at the fair!")
        result = parser.parse(line)
        assert result is not None and result.speaker == "MOM"

    def test_named_lines_are_never_place_names(self, sample_profile: GameProfile) -> None:
        sample_profile.dialogue_rules.skip_place_names = True
        result = DialogueParser(sample_profile).parse("MARLE: Leene Square")
        assert result is not None and result.speaker == "MARLE"

    def test_off_by_default(self, sample_profile: GameProfile) -> None:
        parser = DialogueParser(sample_profile)
        parser.parse("MOM: Have fun at the fair!")
        assert parser.parse("Leene Square").speaker == "MOM"


class TestNarratorOption:
    def test_narrator_is_off_for_a_new_profile(self) -> None:
        assert VoicesConfig(default_narrator="voice").narrate_unnamed is False

    def test_unnamed_lines_use_the_narrator_when_turned_on(self, sample_profile: GameProfile) -> None:
        result = DialogueParser(sample_profile).parse("The fair is in full swing!")

        assert result is not None
        assert result.speaker == "NARRATOR"
        assert result.voice_id == "voice_narrator_default"

    def test_unnamed_lines_are_silent_when_narrator_is_off(self, sample_profile: GameProfile) -> None:
        sample_profile.voices.narrate_unnamed = False
        parser = DialogueParser(sample_profile)

        assert parser.parse("The fair is in full swing!") is None
        assert parser.parse("Got 1 Tonic!") is None

    def test_named_speakers_still_speak_when_narrator_is_off(self, sample_profile: GameProfile) -> None:
        sample_profile.voices.narrate_unnamed = False
        parser = DialogueParser(sample_profile)

        first = parser.parse("MARLE: Come on, Crono!")
        continued = parser.parse("Let's go see the fair!")

        assert first is not None and first.speaker == "MARLE"
        assert continued is not None and continued.speaker == "MARLE"
