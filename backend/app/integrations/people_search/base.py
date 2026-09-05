"""The `PeopleSearchProvider` seam.

One of the three interfaces notes.md section 6 approves, and the one with the
clearest justification: **the enrichment vendor is the most volatile component in
any sourcing pipeline.** Proxycurl was the default LinkedIn data API until
LinkedIn sued its parent company; it shut down permanently on 4 July 2025 and
every integration built directly on it broke overnight, with no migration path.

So the vendor sits behind a protocol and nothing downstream knows which one is
live. `SourcingProfile` is deliberately a small, vendor-neutral shape: seven
fields, all optional except the name. Anything richer would be PDL's schema
wearing a different name, and the next vendor would not fill it.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class SourcingProfile:
    """One person, normalised. Nothing vendor-specific survives this boundary."""

    # The only required field. A profile with no name cannot be shown to a
    # recruiter or spoken to on a call, so a provider that cannot produce one has
    # produced nothing.
    full_name: str
    headline: str | None = None
    current_title: str | None = None
    current_company: str | None = None
    location: str | None = None
    linkedin_url: str | None = None

    # May be absent, and usually is. PDL's free tier returns availability counts
    # rather than the number itself, which is exactly why contact resolution is a
    # separate pipeline stage instead of a field read. See services/contact_resolution.py.
    phone: str | None = None

    # Stable identity for deduplication inside one result set. LinkedIn URL where
    # there is one, otherwise the name and company together.
    @property
    def dedupe_key(self) -> str:
        if self.linkedin_url:
            return self.linkedin_url.rstrip("/").lower()
        return f"{self.full_name}|{self.current_company or ''}".lower()


@dataclass
class SearchResult:
    profiles: list[SourcingProfile] = field(default_factory=list)
    provider: str = ""
    # What the provider says it could have returned, against what we asked for.
    # Surfaced because a free tier silently truncating is otherwise invisible.
    total_available: int | None = None
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class PeopleSearchProvider(Protocol):
    name: str

    async def search(self, query: dict[str, Any], limit: int) -> SearchResult: ...
