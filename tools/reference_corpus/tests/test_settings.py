from __future__ import annotations

import numpy as np

from tools.reference_corpus.settings import _maximin_latin_hypercube


def test_latin_hypercube_is_reproducible_and_stratified() -> None:
    first = _maximin_latin_hypercube(10, 4, 42)
    second = _maximin_latin_hypercube(10, 4, 42)
    np.testing.assert_array_equal(first, second)
    assert np.all((first >= 0.05) & (first <= 0.95))
    normalized = (first - 0.05) / 0.9
    for column in normalized.T:
        assert sorted(np.floor(column * 10).astype(int)) == list(range(10))
