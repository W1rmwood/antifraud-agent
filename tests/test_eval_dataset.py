from collections import Counter

from eval.dataset import load_cases
from rag.chunks import load_schemes


def test_dataset_is_valid_and_balanced() -> None:
    cases = load_cases()
    schemes = load_schemes("data/schemes")

    assert len(cases) >= 40
    assert len({c.id for c in cases}) == len(cases)
    assert sum(not c.is_fraud for c in cases) >= 10, "need ordinary calls to measure false alarms"
    for case in cases:
        if case.expected_scheme is not None:
            scheme = schemes[case.expected_scheme]
            assert case.expected_stage is not None
            assert 1 <= case.expected_stage <= len(scheme.stages), case.id


def test_fragments_are_not_copied_from_schemes() -> None:
    """Verbatim typical_phrases in a query would inflate retrieval metrics."""
    phrases = {
        p.lower()
        for s in load_schemes("data/schemes").values()
        for st in s.stages
        for p in st.typical_phrases
    }
    copied = Counter(
        case.id for case in load_cases() for u in case.utterances if u.text.lower() in phrases
    )
    assert not copied, f"fragments copy scheme phrases: {dict(copied)}"
