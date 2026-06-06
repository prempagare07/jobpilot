from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import ApolloUsage, OutreachContact

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
APOLLO_DAILY_CREDIT_LIMIT = 50
HIRING_MANAGER_TITLES = [
    "Engineering Manager",
    "Senior Engineering Manager",
    "Director of Engineering",
    "VP Engineering",
    "Head of Engineering",
    "CTO",
    "Tech Lead",
    "Hiring Manager",
]


class ApolloConfigError(RuntimeError):
    pass


class ApolloCreditLimitReached(RuntimeError):
    pass


@dataclass(frozen=True)
class ApolloContact:
    id: str
    name: str
    title: str | None
    email: str | None
    email_status: str | None
    linkedin_url: str | None
    company: str
    seniority: str | None
    department: str | None


class ApolloClient:
    def __init__(
        self,
        db: Session,
        api_key: str | None = None,
        base_url: str = APOLLO_BASE_URL,
        daily_credit_limit: int = APOLLO_DAILY_CREDIT_LIMIT,
    ) -> None:
        self.db = db
        self.api_key = api_key if api_key is not None else settings.apollo_api_key
        self.base_url = base_url.rstrip("/")
        self.daily_credit_limit = daily_credit_limit

    async def find_hiring_managers(self, company_name: str, job_title: str) -> list[ApolloContact]:
        cached = self._cached_contacts(company_name)
        if cached:
            return cached

        body = {
            "q_organization_name": company_name,
            "person_titles": HIRING_MANAGER_TITLES,
            "contact_email_status": ["verified", "likely to engage"],
            "per_page": 10,
            "page": 1,
        }
        if job_title:
            body["q_organization_job_titles"] = [job_title]

        try:
            payload = await self._post("/mixed_people/api_search", body, operation="people_search")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            payload = await self._post("/mixed_people/search", body, operation="people_search_legacy")

        people = payload.get("people") or payload.get("contacts") or []
        contacts = [self._contact_from_person(person, fallback_company=company_name) for person in people]
        for contact in contacts:
            self._upsert_contact(contact)
        self.db.commit()
        return contacts

    async def enrich_contact(self, apollo_id: str) -> ApolloContact:
        payload = await self._post("/people/match", {"id": apollo_id}, operation="people_enrich")
        person = payload.get("person") or payload.get("contact") or payload
        contact = self._contact_from_person(person, fallback_company="")
        self._upsert_contact(contact)
        self.db.commit()
        return contact

    async def find_email(self, name: str, company_domain: str) -> str | None:
        payload = await self._post(
            "/people/match",
            {
                "name": name,
                "domain": company_domain,
                "organization_domain": company_domain,
            },
            operation="email_discovery",
        )
        person = payload.get("person") or payload.get("contact") or payload
        contact = self._contact_from_person(person, fallback_company=company_domain)
        if contact.id or contact.email:
            self._upsert_contact(contact)
            self.db.commit()
        return contact.email

    async def _post(self, path: str, body: dict[str, Any], operation: str) -> dict[str, Any]:
        if not self.api_key:
            raise ApolloConfigError("APOLLO_API_KEY is not set.")
        self._consume_credit(operation)
        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self.base_url}{path}", headers=headers, json=body)
            response.raise_for_status()
            return response.json()

    def _consume_credit(self, operation: str) -> None:
        today = datetime.utcnow().date().isoformat()
        used = self.db.scalar(
            select(func.coalesce(func.sum(ApolloUsage.credits_used), 0)).where(ApolloUsage.usage_date == today)
        )
        if int(used or 0) >= self.daily_credit_limit:
            raise ApolloCreditLimitReached(f"Apollo daily credit limit reached: {self.daily_credit_limit}")
        self.db.add(ApolloUsage(usage_date=today, operation=operation, credits_used=1))
        self.db.commit()

    def _cached_contacts(self, company_name: str) -> list[ApolloContact]:
        contacts = list(
            self.db.scalars(
                select(OutreachContact).where(OutreachContact.company.ilike(company_name)).limit(10)
            )
        )
        return [apollo_contact_from_model(contact) for contact in contacts]

    def _upsert_contact(self, contact: ApolloContact, job_id: str | None = None) -> OutreachContact:
        existing = self._find_existing_contact(contact)
        if existing is None:
            existing = OutreachContact(
                name=contact.name or "Unknown",
                title=contact.title,
                email=contact.email,
                email_status=contact.email_status,
                company=contact.company or "Unknown",
                seniority=contact.seniority,
                department=contact.department,
                linkedin_url=contact.linkedin_url,
                job_id=job_id,
                apollo_id=contact.id or None,
            )
            self.db.add(existing)
            return existing

        existing.name = contact.name or existing.name
        existing.title = contact.title or existing.title
        existing.email = contact.email or existing.email
        existing.email_status = contact.email_status or existing.email_status
        existing.company = contact.company or existing.company
        existing.seniority = contact.seniority or existing.seniority
        existing.department = contact.department or existing.department
        existing.linkedin_url = contact.linkedin_url or existing.linkedin_url
        existing.apollo_id = contact.id or existing.apollo_id
        if job_id and existing.job_id is None:
            existing.job_id = job_id
        return existing

    def _find_existing_contact(self, contact: ApolloContact) -> OutreachContact | None:
        clauses = []
        if contact.id:
            clauses.append(OutreachContact.apollo_id == contact.id)
        if contact.email:
            clauses.append(OutreachContact.email == contact.email)
        if not clauses:
            clauses.append(
                (OutreachContact.name == contact.name) & (OutreachContact.company == contact.company)
            )
        return self.db.scalar(select(OutreachContact).where(or_(*clauses)).limit(1))

    def _contact_from_person(self, person: dict[str, Any], fallback_company: str) -> ApolloContact:
        organization = person.get("organization") or {}
        company = (
            person.get("organization_name")
            or organization.get("name")
            or person.get("company")
            or fallback_company
            or ""
        )
        departments = person.get("departments") or person.get("department") or []
        if isinstance(departments, list):
            department = ", ".join(str(item) for item in departments if item)
        else:
            department = str(departments) if departments else None
        return ApolloContact(
            id=str(person.get("id") or person.get("person_id") or ""),
            name=str(person.get("name") or "Unknown"),
            title=person.get("title"),
            email=person.get("email"),
            email_status=person.get("email_status") or person.get("contact_email_status"),
            linkedin_url=person.get("linkedin_url") or person.get("linkedin"),
            company=str(company),
            seniority=person.get("seniority"),
            department=department,
        )


def apollo_contact_from_model(contact: OutreachContact) -> ApolloContact:
    return ApolloContact(
        id=contact.apollo_id or "",
        name=contact.name,
        title=contact.title,
        email=contact.email,
        email_status=contact.email_status,
        linkedin_url=contact.linkedin_url,
        company=contact.company,
        seniority=contact.seniority,
        department=contact.department,
    )
