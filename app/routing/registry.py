"""Which models the gateway serves, and which provider serves each one.

The registry is fixed at startup from settings, so a model name resolves even when its
backend is down: the request then fails with a clear error instead of a misleading 404.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.providers import Provider

ALIAS_OWNER = "gateway"


@dataclass(frozen=True, slots=True)
class Target:
    """One concrete model and the provider that serves it."""

    model: str
    provider: Provider


@dataclass(frozen=True, slots=True)
class ListedModel:
    id: str
    owned_by: str


class ModelRegistry:
    def __init__(self, *, models: Mapping[str, Provider], aliases: Mapping[str, Sequence[str]]) -> None:
        for alias, chain in aliases.items():
            if alias in models:
                raise ValueError(f"alias {alias!r} has the same name as a model")
            if not chain:
                raise ValueError(f"alias {alias!r} has no models")
            unknown = [name for name in chain if name not in models]
            if unknown:
                raise ValueError(f"alias {alias!r} refers to unknown models: {', '.join(unknown)}")
        self._models = dict(models)
        self._aliases = {alias: tuple(chain) for alias, chain in aliases.items()}

    def resolve(self, name: str) -> tuple[Target, ...] | None:
        """The targets to try for ``name``, in order, or None if the gateway does not serve it."""
        if name in self._models:
            return (Target(name, self._models[name]),)
        if name in self._aliases:
            return tuple(Target(model, self._models[model]) for model in self._aliases[name])
        return None

    def targets(self) -> tuple[Target, ...]:
        """Every configured model, in configuration order; aliases are not included."""
        return tuple(Target(name, provider) for name, provider in self._models.items())

    def is_alias(self, name: str) -> bool:
        return name in self._aliases

    async def available_models(self) -> list[ListedModel]:
        """Models whose provider can serve them now, then aliases with at least one such model."""
        available = {name for name, provider in self._models.items() if await provider.is_available(name)}
        listed = [ListedModel(name, self._models[name].name) for name in self._models if name in available]
        listed += [
            ListedModel(alias, ALIAS_OWNER)
            for alias, chain in self._aliases.items()
            if any(model in available for model in chain)
        ]
        return listed
