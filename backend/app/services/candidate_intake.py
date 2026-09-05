"""CSV intake: normalise, validate, dedupe. Plumbing, deliberately dull.

The one rule worth stating: **no row is ever silently dropped.** Every rejection
comes back with the row number and every reason it failed, not just the first.
A recruiter who uploads 400 rows and imports 380 needs to know which 20 and why,
or they will assume the file was fine and wonder later why those people were
never called.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# Headers we will map without asking. Anything else is left unmapped for the
# recruiter to confirm rather than guessed at.
_NAME_HEADERS = {"name", "full_name", "fullname", "candidate", "candidate_name"}
_PHONE_HEADERS = {
    "phone", "phone_number", "mobile", "mobile_number", "mobile_no",
    "contact", "contact_number", "number",
}

RowStatus = Literal["ok", "rejected", "duplicate_in_file", "duplicate_existing", "dnc"]


def normalise_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def normalise_phone(raw: str | None) -> str | None:
    """To E.164, assuming India when there is no country code.

    Every customer in this business is Indian and every candidate list will be
    Indian mobile numbers, most written without a country code and often with a
    leading zero from a landline-era habit. Assuming +91 is right far more often
    than it is wrong, and the alternative — rejecting every unprefixed number —
    would reject most of a typical file.

    The assumption is deliberately narrow: it only applies to a 10-digit number
    starting 6-9, which is the actual Indian mobile range. A 10-digit number
    starting with anything else is rejected rather than silently given a +91.
    """
    if not raw:
        return None
    text = str(raw).strip()
    explicit_country = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None

    if explicit_country:
        # Already international; trust it rather than re-deriving.
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    # 00 as the international prefix, e.g. 00919876543210
    if digits.startswith("00"):
        digits = digits[2:]
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    # Domestic trunk prefix: 09876543210
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    # Country code without a plus: 919876543210
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def dedupe_key(requisition_id: int | None, phone: str) -> str:
    """Scoped to the requisition, not global.

    The same person legitimately applies to two different roles, and a globally
    unique phone would make the second application collide with the first.
    """
    return hashlib.sha256(f"req:{requisition_id}:{phone}".encode()).hexdigest()


def read_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    return headers, [row for row in reader]


def propose_mapping(headers: list[str], candidate_variables: list[str]) -> dict[str, str | None]:
    """Suggest which column feeds which field. Ambiguity stays unmapped.

    A guess that is wrong dials the wrong people, and that mistake is invisible
    in a success message but obvious in a preview. So anything not recognised
    with confidence is returned as null for the recruiter to fill in.
    """
    lookup = {normalise_header(h): h for h in headers}
    mapping: dict[str, str | None] = {
        "name": next((lookup[k] for k in _NAME_HEADERS if k in lookup), None),
        "phone": next((lookup[k] for k in _PHONE_HEADERS if k in lookup), None),
    }
    for variable in candidate_variables:
        mapping[variable] = lookup.get(normalise_header(variable))
    return mapping


@dataclass
class RowResult:
    row_number: int
    status: RowStatus
    name: str | None = None
    phone: str | None = None
    custom_fields: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def importable(self) -> bool:
        return self.status in ("ok", "dnc")


@dataclass
class IntakePlan:
    rows: list[RowResult]

    @property
    def importable(self) -> list[RowResult]:
        return [r for r in self.rows if r.importable]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "total": len(self.rows),
            "imported": len(self.importable),
            "rejected": counts.get("rejected", 0),
            "duplicates_in_file": counts.get("duplicate_in_file", 0),
            "duplicates_existing": counts.get("duplicate_existing", 0),
            "do_not_call": counts.get("dnc", 0),
            # Every row that did not import, with every reason it did not.
            "problems": [
                {"row": r.row_number, "phone": r.phone, "reasons": r.reasons}
                for r in self.rows
                if r.reasons
            ],
        }


def build_plan(
    rows: list[dict[str, str]],
    mapping: dict[str, str | None],
    candidate_variables: list[str],
    existing_phones: set[str],
    dnc_phones: set[str],
    *,
    first_row_number: int = 2,
) -> IntakePlan:
    """Validate every row, collecting all reasons rather than stopping at the first.

    `first_row_number` defaults to 2 because row 1 of the file is the header, and
    a recruiter opening the CSV in a spreadsheet counts from there.
    """
    results: list[RowResult] = []
    seen_in_file: set[str] = set()

    for offset, raw in enumerate(rows):
        number = first_row_number + offset
        reasons: list[str] = []

        name = (raw.get(mapping.get("name") or "", "") or "").strip()
        if not name:
            reasons.append("no name")

        phone_column = mapping.get("phone") or ""
        phone = normalise_phone(raw.get(phone_column))
        if phone is None:
            reasons.append(
                f"phone {raw.get(phone_column, '')!r} is not a usable Indian mobile number"
            )

        custom_fields: dict[str, Any] = {}
        for variable in candidate_variables:
            column = mapping.get(variable)
            value = (raw.get(column or "", "") or "").strip()
            if not value:
                # Hunar returns 422 for a call whose custom_data is missing a key
                # the agent declares, so an empty variable is a rejection here
                # rather than a surprise at dispatch.
                reasons.append(f"missing required variable '{variable}'")
            else:
                custom_fields[variable] = value

        if reasons or phone is None:
            results.append(
                RowResult(number, "rejected", name or None, phone, custom_fields, reasons)
            )
            continue

        if phone in seen_in_file:
            results.append(
                RowResult(number, "duplicate_in_file", name, phone, custom_fields,
                          ["same number appears earlier in this file"])
            )
            continue
        if phone in existing_phones:
            results.append(
                RowResult(number, "duplicate_existing", name, phone, custom_fields,
                          ["already on this requisition"])
            )
            continue

        seen_in_file.add(phone)
        if phone in dnc_phones:
            # Imported and marked, not rejected. A recruiter needs to see that
            # the person is on the list and why, not just find them missing.
            results.append(
                RowResult(number, "dnc", name, phone, custom_fields,
                          ["on the do-not-call list, imported but will not be dialled"])
            )
            continue

        results.append(RowResult(number, "ok", name, phone, custom_fields))

    return IntakePlan(results)
