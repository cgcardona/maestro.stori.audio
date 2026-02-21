"""Melody scoring functions."""


def score_melody_notes(notes: list[dict], *, min_notes: int = 8) -> tuple[float, list[str]]:
    """Score melody: phrase length, note count, register."""
    if len(notes) < min_notes:
        return 0.4, ["melody_sparse: add more melody notes or check rest_density"]
    score = min(1.0, len(notes) / 32.0)
    return score, []
