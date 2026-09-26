"""Row builders shared by the two medicines suites.

`REDUCING` is the utterance every heading in `test_meds_kind.py` is judged against: it
names a direction unambiguously, so a row claiming any other direction can be proved
wrong. `NO_DIRECTION` names none, so it contradicts nothing and every heading survives
it — which is the discipline these guards are built on, refusing what can be proved
wrong rather than what cannot be read.
"""

from conftest import utt
from second_chair.meds import ChangeKind, MedChange

REDUCING = "right, we'll bring the bisoprolol down from five milligrams to two point five"
NO_DIRECTION = "the bisoprolol is the little white one you take in the morning"


def row(source: str = REDUCING, **kw) -> MedChange:
    base = dict(
        medicine="bisoprolol",
        kind=ChangeKind.REDUCED,
        from_dose=None,
        to_dose=None,
        timing=None,
        quote=source,
        utterance_index=0,
    )
    base.update(kw)
    return MedChange(**base)


__all__ = ["NO_DIRECTION", "REDUCING", "row", "utt"]
