from __future__ import annotations

from dataclasses import dataclass

from backend.agents.ats_scorer import ATSResult
from backend.agents.json_utils import as_string_list, compact_words, extract_json_object
from backend.agents.ollama_client import SMART_MODEL, OllamaClient, ollama_client


@dataclass(frozen=True)
class CoverLetterResult:
    subject_line: str
    body: str
    word_count: int
    key_points_addressed: list[str]


class CoverLetterAgent:
    def __init__(self, client: OllamaClient = ollama_client) -> None:
        self.client = client

    async def generate(
        self,
        job: dict,
        profile: dict,
        resume_text: str,
        ats_result: ATSResult,
    ) -> CoverLetterResult:
        system = """
You write tailored cover letters for technical job applications.
Use a professional but conversational tone, never generic.
Open with a specific hook about the company or role, not "I am writing to apply".
Paragraph 1: why this specific company or role excites the applicant.
Use the company name and concrete job-description details.
Paragraph 2: the applicant's top 2-3 relevant achievements with numbers where possible.
Paragraph 3: specific technical skill matches from the supplied ATS matched keywords.
Closing: clear call to action and availability.
Maximum 350 words.
Do not use these phrases: "I am passionate", "leverage", "synergy", "team player".
Return only valid JSON: {"subject_line": "...", "body": "...", "word_count": 0, "key_points_addressed": ["..."]}.
""".strip()
        user = f"""
Job:
Title: {job.get("title", "")}
Company: {job.get("company", "")}
Location: {job.get("location", "")}
Description:
{str(job.get("job_description") or job.get("description") or "")[:9000]}

Applicant profile:
Name: {profile.get("full_name", "")}
Email: {profile.get("email", "")}
LinkedIn: {profile.get("linkedin_url", "")}
GitHub: {profile.get("github_url", "")}
Portfolio: {profile.get("portfolio_url", "")}
Summary: {profile.get("summary", "")}
Skills: {profile.get("skills_json") or profile.get("skills") or []}
Target roles: {profile.get("target_roles_json") or []}
Experience modules: {profile.get("experience_json") or []}
Projects: {profile.get("projects_json") or profile.get("projects") or []}
Achievements: {profile.get("achievements_json") or profile.get("achievements") or []}
Certifications: {profile.get("certifications_json") or []}

Resume text:
{resume_text[:9000]}

ATS matched keywords:
{ats_result.matched_keywords}

ATS missing keywords:
{ats_result.missing_keywords}

ATS tailoring suggestions:
{ats_result.tailoring_suggestions}
""".strip()
        raw = await self.client.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=SMART_MODEL,
            json_mode=True,
        )
        data = extract_json_object(raw)
        body = compact_words(str(data.get("body") or ""), 350)
        return CoverLetterResult(
            subject_line=str(data.get("subject_line") or f"{job.get('company', 'Your team')} application").strip(),
            body=body,
            word_count=len(body.split()),
            key_points_addressed=as_string_list(data.get("key_points_addressed")),
        )
