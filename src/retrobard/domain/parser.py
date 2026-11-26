# Copyright (c) 2026 Davide Aldegheri
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import re
import time
from dataclasses import dataclass
from difflib import get_close_matches

from retrobard.config import GameProfile
from retrobard.domain.models import VoiceProfile
from retrobard.domain.status import StatusKind, StatusSink, null_status, report

FEMALE_HINTS = {
    "MOM", "MOTHER", "QUEEN", "PRINCESS", "GIRL", "WOMAN", "LADY",
    "MAID", "SISTER", "AUNT", "GRANNY", "WAITRESS", "NURSE",
    "LEENE", "GINA", "FIONA", "LARA", "SCHALA", "AZALA", "FLEA",
}

# Vowel-less interjections with repeated letters collapsed ("hmmm" -> "hm")
VOWELLESS_INTERJECTIONS = {
    "hm", "hmph", "mhm", "m", "sh", "pst", "pft", "pf", "psh",
    "z", "br", "gr", "tsk", "tch", "ngh", "nh",
}

# A choice line: at most 4 short words, optional cursor/bullet in front, starting
# with a capital, optionally ending in "." or "!" ("Yes", "> No", "Yes.", "Buy")
CHOICE_LINE_RE = re.compile(r"^[^A-Za-z0-9]{0,3}[A-Z0-9][\w'\-]*(?: [\w'\-]+){0,3}[.!]?$")
CHOICE_LINE_MAX_CHARS = 24

# A place name shown on its own: one line of 1-4 capitalized words, with no
# sentence punctuation ("Residence", "Leene Square", "Mt. Woe", "End of Time").
# Only an abbreviation inside it may end with a period, so "Crono." stays dialogue.
PLACE_NAME_MAX_WORDS = 4
CONNECTOR_WORDS = {"of", "the", "de", "du", "la", "le"}

# Words that often follow "I" in dialogue. OCR can merge the two ("Iheard",
# "Idon't"), and none of them makes a real word with an "I" in front, so the
# merged form is always split again. Contractions are listed by their stem
# ("don" for "don't"), since the apostrophe ends the match.
WORDS_AFTER_I = frozenset(
    ["am", "was", "will", "would", "have", "had", "can", "cannot", "could", "should", "shall", "must", "might", "may", "need", "needed", "think", "thought", "know", "knew", "want", "wanted", "see", "saw", "hear", "heard", "guess", "guessed", "mean", "meant", "did", "do", "feel", "felt", "hope", "hoped", "wish", "wished", "said", "say", "told", "tell", "got", "get", "just", "never", "really", "also", "still", "only", "always", "often", "already", "love", "loved", "like", "liked", "hate", "hated", "believe", "found", "find", "came", "come", "went", "go", "made", "make", "tried", "try", "remember", "forgot", "forget", "promise", "promised", "swear", "bet", "wonder", "suppose", "missed", "miss", "lost", "left", "met", "saved", "owe", "beg", "understand", "understood", "agree", "asked", "ask", "called", "thank", "figured", "realized", "decided", "used", "don", "didn", "won", "wasn", "couldn", "wouldn", "shouldn", "haven", "hadn"]
)

NPC_SPEED_VARIANTS =[0.88, 0.94, 1.00, 1.06, 1.12]
NPC_STABILITY_VARIANTS = [0.35, 0.50, 0.70]
NPC_PITCH_VARIANTS = [-1.5, -0.8, 0.0, 0.8, 1.5]


@dataclass(frozen=True)
class ParsedDialogue:
    speaker: str
    clean_text: str
    voice: VoiceProfile

    @property
    def voice_id(self) -> str:
        return self.voice.voice_id


class DialogueParser:
    def __init__(
        self,
        profile: GameProfile,
        context_ttl_s: float = 25.0,
        status: StatusSink = null_status,
    ):
        self.profile = profile
        self.status = status
        self.context_ttl_s = context_ttl_s

        # Conversation state across multi-box monologues
        self._active_speaker: str | None = None
        self._last_speaker_time: float = 0.0

        self._compiled_strips = [
            re.compile(pattern) for pattern in profile.dialogue_rules.strip_patterns
        ]
        self._whitespace_re = re.compile(r"\s+")
        self._non_alnum_re = re.compile(r"[^A-Z0-9 _\-]")
        # OCR sometimes merges "I am" into "Iam". Only split before words that
        # commonly follow "I", so real words such as "Its", "Into" or "Instead"
        # stay whole.
        self._merged_pronoun_re = re.compile(
            r"\bI(" + "|".join(sorted(WORDS_AFTER_I, key=len, reverse=True)) + r")\b"
        )
        self._known_characters = list(profile.voices.characters.keys())

        # Matches "MOM: Hello", "MOM; Hello", "GUARD 1 | Halt!", and names written
        # flush against the text ("MAN:Hang on!", as Secret of Mana does). Without a
        # space, a digit after the colon is a time or score ("10:30"), not a name.
        self._inline_delim_re = re.compile(
            r"^\s*([A-Za-z0-9\.\-\' ]{2,18}?)\s*[:;\|](?:\s+|(?=[^\s\d]))(.+)$", re.DOTALL
        )
        self._word_re = re.compile(r"[A-Za-z]+")
        self._repeat_re = re.compile(r"(.)\1+")
        # A line of a box where a second character starts talking: "POPOI:Hey!"
        # (cursor or bullet marks in front allowed). The name is checked further
        # in _starts_new_speaker.
        self._speaker_line_re = re.compile(
            r"^[\*\-\|\s>]*([A-Za-z0-9\.\-\' ]{2,18}?)\s*[:;\|](?:\s+|(?=[^\s\d]))\S"
        )
        # Matches dropped-colon cases ("MOM Let's get moving!")
        self._allcaps_prefix_re = re.compile(
            r"^\s*([A-Z0-9]{2,12})\s+([A-Z][a-z].+)$", re.DOTALL
        )
        # Matches RPG item/system prompts that should always use NARRATOR
        self._system_msg_re = re.compile(
            r"^(Got |Received |Obtained |Found |Learned |Joined |Locked|Nothing |HP |MP )",
            re.IGNORECASE,
        )

    def reset_context(self) -> None:
        """Clears the remembered speaker when the dialogue window closes."""
        if self._active_speaker is not None:
            report(
                self.status,
                StatusKind.DEBUG,
                f"[PARSER] Dialogue box closed -> Reset speaker ({self._active_speaker} -> NARRATOR)",
            )
            self._active_speaker = None
            self._last_speaker_time = 0.0

    def split_speakers(self, raw_text: str) -> list[str]:
        """Splits a box where a later line starts with another character's name.

        "RANDI:Look!\\nPOPOI:Where?" -> ["RANDI:Look!", "POPOI:Where?"]. Only a
        line that begins with an all-caps name (or a character the profile
        voices) and a delimiter starts a new part, so a sentence that merely
        wraps onto a line with a colon in it stays whole."""
        lines = raw_text.split("\n")
        parts: list[list[str]] = [[lines[0]]]
        for line in lines[1:]:
            if self._starts_new_speaker(line):
                parts.append([line])
            else:
                parts[-1].append(line)
        return ["\n".join(part) for part in parts]

    def _starts_new_speaker(self, line: str) -> bool:
        match = self._speaker_line_re.match(line)
        if not match:
            return False
        name = match.group(1).strip()
        letters = [c for c in name if c.isalpha()]
        return bool(letters) and (
            all(c.isupper() for c in letters) or self._is_known_character(name)
        )

    def parse_all(
        self, raw_text: str, explicit_speaker: str | None = None
    ) -> list[ParsedDialogue]:
        """Parses a box that may hold several characters talking in turn; each
        part is returned in order. `explicit_speaker` applies to the first part."""
        results = []
        for index, part in enumerate(self.split_speakers(raw_text or "")):
            parsed = self.parse(part, explicit_speaker if index == 0 else None)
            if parsed:
                results.append(parsed)
        return results

    def parse(
        self, raw_text: str, explicit_speaker: str | None = None
    ) -> ParsedDialogue | None:
        if not raw_text or self._is_gibberish(raw_text):
            return None
        if self.profile.dialogue_rules.skip_choices:
            raw_text = self._drop_choice_lines(raw_text)

        now = time.monotonic()
        speaker_key = (
            self._resolve_speaker_name(explicit_speaker) if explicit_speaker else None
        )
        # Drop leading OCR artifacts ("* MOM: ...") before looking for a speaker prefix
        body_text = raw_text.strip()
        for pattern in self._compiled_strips:
            body_text = pattern.sub("", body_text).strip()

        if not speaker_key:
            speaker_key, body_text = self._extract_inline_speaker(body_text)

        clean_text = self._sanitize(body_text)
        if (
            not speaker_key
            and self.profile.dialogue_rules.skip_place_names
            and self._is_place_name(body_text)
        ):
            # Not part of the conversation: neither spoken nor given to the last speaker
            report(self.status, StatusKind.DEBUG, f"[PARSER] Place name, not spoken: {clean_text!r}")
            return None
        if (
            len(clean_text) < self.profile.dialogue_rules.min_length
            or self._is_gibberish(clean_text)
        ):
            if speaker_key:
                # Nothing to say ("RANDI: !?"), but the unnamed lines that follow
                # are still this character talking
                self._active_speaker = speaker_key
                self._last_speaker_time = now
            return None

        # Decide final speaker: explicit -> system message check -> remembered speaker -> NARRATOR
        if speaker_key:
            resolved_speaker = speaker_key
            self._active_speaker = speaker_key
            self._last_speaker_time = now
        elif self._system_msg_re.match(clean_text):
            resolved_speaker = "NARRATOR"
        elif (
            self._active_speaker
            and (now - self._last_speaker_time) <= self.context_ttl_s
        ):
            resolved_speaker = self._active_speaker
            self._last_speaker_time = now  # Extend TTL while monologue continues
        else:
            resolved_speaker = "NARRATOR"
            self._active_speaker = None

        if resolved_speaker == "NARRATOR" and not self.profile.voices.narrate_unnamed:
            report(self.status, StatusKind.DEBUG, "[PARSER] No speaker name and narrator is off -> not spoken")
            return None

        voice_profile = self._pick_voice_profile(resolved_speaker)

        return ParsedDialogue(
            speaker=resolved_speaker,
            clean_text=clean_text,
            voice=voice_profile,
        )

    def _extract_inline_speaker(self, text: str) -> tuple[str | None, str]:
        match = self._inline_delim_re.match(text)
        if match:
            candidate, remainder = match.group(1), match.group(2)
            # An unknown prefix must look like a name ("Old Man", "GUARD 1"),
            # otherwise "He said; go away" would invent a speaker called HE SAID
            if self._is_known_character(candidate) or self._looks_like_name(candidate):
                resolved = self._resolve_speaker_name(candidate)
                if resolved:
                    return resolved, remainder

        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            first_clean = first_line.strip().rstrip(":;|.")
            if 2 <= len(first_clean) <= 15 and (
                first_clean.isupper() or self._is_known_character(first_clean)
            ):
                resolved = self._resolve_speaker_name(first_clean)
                if resolved and rest.strip():
                    return resolved, rest

        caps_match = self._allcaps_prefix_re.match(text)
        if caps_match:
            candidate, remainder = caps_match.group(1), caps_match.group(2)
            if candidate not in {"OH", "AH", "HEY", "NO", "YES", "OK", "WAIT"}:
                resolved = self._resolve_speaker_name(candidate)
                if resolved:
                    return resolved, remainder

        return None, text

    def _drop_choice_lines(self, text: str) -> str:
        """Removes the answers of a choice prompt so only the question is spoken.

        Choices are two or more short trailing lines right after a line that asks
        a question, e.g. "Stay the night?" / "Yes" / "No" -> "Stay the night?".
        """
        lines = [line.strip() for line in text.strip().split("\n")]
        lines = [line for line in lines if line]
        n_choices = 0
        for line in reversed(lines):
            if len(line) > CHOICE_LINE_MAX_CHARS or not CHOICE_LINE_RE.match(line):
                break
            n_choices += 1
        question_idx = len(lines) - n_choices - 1
        if n_choices < 2 or question_idx < 0 or not lines[question_idx].endswith("?"):
            return text
        report(
            self.status,
            StatusKind.DEBUG,
            f"[PARSER] Skipped {n_choices} choice line(s): {lines[question_idx + 1:]!r}",
        )
        return "\n".join(lines[: question_idx + 1])

    @staticmethod
    def _is_place_name(text: str) -> bool:
        text = text.strip()
        words = text.split(" ")
        if "\n" in text or not 1 <= len(words) <= PLACE_NAME_MAX_WORDS:
            return False
        if words[-1].endswith(".") or words[-1] in CONNECTOR_WORDS:
            return False
        return all(
            (word in CONNECTOR_WORDS and index > 0)
            or re.fullmatch(r"[A-Z][A-Za-z'\-]*\.?", word) is not None
            for index, word in enumerate(words)
        )

    @staticmethod
    def _looks_like_name(candidate: str) -> bool:
        words = candidate.split()
        return 1 <= len(words) <= 3 and all(
            w[0].isupper() or w[0].isdigit() for w in words
        )

    def _is_known_character(self, name: str) -> bool:
        cleaned = self._non_alnum_re.sub("", name.upper()).strip()
        if cleaned in self.profile.voices.characters or cleaned in self.profile.voices.aliases:
            return True
        de_leeted = cleaned.translate(str.maketrans({"0": "O", "1": "I", "5": "S"}))
        return bool(
            get_close_matches(de_leeted, self._known_characters, n=1, cutoff=0.78)
        )

    def _resolve_speaker_name(self, raw_speaker: str) -> str | None:
        cleaned = self._non_alnum_re.sub("", raw_speaker.upper()).strip()
        if not cleaned:
            return None

        # Another name for a character ("GIRL" before Marle is named): her lines
        aliases = self.profile.voices.aliases
        if cleaned in aliases:
            return aliases[cleaned]

        if cleaned in self.profile.voices.characters:
            return cleaned

        de_leeted = cleaned.translate(str.maketrans({"0": "O", "1": "I", "5": "S"}))
        if de_leeted in self.profile.voices.characters:
            return de_leeted

        matches = get_close_matches(
            de_leeted, self._known_characters, n=1, cutoff=0.78
        )
        if matches:
            return matches[0]

        return de_leeted

    def _pick_voice_profile(self, speaker: str) -> VoiceProfile:
        if speaker == "NARRATOR":
            return VoiceProfile(
                voice_id=self.profile.voices.default_narrator,
                stability=0.70,
                similarity_boost=0.75,
                speed=1.00,
            )

        # 1. Explicitly configured character in YAML (string or full settings dict)
        configured = self.profile.voices.resolve_character(speaker)
        if configured:
            return configured

        # 2. Unmapped named NPC -> deterministic base voice + speed/stability variation
        words = set(speaker.split())
        is_female = bool(words & FEMALE_HINTS) or speaker in FEMALE_HINTS
        pool = (
            self.profile.voices.female_pool
            if is_female
            else self.profile.voices.male_pool
        )

        if not pool:
            return VoiceProfile(voice_id=self.profile.voices.default_narrator)

        digest = hashlib.md5(speaker.encode("utf-8")).digest()
        base_voice_id = pool[digest[0] % len(pool)]
        npc_speed = NPC_SPEED_VARIANTS[digest[1] % len(NPC_SPEED_VARIANTS)]
        npc_stability = NPC_STABILITY_VARIANTS[digest[2] % len(NPC_STABILITY_VARIANTS)]
        npc_pitch = NPC_PITCH_VARIANTS[digest[3] % len(NPC_PITCH_VARIANTS)]

        return VoiceProfile(
            voice_id=base_voice_id,
            stability=npc_stability,
            similarity_boost=0.70,
            speed=npc_speed,
            pitch_semitones=npc_pitch,
        )

    def _is_gibberish(self, text: str) -> bool:
        alpha_chars = [c.lower() for c in text if c.isalpha()]
        if len(alpha_chars) < 2:
            return True

        # Real vowel-less lines such as "Hmm...", "Shh!" or "Zzz..." are dialogue
        words = self._word_re.findall(text.lower())
        if all(
            len(w) >= 2 and self._repeat_re.sub(r"\1", w) in VOWELLESS_INTERJECTIONS
            for w in words
        ):
            return False

        real_vowels = sum(1 for c in alpha_chars if c in "aeiou")
        if real_vowels == 0:
            return True
        if len(alpha_chars) >= 6 and (real_vowels / len(alpha_chars)) < 0.15:
            return True

        if len(alpha_chars) >= 6:
            counts = sorted(
                (alpha_chars.count(c) for c in set(alpha_chars)), reverse=True
            )
            if (sum(counts[:2]) / len(alpha_chars)) > 0.65:
                return True

        tokens = text.split()
        if len(tokens) >= 4:
            junk_tokens = sum(
                1 for t in tokens if len(re.sub(r"[^A-Za-z]", "", t)) <= 1
            )
            if (junk_tokens / len(tokens)) >= 0.55:
                return True

        return False

    def _sanitize(self, text: str) -> str:
        cleaned = self._whitespace_re.sub(" ", text).replace(";", ",").strip()
        for pattern in self._compiled_strips:
            cleaned = pattern.sub("", cleaned).strip()
        cleaned = self._merged_pronoun_re.sub(r"I \1", cleaned)
        if cleaned.endswith(":"):
            cleaned = cleaned[:-1] + "!"
        return cleaned