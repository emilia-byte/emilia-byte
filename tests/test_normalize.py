"""
Unit tests for login.normalize() -- coercing openpyxl cell values (which
come back as None / int / float / str) into a consistent string form for
comparison against spreadsheet lookups. No browser/network involved.
"""

import pytest

from login import normalize


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (123, "123"),
        (123.0, "123"),       # whole-number float loses the trailing .0
        (123.5, "123.5"),
        ("  EMI_AUTO_2  ", "EMI_AUTO_2"),  # surrounding whitespace stripped
        ("already_clean", "already_clean"),
    ],
)
def test_normalize(value, expected):
    assert normalize(value) == expected
