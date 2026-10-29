"""App-owned configuration snapshots and their generation-local clients."""

from collections.abc import Callable, Mapping
from threading import RLock
from types import MappingProxyType
from typing import TypeVar

from .json_storage import StorageError

T = TypeVar("T")


def validate_environment(values: Mapping[str, str]) -> dict[str, str]:
    captured = dict(values)
    for key, value in captured.items():
        if (not isinstance(key, str) or not key or "=" in key or "\0" in key
                or not isinstance(value, str) or "\0" in value):
            raise StorageError("Configuration must contain valid environment strings")
        try:
            key.encode("utf-8")
            value.encode("utf-8")
        except UnicodeError as exc:
            raise StorageError("Configuration must contain valid UTF-8 text") from exc
    return captured


class RuntimeEnvironment:
    """Publish only saved settings; existing client references keep their snapshot.

    Persistence is supplied by the owning app, preserving its existing JSON/dotenv
    storage contract. Client factories only construct local objects: network work
    must happen after this cache returns and releases its lock.
    """

    def __init__(self, values: Mapping[str, str]):
        self._lock = RLock()
        self._snapshot = MappingProxyType(validate_environment(values))
        self._clients: dict[str, object] = {}

    def snapshot(self) -> Mapping[str, str]:
        with self._lock:
            return self._snapshot

    def client(self, name: str, create: Callable[[Mapping[str, str]], T]) -> T:
        with self._lock:
            if name not in self._clients:
                self._clients[name] = create(self._snapshot)
            return self._clients[name]

    def update(
        self, updates: Mapping[str, str], *, remove_keys=(),
        persist: Callable[[dict[str, str], tuple[str, ...]], None],
    ) -> Mapping[str, str]:
        updates = validate_environment(updates)
        removals = tuple(remove_keys)
        validate_environment(dict.fromkeys(removals, ""))
        with self._lock:
            values = {**self._snapshot, **updates}
            for key in removals:
                values.pop(key, None)
            # Construct the replacement before persistence; after it succeeds,
            # publication cannot fail due to validation or client construction.
            replacement = MappingProxyType(values)
            persist(dict(updates), removals)
            self._snapshot = replacement
            self._clients = {}
            return replacement
