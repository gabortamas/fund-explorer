"""Score an extractor against the hand-made answer key, field by field.

For each field: how many of the answer-key funds did the extractor get exactly right?
"Exactly" means: the same text (ignoring upper/lower case and extra spaces), or the
same number. A field the extractor left empty counts as wrong; so does a value the
answer key leaves empty but the extractor filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fundrag.extract.schema import COLUMNS, KidFields

FIELDS = [c for c in COLUMNS if c != "isin"]


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())
    return abs(float(a) - float(b)) < 1e-6


@dataclass
class Score:
    n_funds: int = 0
    correct: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    mistakes: list[tuple[str, str, object, object]] = field(default_factory=list)
    missing_funds: list[str] = field(default_factory=list)  # in the key, not extracted

    @property
    def accuracy(self) -> float:
        total = self.n_funds * len(FIELDS)
        return sum(self.correct.values()) / total if total else 0.0


def score(extracted: dict[str, KidFields], gold: dict[str, KidFields]) -> Score:
    result = Score()
    for isin, truth in gold.items():
        guess = extracted.get(isin)
        if guess is None:
            result.missing_funds.append(isin)
            guess = KidFields(isin=isin)  # nothing extracted: every field wrong
        result.n_funds += 1
        for f in FIELDS:
            expected, got = getattr(truth, f), getattr(guess, f)
            if same(expected, got):
                result.correct[f] += 1
            else:
                result.mistakes.append((isin, f, expected, got))
    return result
