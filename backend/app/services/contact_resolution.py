"""Turning a profile into a number we can actually dial.

**This is a pipeline stage, not a field read, and that is the interesting part of
Module B.** Every people-data free tier gates phone numbers: PDL returns the
profile and withholds the number, or returns availability counts instead of
values. A design that treats `profile.phone` as data is a design that works on
the paid plan and returns an empty screen on the one we have.

So resolution is explicit, it is allowed to fail, and **every record carries a
badge naming which resolver produced its number.** That badge is not decoration.
A recruiter about to call thirty strangers needs to know whether a number came
from the vendor or from a demo fixture, and hiding it would make the demo a lie
that only breaks in production.
"""

import hashlib
from dataclasses import dataclass
from typing import Literal

from app.integrations.people_search.base import SourcingProfile
from app.services.candidate_intake import normalise_phone

Resolver = Literal["provider", "fixture", "unresolved"]

RESOLVER_LABELS: dict[str, str] = {
    "provider": "From the search provider",
    "fixture": "Demo number",
    "unresolved": "No number found",
}


@dataclass
class ResolvedContact:
    profile: SourcingProfile
    phone_e164: str | None
    resolver: Resolver
    detail: str

    @property
    def dialable(self) -> bool:
        return self.phone_e164 is not None


def resolve(profile: SourcingProfile, *, allow_fixture: bool) -> ResolvedContact:
    """One profile in, one contact out. Never raises.

    A profile with no reachable number is a normal outcome, not an error: it is
    what the free tier produces most of the time. It comes back as `unresolved`
    with a reason, so the screen can show the recruiter how much of their result
    set is actually callable.
    """
    if profile.phone:
        normalised = normalise_phone(profile.phone)
        if normalised:
            return ResolvedContact(
                profile, normalised, "provider",
                "the search provider returned this number",
            )
        # Reported rather than silently dropped. A number the provider has but we
        # cannot normalise is a gap in `normalise_phone`, and it should be
        # visible as one instead of looking like missing data.
        return ResolvedContact(
            profile, None, "unresolved",
            f"the provider returned {profile.phone!r}, which is not a usable Indian mobile number",
        )

    if allow_fixture:
        number = _demo_number(profile)
        return ResolvedContact(
            profile, number, "fixture",
            "no number was available, so a demo number is standing in for it",
        )

    return ResolvedContact(
        profile, None, "unresolved",
        "the search provider has no phone number for this profile on the current plan",
    )


def resolve_all(
    profiles: list[SourcingProfile], *, allow_fixture: bool
) -> list[ResolvedContact]:
    return [resolve(p, allow_fixture=allow_fixture) for p in profiles]


def _demo_number(profile: SourcingProfile) -> str:
    """A stable synthetic number, derived from the profile so re-running a search
    does not reshuffle who is who.

    Inside the 9876543xxx block this repo uses for all synthetic numbers. In demo
    mode nothing is dialled anyway — the simulator answers — so these are never
    put on a real telephone network.
    """
    # SHA-256, not hash(): Python randomises str hashing per process, so the
    # built-in would hand the same profile a different number after a restart.
    digest = hashlib.sha256(profile.dedupe_key.encode()).hexdigest()
    return f"+91987654{int(digest[:8], 16) % 10_000:04d}"
