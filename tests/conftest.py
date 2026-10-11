import pytest

from underwriteiq.generator import generate_book


@pytest.fixture(scope="session")
def small_book():
    return generate_book(800, seed=7)
