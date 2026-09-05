"""A curated set of synthetic profiles.

Two jobs, not one. It is the demo-mode provider, and it is also the fallback when
PDL returns profiles but no dialable numbers — which on the free tier is the
normal outcome rather than the exceptional one.

Everything here is invented. The names are constructed, the numbers are in the
9876543xxx block used throughout this repo's test data, and the LinkedIn URLs
point at a domain that does not resolve. No real person's contact details are in
this file, and none may ever be added to it.

The roles match the skilled and technical staffing that Hunar's Aarvi Encon and
Rupeek case studies describe. Frontline profiles are deliberately absent: the
public people-data sets do not cover frontline workers, and pretending otherwise
would be the kind of demo that falls apart under one question.
"""

from typing import Any

from app.integrations.people_search.base import (
    PeopleSearchProvider,
    SearchResult,
    SourcingProfile,
)

_PROFILES: list[SourcingProfile] = [
    SourcingProfile(
        full_name="Ananya Raghavan",
        headline="Instrumentation Engineer | Process automation",
        current_title="Instrumentation Engineer",
        current_company="Meridian Process Systems",
        location="Pune, Maharashtra, India",
        linkedin_url="https://linkedin.invalid/in/ananya-raghavan-demo",
        phone="+919876543301",
    ),
    SourcingProfile(
        full_name="Vikram Sethi",
        headline="Senior Mechanical Design Engineer",
        current_title="Senior Mechanical Design Engineer",
        current_company="Kalyani Heavy Fabrication",
        location="Pune, Maharashtra, India",
        linkedin_url="https://linkedin.invalid/in/vikram-sethi-demo",
        phone="+919876543302",
    ),
    SourcingProfile(
        full_name="Meera Nambiar",
        headline="QA/QC Engineer, piping and welding inspection",
        current_title="QA/QC Engineer",
        current_company="Coastal Energy Projects",
        location="Chennai, Tamil Nadu, India",
        linkedin_url="https://linkedin.invalid/in/meera-nambiar-demo",
        phone="+919876543303",
    ),
    SourcingProfile(
        full_name="Rohit Deshpande",
        headline="Electrical Site Engineer | Substation commissioning",
        current_title="Electrical Site Engineer",
        current_company="Sunbeam Infra Services",
        location="Nagpur, Maharashtra, India",
        linkedin_url="https://linkedin.invalid/in/rohit-deshpande-demo",
        # No number even in the fixture set. Contact resolution has to have a
        # failing case, or the screen never shows what an unreachable profile
        # looks like and the constraint stays invisible.
        phone=None,
    ),
    SourcingProfile(
        full_name="Farhan Qureshi",
        headline="Field Service Engineer, industrial pumps",
        current_title="Field Service Engineer",
        current_company="Torrent Rotating Equipment",
        location="Ahmedabad, Gujarat, India",
        linkedin_url="https://linkedin.invalid/in/farhan-qureshi-demo",
        phone="+919876543305",
    ),
    SourcingProfile(
        full_name="Divya Shanbhag",
        headline="Backend Engineer | Python, distributed systems",
        current_title="Backend Engineer",
        current_company="Kestrel Labs",
        location="Bengaluru, Karnataka, India",
        linkedin_url="https://linkedin.invalid/in/divya-shanbhag-demo",
        phone="+919876543306",
    ),
    SourcingProfile(
        full_name="Aditya Kulkarni",
        headline="Senior Backend Engineer | Go, Kubernetes",
        current_title="Senior Backend Engineer",
        current_company="Northline Payments",
        location="Bengaluru, Karnataka, India",
        linkedin_url="https://linkedin.invalid/in/aditya-kulkarni-demo",
        phone="+919876543307",
    ),
    SourcingProfile(
        full_name="Sneha Iyer",
        headline="Piping Design Engineer, oil and gas",
        current_title="Piping Design Engineer",
        current_company="Anvil Engineering Consultants",
        location="Vadodara, Gujarat, India",
        linkedin_url="https://linkedin.invalid/in/sneha-iyer-demo",
        phone="+919876543308",
    ),
    SourcingProfile(
        full_name="Kabir Malhotra",
        headline="Project Engineer | EPC, turnaround maintenance",
        current_title="Project Engineer",
        current_company="Deccan EPC",
        location="Hyderabad, Telangana, India",
        linkedin_url="https://linkedin.invalid/in/kabir-malhotra-demo",
        phone="+919876543309",
    ),
    SourcingProfile(
        full_name="Priyanka Bose",
        headline="Data Engineer | Spark, Airflow",
        current_title="Data Engineer",
        current_company="Silverline Analytics",
        location="Pune, Maharashtra, India",
        linkedin_url="https://linkedin.invalid/in/priyanka-bose-demo",
        phone="+919876543310",
    ),
]


class FixtureProvider:
    name = "fixture"

    async def search(self, query: dict[str, Any], limit: int) -> SearchResult:
        """Filtered by the query's own terms, not returned wholesale.

        The point of showing an editable query is that editing it changes the
        result. A provider that ignores the query would make that control a lie.
        """
        terms = _terms(query)
        matched = [p for p in _PROFILES if _matches(p, terms)] if terms else list(_PROFILES)

        notes = ["Synthetic profiles. No real person's details appear in this data."]
        if terms and not matched:
            notes.append(
                "No synthetic profile matched those terms, so the full demo set is "
                "shown instead."
            )
            matched = list(_PROFILES)

        return SearchResult(
            profiles=matched[:limit], provider=self.name,
            total_available=len(matched), notes=notes,
        )


def _terms(query: dict[str, Any]) -> list[str]:
    """Pull every string leaf out of the Elasticsearch query.

    Crude on purpose: matching the real query grammar would be building a search
    engine to filter ten rows. The words are what matter here.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"bool", "must", "should", "filter", "term", "terms", "match"}:
                    walk(value)
                elif isinstance(value, (dict, list)):
                    walk(value)
                elif isinstance(value, str):
                    found.append(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(query)
    return [t.lower() for term in found for t in term.split() if len(t) > 2]


def _matches(profile: SourcingProfile, terms: list[str]) -> bool:
    haystack = " ".join(
        filter(None, [profile.current_title, profile.headline, profile.location,
                      profile.current_company])
    ).lower()
    return any(term in haystack for term in terms)


_: PeopleSearchProvider = FixtureProvider()
