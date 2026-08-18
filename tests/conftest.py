from __future__ import annotations

import pytest

from tests.helpers import FIXTURES_DIR


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR
