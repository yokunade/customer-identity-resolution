"""API contracts: inbound records, cleansed records and resolution responses."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- inbound


class InboundAddress(BaseModel):
    """A postal address as supplied by a source system. Any shape is accepted:
    numbered lines, a free list of lines, or both."""

    model_config = ConfigDict(extra="allow")

    line1: str | None = None
    line2: str | None = None
    line3: str | None = None
    line4: str | None = None
    lines: list[str] | None = None
    town: str | None = None
    county: str | None = None
    postcode: str | None = None
    country: str | None = None
    uprn: str | int | None = Field(
        default=None, description="Unique Property Reference Number, if the source has one."
    )


class InboundCustomer(BaseModel):
    """A customer record from any upstream service. Only source_system and
    source_record_id are mandatory; everything else is cleansed as available."""

    model_config = ConfigDict(extra="allow")

    source_system: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")
    source_record_id: str = Field(min_length=1, max_length=128)

    title: str | None = None
    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    full_name: str | None = Field(default=None, description="Used when name parts are not supplied.")
    suffix: str | None = None
    gender: str | None = None
    date_of_birth: str | dt.date | None = Field(
        default=None, description="ISO or UK day-first formats (DD/MM/YYYY etc.)."
    )

    email: str | None = None
    emails: list[str] = Field(default_factory=list)

    phone: str | None = None
    mobile_phone: str | None = None
    home_phone: str | None = None
    work_phone: str | None = None
    phones: list[str] = Field(default_factory=list)

    address: InboundAddress | None = None
    previous_addresses: list[InboundAddress] = Field(default_factory=list)


# --------------------------------------------------------------------------- cleansed


class Issue(BaseModel):
    field: str
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    detail: str | None = None


class CleansedName(BaseModel):
    title: str | None = None
    first_name: str | None = None
    middle_names: str | None = None
    last_name: str | None = None
    suffix: str | None = None


class CleansedEmail(BaseModel):
    address: str
    canonical: str
    domain: str
    usable_for_matching: bool = True


class CleansedPhone(BaseModel):
    e164: str
    national: str | None = None
    type: Literal[
        "mobile", "landline", "non_geographic", "freephone", "premium", "personal",
        "pager", "voip", "international", "unknown",
    ]
    usable_for_matching: bool = True


class CleansedAddress(BaseModel):
    lines: list[str] = Field(default_factory=list, description="Royal Mail style formatted lines.")
    sub_building: str | None = None
    building_name: str | None = None
    building_number: str | None = None
    street: str | None = None
    localities: list[str] = Field(default_factory=list)
    post_town: str | None = None
    county: str | None = None
    postcode: str | None = None
    postcode_outward: str | None = None
    postcode_inward: str | None = None
    postcode_sector: str | None = None
    postcode_district: str | None = None
    postcode_area: str | None = None
    country: str = "GB"
    is_uk: bool = True
    uprn: str | None = None


class CleansedRecord(BaseModel):
    source_system: str
    source_record_id: str
    name: CleansedName
    gender: Literal["M", "F", "X"] | None = None
    date_of_birth: dt.date | None = None
    date_of_birth_usable: bool = False
    emails: list[CleansedEmail] = Field(default_factory=list)
    phones: list[CleansedPhone] = Field(default_factory=list)
    address: CleansedAddress | None = None
    previous_addresses: list[CleansedAddress] = Field(default_factory=list)


# --------------------------------------------------------------------------- responses


# relinked: a re-sent source record that was its customer's only record matched
# another customer, so its old customer was merged into that one.
Outcome = Literal["matched", "new_customer", "existing_source_record", "relinked"]


class MatchDetail(BaseModel):
    rule: str
    tier: Literal["exact", "strong", "probable", "possible"]
    priority: int
    matched_record_ids: list[int]
    auto_linked: bool = Field(
        default=True,
        description="False when the rule's tier is not in AUTO_LINK_TIERS: a new customer was created and the "
                    "suggested link was queued for review.",
    )
    suggested_customer_id: UUID | None = None
    competing_customer_ids: list[UUID] = Field(
        default_factory=list,
        description="Other customers that matched at the same rule. Logged as merge candidates.",
    )


class ResolveResponse(BaseModel):
    customer_id: UUID
    outcome: Outcome
    record_id: int
    match: MatchDetail | None = None
    candidates_considered: int = 0
    skipped_blocks: list[str] = Field(
        default_factory=list, description="Blocking keys skipped because they exceeded MAX_BLOCK_SIZE."
    )
    match_keys_generated: int = 0
    data_quality: list[Issue] = Field(default_factory=list)
    cleansed: CleansedRecord
    processing_ms: float
    matching_ms: float = Field(default=0.0, description="Part of processing_ms spent finding and scoring candidates.")


class CleanseResponse(BaseModel):
    cleansed: CleansedRecord
    data_quality: list[Issue]
    match_keys: dict[str, int] = Field(description="Number of key values generated per match key type.")
    blocking_keys: dict[str, int]


class BatchResponse(BaseModel):
    results: list[ResolveResponse | dict[str, Any]]


class SourceRecordSummary(BaseModel):
    record_id: int
    source_system: str
    source_record_id: str
    received_at: dt.datetime
    updated_at: dt.datetime
    cleansed: dict[str, Any]


class CustomerResponse(BaseModel):
    customer_id: UUID
    requested_customer_id: UUID
    status: str
    created_at: dt.datetime
    source_records: list[SourceRecordSummary]


class CustomerSearchResponse(BaseModel):
    searched_by: Literal["email", "mobile", "postcode"]
    normalised_value: str = Field(description="The value after cleansing, as it was matched.")
    customers: list[CustomerResponse]
    truncated: bool = Field(description="More customers matched than the limit allowed.")


class SourceLookupResponse(BaseModel):
    source_system: str
    source_record_id: str
    record_id: int
    customer_id: UUID


class MergeRequest(BaseModel):
    survivor_customer_id: UUID
    merged_customer_id: UUID
    reason: str | None = None


class MergeResponse(BaseModel):
    survivor_customer_id: UUID
    merged_customer_id: UUID
    source_records_moved: int
