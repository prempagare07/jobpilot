from __future__ import annotations

from backend.db.models import Job, OutreachContact, Profile
from backend.services.ollama_client import ollama_client


async def generate_outreach_email(
    profile: Profile,
    contact: OutreachContact,
    job: Job | None = None,
) -> tuple[str, str]:
    role_line = f"{job.title} at {job.company}" if job else f"roles at {contact.company}"
    prompt = f"""
Applicant: {profile.full_name}
Applicant summary: {profile.summary or ""}
Applicant skills: {", ".join(profile.skills_json or [])}
Contact: {contact.name}, {contact.title or "team member"} at {contact.company}
Opportunity: {role_line}

Create a short networking email. Return exactly two sections:
Subject: <subject>
Body: <body>
""".strip()
    response = await ollama_client.smart(prompt)
    subject = "Quick question about opportunities"
    body = response
    for line in response.splitlines():
        if line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
        if line.lower().startswith("body:"):
            body = response.split(line, 1)[1].strip()
            break
    return subject, body
