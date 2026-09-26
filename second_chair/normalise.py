"""Normalisation for guard matching. The output of this module is never displayed.

A guard that compares a list against raw model output is comparing against bytes the
model picked. `she's`, `she’s`, `she' s`, `ѕhe's` (Cyrillic `ѕ`) and `s​he's` (zero-width
space) must all reach the same comparison, or the list is decorative.

The contract this module follows, shared with every other guard in this project's
family:

* Normalisation produces a **matching copy**. It is never stored, never rendered, never
  logged as the text, and never sent anywhere. The original is what you keep.
* Two forms come out, because one is not enough. `spaced` turns apostrophes and hyphens
  into spaces, so `she's` becomes `she s` and the word `she` is visible. `squeezed`
  removes them, so `did' not` becomes `didnot` and a phrase rule still sees it. Checking
  only one trades one hole for another.
* Order matters. Case-folding happens *before* the lookalike fold, or an uppercase
  Cyrillic `Ѕ` misses the map.

`foreign_letters()` is the allowlist half of this and the part that matters most. A fold
table is a denylist wearing different clothes: `dıd not` with a dotless Turkish `ı`
survives it, and so will the next lookalike. For an English-only guarded field, any
letter that is still outside `a-z` after folding is either a language the guard was never
written for or somebody probing it. Refuse, and do not extend the map.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Invisible or rendering-steering. None of them carry meaning for a guard, and every
# one is a way to split a banned word in half.
_INVISIBLE = {
    0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,   # soft hyphen, zero-width family
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,           # bidi embedding / override
    0x2066, 0x2067, 0x2068, 0x2069,                   # bidi isolates
}

# Latin lookalikes that spell English words. Deliberately not exhaustive — see
# `foreign_letters` below for what catches the rest.
_LOOKALIKE = {
    "а": "a", "б": "b", "е": "e", "ѕ": "s", "і": "i", "ј": "j", "ӏ": "l", "о": "o",
    "р": "p", "с": "c", "у": "y", "х": "x", "ԁ": "d", "һ": "h", "ԛ": "q", "ѡ": "w",
    "ν": "v", "ο": "o", "ρ": "p", "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t",
}

_PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'",
    "´": "'", "`": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
}

_FOLD = str.maketrans({**_PUNCT, **_LOOKALIKE})
_DROP = {cp: None for cp in _INVISIBLE}
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def normalise(text: str) -> str:
    """A matching copy of `text`. Never render this."""
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_DROP)
    s = "".join(c for c in s if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))
    s = s.casefold()                                   # before the fold, not after
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = unicodedata.normalize("NFC", s).translate(_FOLD)
    s = re.sub(r"[^\S\n]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


@dataclass(frozen=True)
class Normalised:
    """A matching copy. Never render any field of this."""

    original: str
    spaced: str      # "she's" -> "she s"
    squeezed: str    # "she's" -> "shes"
    tokens: frozenset[str]

    def has_word(self, word: str) -> bool:
        n = normalise(word)
        return n in self.tokens or re.sub(r"['-]", "", n) in self.tokens

    def has_any_word(self, words) -> str | None:
        """The first of `words` present as a whole token, or None."""
        for w in words:
            if self.has_word(w):
                return w
        return None

    def has_phrase(self, phrase: str) -> bool:
        n = normalise(phrase)
        spaced = re.sub(r"\s+", " ", re.sub(r"['-]", " ", n)).strip()
        return spaced in self.spaced or re.sub(r"['-]", "", n) in self.squeezed

    def has_any_phrase(self, phrases) -> str | None:
        """The first of `phrases` present, or None."""
        for p in phrases:
            if self.has_phrase(p):
                return p
        return None

    def foreign_letters(self) -> list[str]:
        """Letters that survived folding. Non-empty means somebody is trying."""
        return sorted({c for c in self.squeezed if c.isalpha() and not ("a" <= c <= "z")})


def prepare(text: str) -> Normalised:
    n = normalise(text)
    spaced = re.sub(r"\s+", " ", re.sub(r"\s*['-]\s*", " ", n)).strip()
    squeezed = re.sub(r"\s*['-]\s*", "", n)
    tokens = frozenset(t for t in _WORD_SPLIT.split(spaced + " " + squeezed) if t)
    return Normalised(text, spaced, squeezed, tokens)


__all__ = ["Normalised", "normalise", "prepare"]
