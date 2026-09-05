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
            # Said out loud. Silently truncating a 4,000-result search to ten
            # would make the sourcing pool look tiny for no visible reason.
            notes.append(
                f"{total} profiles matched; {len(profiles)} were fetched because each "
                f"record costs one credit and the cap here is {MAX_RESULTS}."
            )

        # Deliberately no scroll_token handling. Following it is how a free tier
        # disappears in a single request.
        return SearchResult(
            profiles=profiles, provider=self.name, total_available=total, notes=notes
        )


def _to_profile(record: dict[str, Any]) -> SourcingProfile:
    """PDL's schema, flattened to ours.

    Every field is read defensively. PDL returns nulls for anything the current
    plan does not include, and which fields those are changes with the plan
    rather than with the API version.
    """
    return SourcingProfile(
        full_name=record.get("full_name") or "Unknown",
        headline=record.get("headline"),
        current_title=record.get("job_title"),
        current_company=record.get("job_company_name"),
        location=record.get("location_name"),
        linkedin_url=_linkedin(record),
        phone=_phone(record),
    )


def _linkedin(record: dict[str, Any]) -> str | None:
    url = record.get("linkedin_url")
    if not url:
        return None
    return url if url.startswith("http") else f"https://{url}"


def _phone(record: dict[str, Any]) -> str | None:
    """A number if the plan includes one. Usually it does not.

    `mobile_phone` and `phone_numbers` are both gated: on the free tier they come
    back null and empty. Returning None here is the normal case, and contact
    resolution is what deals with it.
    """
    mobile = record.get("mobile_phone")
    if isinstance(mobile, str) and mobile.strip():
        return mobile.strip()
    numbers = record.get("phone_numbers") or []
    for number in numbers:
        if isinstance(number, str) and number.strip():
            return number.strip()
    return None


_: PeopleSearchProvider = PDLProvider("")
