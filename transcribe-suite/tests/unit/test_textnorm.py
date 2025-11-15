import pytest

hyp = pytest.importorskip("hypothesis")
from hypothesis import given, strategies as st

from textnorm import TextNormalizer


@given(st.text())
def test_text_normalizer_idempotent(text):
    normalizer = TextNormalizer()
    human1, machine1 = normalizer.normalize_pair(text, "fr")
    human2, machine2 = normalizer.normalize_pair(human1, "fr")
    assert human1 == human2
    assert machine1 == machine2
