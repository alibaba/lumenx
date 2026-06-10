import pytest

from src.apps.playground.models import GenerateRequest, PlaygroundMode
from src.apps.playground.service import PlaygroundService


class FakeStorage:
    def __init__(self):
        self.generations = []

    def add_generation(self, gen):
        self.generations.append(gen)


def test_playground_rejects_prompt_over_model_limit():
    service = PlaygroundService(FakeStorage())
    request = GenerateRequest(
        mode=PlaygroundMode.T2V,
        model_id="seedance-2.0-t2v",
        prompt="x" * 2001,
    )

    with pytest.raises(ValueError, match="Prompt exceeds 2000 characters"):
        service.create_generation(request)


def test_playground_accepts_prompt_at_model_limit():
    storage = FakeStorage()
    service = PlaygroundService(storage)
    request = GenerateRequest(
        mode=PlaygroundMode.T2V,
        model_id="seedance-2.0-t2v",
        prompt="x" * 2000,
    )

    generation = service.create_generation(request)

    assert generation.prompt == "x" * 2000
    assert storage.generations == [generation]
