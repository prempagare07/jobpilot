from __future__ import annotations

from dataclasses import dataclass

from backend.agents.json_utils import as_string_list, compact_words, extract_json_object
from backend.agents.ollama_client import SMART_MODEL, OllamaClient, ollama_client


@dataclass(frozen=True)
class EmailDraft:
    subject: str
    body: str
    personalization_notes: list[str]


class ColdEmailWriter:
    def __init__(self, client: OllamaClient = ollama_client) -> None:
        self.client = client

    async def write(
        self,
        contact: dict,
        job: dict,
        profile: dict,
        template: dict | None = None,
    ) -> EmailDraft:
        achievements = (
            profile.get("projects_json")
            or profile.get("projects")
            or profile.get("achievements_json")
            or profile.get("achievements")
            or profile.get("experience_json")
            or ""
        )
        system = """
You write concise, personalized cold emails for job networking.
Subject line: max 8 words, specific, and do not use "following up" or "reaching out".
Body: exactly 4 short paragraphs, max 200 words total.
Paragraph 1: one sentence on why the applicant is contacting THIS person at THIS company.
Paragraph 2: one specific thing the applicant built or shipped that is relevant to their stack.
Paragraph 3: one question about the team or role that shows research.
Paragraph 4: low-pressure call to action, using wording like "Happy to share more if helpful".
Sign off with the applicant's name, LinkedIn URL, and GitHub URL.
Do not use these phrases: "I hope this email finds you well", "I am reaching out because", "I would love to".
Return only valid JSON: {"subject": "...", "body": "...", "personalization_notes": ["..."]}.
""".strip()
        user = f"""
Contact:
Name: {contact.get("name", "")}
Title: {contact.get("title", "")}
Company: {contact.get("company", "")}
LinkedIn: {contact.get("linkedin_url", "")}

Job:
Title: {job.get("title", "")}
Company: {job.get("company") or contact.get("company", "")}
Description excerpt:
{str(job.get("job_description") or job.get("description") or "")[:200]}

Applicant profile:
Name: {profile.get("full_name", "")}
LinkedIn: {profile.get("linkedin_url", "")}
GitHub: {profile.get("github_url", "")}
Summary: {profile.get("summary", "")}
Skills: {profile.get("skills_json") or profile.get("skills") or []}
Target roles: {profile.get("target_roles_json") or []}
Projects or achievements: {achievements}

Optional template:
{template or {}}
""".strip()
        raw = await self.client.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=SMART_MODEL,
            json_mode=True,
        )
        data = extract_json_object(raw)
        subject = trim_subject(str(data.get("subject") or "Question about your team"))
        body = compact_words(remove_forbidden_phrases(str(data.get("body") or "")), 200)
        return EmailDraft(
            subject=subject,
            body=body,
            personalization_notes=as_string_list(data.get("personalization_notes")),
        )


def trim_subject(subject: str) -> str:
    forbidden = ("following up", "reaching out")
    cleaned = subject.strip()
    for phrase in forbidden:
        cleaned = cleaned.replace(phrase, "").replace(phrase.title(), "")
    words = cleaned.split()
    if len(words) > 8:
        cleaned = " ".join(words[:8])
    return cleaned.strip(" -:") or "Question about your team"


def remove_forbidden_phrases(body: str) -> str:
    cleaned = body
    replacements = {
        "I hope this email finds you well": "",
        "I am reaching out because": "",
        "I would love to": "I would be glad to",
    }
    for phrase, replacement in replacements.items():
        cleaned = cleaned.replace(phrase, replacement)
        cleaned = cleaned.replace(phrase.lower(), replacement)
    return "\n\n".join(paragraph.strip() for paragraph in cleaned.splitlines() if paragraph.strip())
