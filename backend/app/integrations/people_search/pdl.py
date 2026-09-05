"""People Data Labs Person Search.

The free tier is **100 records a month, and every returned record spends one**.
That single fact drives every decision in this file: a hard result cap, no
pagination loop, and no retry on a 402. A pagination bug here does not cost a
slow endpoint, it costs the entire month's quota in one request.
"""

import logging
from typing import Any

import httpx

from app.integrations.people_search.base import (
    PeopleSearchProvider,
    SearchResult,
    SourcingProfile,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.peopledatalabs.com/v5"

# Well under the API's own maximum of 100. The cap is ours, not theirs: at one
# credit per record a single careless search would spend a fifth of the month.
MAX_RESULTS = 10

TIMEOUT_SECONDS = 30.0


class PeopleSearchError(Exception):
    """Carries a sentence a recruiter can act on, not a status code."""


class PDLProvider:
    name = "pdl"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport

    async def search(self, query: dict[str, Any], limit: int) -> SearchResult:
        if not self._api_key:
            raise PeopleSearchError(
                "PDL_API_KEY is not set, so live search is unavailable. The fixture "
                "provider is serving results instead."
            )

        size = max(1, min(limit, MAX_RESULTS))
        params = {
            # Elasticsearch query, built by services/jd_to_query.py and editable
            # by the recruiter before it runs.
            "query": query,
            "size": size,
            "titlecase": True,
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT_SECONDS), transport=self._transport
            ) as client:
                response = await client.post(
                    f"{BASE_URL}/person/search",
                    headers={"X-Api-Key": self._api_key, "Content-Type": "application/json"},
                    json=params,
                )
        except httpx.HTTPError as exc:
            raise PeopleSearchError(f"could not reach People Data Labs: {exc}") from exc

        if response.status_code == 401:
            raise PeopleSearchError("People Data Labs rejected the API key")
        if response.status_code == 402:
            # Never retried. A quota error is a fact about the month, not a
            # transient failure, and retrying spends what is left of it.
            raise PeopleSearchError(
                "the People Data Labs quota for this month is exhausted (the free "
                "tier is 100 records, and every record returned spends one)"
            )
        if response.status_code == 404:
            # PDL answers 404 for "no records matched", which is not an error.
            return SearchResult(profiles=[], provider=self.name, total_available=0,
                                notes=["No profiles matched this query."])
        if response.status_code >= 400:
            raise PeopleSearchError(
                f"People Data Labs answered {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        records = body.get("data") or []
        profiles = [_to_profile(r) for r in records]

        notes: list[str] = []
        total = body.get("total")
        if isinstance(total, int) and total > len(profiles):
            # Said out loud. Silently truncating a 7,000-result search to ten
            # would make the sourcing pool look tiny for no visible reason.
            fetched = len(profiles)
            notes.append(
                f"{total:,} profiles matched and {fetched} "
                f"{'was' if fetched == 1 else 'were'} fetched: each record costs one "
                f"credit against a 100-a-month free tier."
            )
        if any(p.phone_available and not p.phone for p in profiles):
            # The single most important thing to say on this screen when the
            # plan is limited, and it is invisible in the profile list itself.
            notes.append(
                "Some of these profiles have a phone number on file that this plan "
                "does not release. They are marked as unresolved rather than dropped."
            )

        # Deliberately no scroll_token handling. Following it is how a free tier
        # disappears in a single request.
        return SearchResult(
            profiles=profiles, provider=self.name, total_available=total, notes=notes
        )


def _to_profile(record: dict[str, Any]) -> SourcingProfile:
    """PDL's schema, flattened to ours.

    Read through `_text`, never straight off the dict. **On a limited plan PDL
    substitutes the literal boolean `true` for a gated field rather than null or
    an absent key** — observed on a live record where `mobile_phone`,
    `phone_numbers`, `emails` and even `location_name` all came back as `True`.
    Taking `record.get("location_name")` at face value puts the string "True" in
    the location column of a recruiter's screen.
    """
    phone, available = _phone(record)
    own_location = _text(record, "location_name")
    # Falls back to the employer's office, which is not gated — but it is the
    # company's location, not the person's. A live India-filtered search returned
    # engineers whose company HQ is in Houston, so this is flagged rather than
    # presented as where they are.
    company_location = _text(record, "job_company_location_name")

    return SourcingProfile(
        full_name=_text(record, "full_name") or "Unknown",
        headline=_text(record, "headline"),
        current_title=_text(record, "job_title"),
        current_company=_text(record, "job_company_name"),
        location=own_location or company_location,
        location_is_company=own_location is None and company_location is not None,
        linkedin_url=_linkedin(record),
        phone=phone,
        phone_available=available,
    )


def _text(record: dict[str, Any], key: str) -> str | None:
    """A string, or nothing. Booleans and numbers are treated as absent."""
    value = record.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _linkedin(record: dict[str, Any]) -> str | None:
    url = _text(record, "linkedin_url")
    if not url:
        return None
    # Returned without a scheme: "linkedin.com/in/...". A bare href like that is
    # resolved relative to our own origin and 404s.
    return url if url.startswith("http") else f"https://{url}"


def _phone(record: dict[str, Any]) -> tuple[str | None, bool]:
    """The number, and whether PDL says it has one it will not give us.

    Three outcomes, not two: a real number, `true` meaning "exists, not included
    in this plan", or nothing at all. Collapsing the last two loses the only
    piece of information that tells a recruiter whether upgrading would help.
    """
    for key in ("mobile_phone", "phone_numbers"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), True
        if isinstance(value, list):
            for number in value:
                if isinstance(number, str) and number.strip():
                    return number.strip(), True

    gated = any(record.get(key) is True for key in ("mobile_phone", "phone_numbers"))
    return None, gated


_: PeopleSearchProvider = PDLProvider("")
