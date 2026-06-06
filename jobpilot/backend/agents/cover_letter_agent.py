from __future__ import annotations

from backend.db.models import Job, Profile, ResumeVersion
from backend.services.ollama_client import ollama_client


async def generate_cover_letter(profile: Profile, resume: ResumeVersion, job: Job) -> str:
    system = (
        "You write concise, specific cover letters for software and AI roles. "
        "Use only the applicant information provided and keep the tone confident, direct, and human."
    )
    prompt = f"""
Applicant:
Name: {profile.full_name}
Location: {profile.location or ""}
Experience: {profile.years_experience} years
Summary: {profile.summary or ""}
Skills: {", ".join(profile.skills_json or [])}
Target roles: {", ".join(profile.target_roles_json or [])}
Experience modules: {profile.experience_json or []}
Projects: {profile.projects_json or []}
Achievements: {profile.achievements_json or []}
Certifications: {profile.certifications_json or []}

Resume version: {resume.name}
Target roles: {", ".join(resume.target_roles or [])}

Job:
Title: {job.title}
Company: {job.company}
Location: {job.location or ""}
Description:
{job.job_description[:6000]}

Write a cover letter under 300 words. Do not include fake metrics or experience.
""".strip()
    return await ollama_client.smart(prompt=prompt, system=system)
