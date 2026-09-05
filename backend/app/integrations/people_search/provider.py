"""Which people-search provider is live.

Mirrors `integrations/hunar/provider.py` deliberately: same singleton shape, same
reset helper for tests.

`PEOPLE_SEARCH_PROVIDER` is the only switch, and `DEMO_MODE` deliberately does
not participate. It did at first, which was wrong: `DEMO_MODE` exists because the
Hunar trial key expires and the deployed demo must survive it, so it is a
statement about **dialling**, not about search. Coupling them made the most
useful configuration unreachable — real PDL profiles with simulated calls, which
is exactly what you want in a review meeting: honest data, nobody's phone rings.
"""

from app.config import settings
from app.integrations.people_search.base import PeopleSearchProvider

_provider: PeopleSearchProvider | None = None


def get_people_search_provider() -> PeopleSearchProvider:
    global _provider
    if _provider is None:
        _provider = _build()
    return _provider


def _build() -> PeopleSearchProvider:
    from app.integrations.people_search.fixture import FixtureProvider

    if settings.people_search_provider != "pdl":
        return FixtureProvider()

    if not settings.pdl_api_key:
        # Falling back rather than failing: a missing key is a configuration
        # gap, and a sourcing screen that 500s teaches the reviewer nothing.
        # The response says which provider actually answered.
        return FixtureProvider()

    from app.integrations.people_search.pdl import PDLProvider

    return PDLProvider(settings.pdl_api_key)


def reset_people_search_provider() -> None:
    """Test helper. The singleton would otherwise leak between tests."""
    global _provider
    _provider = None
