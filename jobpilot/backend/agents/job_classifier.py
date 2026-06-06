from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from backend.agents.json_utils import extract_json_object
from backend.agents.ollama_client import FAST_MODEL, OllamaClient, ollama_client

RoleType = Literal["AI Engineer", "SDE", "ML Engineer", "Data Engineer", "Full Stack", "Backend", "Other"]
Seniority = Literal["junior", "mid", "senior", "staff", "skip"]


@dataclass(frozen=True)
class JobClassification:
    is_relevant: bool
    role_type: RoleType
    seniority: Seniority
    is_duplicate_suspect: bool
    skip_reason: str | None


class JobClassifier:
    def __init__(
        self,
        client: OllamaClient = ollama_client,
        applicant_years_experience: int = 5,
        company_blacklist: set[str] | None = None,
    ) -> None:
        self.client = client
        self.applicant_years_experience = applicant_years_experience
        self.company_blacklist = {company.lower() for company in (company_blacklist or set())}

    async def classify(self, job_title: str, job_description: str) -> JobClassification:
        deterministic_skip = self._deterministic_skip(job_title, job_description)
        if deterministic_skip:
            return deterministic_skip

        prompt = f"""
Classify this job for a software/AI applicant.

Skip the job if:
- It requires more than 12 years of experience (extremely senior only).
- It is clearly unrelated, such as sales, marketing, recruiter, product manager, project manager, or customer success.
- It appears to be from a company on the user blacklist.

Return only valid JSON:
{{
  "is_relevant": true,
  "role_type": "AI Engineer",
  "seniority": "mid",
  "is_duplicate_suspect": false,
  "skip_reason": null
}}

Allowed role_type values: AI Engineer, SDE, ML Engineer, Data Engineer, Full Stack, Backend, Other.
Allowed seniority values: junior, mid, senior, staff, skip.

Applicant years of experience: {self.applicant_years_experience}
Company blacklist: {sorted(self.company_blacklist)}

Job title:
{job_title}

Job description:
{job_description[:9000]}
""".strip()
        raw = await self.client.generate(prompt=prompt, model=FAST_MODEL, json_mode=True)
        data = extract_json_object(raw)
        return JobClassification(
            is_relevant=bool(data.get("is_relevant")),
            role_type=normalize_role_type(data.get("role_type")),
            seniority=normalize_seniority(data.get("seniority")),
            is_duplicate_suspect=bool(data.get("is_duplicate_suspect")),
            skip_reason=str(data["skip_reason"]) if data.get("skip_reason") else None,
        )

    def _deterministic_skip(self, job_title: str, job_description: str) -> JobClassification | None:
        haystack = f"{job_title}\n{job_description}".lower()
        for company in self.company_blacklist:
            if company and company in haystack:
                return JobClassification(
                    is_relevant=False,
                    role_type="Other",
                    seniority="skip",
                    is_duplicate_suspect=False,
                    skip_reason=f"Company is on user blacklist: {company}",
                )

        unrelated_terms = ("sales", "marketing", "recruiter", "product manager", "project manager", "customer success")
        if any(term in job_title.lower() for term in unrelated_terms):
            return JobClassification(
                is_relevant=False,
                role_type="Other",
                seniority="skip",
                is_duplicate_suspect=False,
                skip_reason="Role title is clearly unrelated to software or AI engineering.",
            )

        required_years = extract_required_years(job_description)
        if self.applicant_years_experience < required_years and required_years and required_years > 12:
            return JobClassification(
                is_relevant=False,
                role_type="Other",
                seniority="skip",
                is_duplicate_suspect=False,
                skip_reason=f"Role appears to require {required_years}+ years of experience.",
            )
        return None


def extract_required_years(text: str) -> int | None:
    matches = re.findall(r"(\d{1,2})\+?\s*(?:years|yrs|yoe)", text.lower())
    if not matches:
        return None
    return max(int(match) for match in matches)


def normalize_role_type(value: object) -> RoleType:
    allowed: set[RoleType] = {"AI Engineer", "SDE", "ML Engineer", "Data Engineer", "Full Stack", "Backend", "Other"}
    text = str(value or "Other").strip()
    return text if text in allowed else "Other"


def normalize_seniority(value: object) -> Seniority:
    allowed: set[Seniority] = {"junior", "mid", "senior", "staff", "skip"}
    text = str(value or "skip").strip().lower()
    return text if text in allowed else "skip"
