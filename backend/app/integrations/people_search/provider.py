"""Which people-search provider is live.

Mirrors `integrations/hunar/provider.py` deliberately: same singleton shape, same
reset helper for tests. Two switches rather than one, because they answer
different questions — `PEOPLE_SEARCH_PROVIDER` says which vendor, `DEMO_MODE`
says whether this deployment may talk to vendors at all.
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

    # Demo mode wins over the vendor setting. A deployment whose Hunar key has
    # expired should not still be spending PDL credits.
    if settings.demo_mode or settings.people_search_provider != "pdl":
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
