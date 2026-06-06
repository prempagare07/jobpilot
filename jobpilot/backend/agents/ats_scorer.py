from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from backend.agents.json_utils import as_string_list, clamp_int, extract_json_object
from backend.agents.ollama_client import FAST_MODEL, OllamaClient, ollama_client


@dataclass(frozen=True)
class ATSResult:
    resume_name: str
    score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    recommendation: str
    tailoring_suggestions: list[str]


class ATSScorer:
    def __init__(self, client: OllamaClient = ollama_client) -> None:
        self.client = client

    async def score(self, job_description: str, resume_text: str, resume_name: str) -> ATSResult:
        prompt = f"""
You are an ATS scoring engine for job applications.

Tasks:
1. Extract the top 30 keywords and skills from the job description. Separate them into required and preferred keywords.
2. Check which extracted keywords appear in the resume. Accept close semantic matches, common abbreviations,
   and equivalent tools.
3. Calculate score exactly as:
   score = (matched_required * 2 + matched_preferred) / total_possible * 100
   where total_possible = required_keywords_count * 2 + preferred_keywords_count.
4. Suggest 3-5 specific resume bullet point rewrites to improve the score. Make the suggestions concrete and tied
   to missing or weak keywords.

Return only valid JSON with this shape:
{{
  "resume_name": "{resume_name}",
  "score": 72,
  "matched_keywords": ["<keyword found in both JD and resume>"],
  "missing_keywords": ["<keyword in JD but not in resume>"],
  "recommendation": "<one sentence>",
  "tailoring_suggestions": ["<rewrite suggestion>"]
}}

Job description:
{job_description[:12000]}

Resume name: {resume_name}
Resume text:
{resume_text[:12000]}
""".strip()
        raw = await self.client.generate(prompt=prompt, model=FAST_MODEL, json_mode=True)
        data = extract_json_object(raw)
        return ATSResult(
            resume_name=str(data.get("resume_name") or resume_name),
            score=clamp_int(data.get("score"), 0, 100),
            matched_keywords=as_string_list(data.get("matched_keywords")),
            missing_keywords=as_string_list(data.get("missing_keywords")),
            recommendation=str(data.get("recommendation") or "Review keyword coverage before applying."),
            tailoring_suggestions=as_string_list(data.get("tailoring_suggestions"))[:5],
        )

    async def pick_best_resume(self, job_description: str, resume_versions: list[dict]) -> tuple[str, ATSResult]:
        if not resume_versions:
            raise ValueError("resume_versions must include at least one resume")

        async def score_version(version: dict[str, Any]) -> ATSResult:
            resume_name = str(version.get("resume_name") or version.get("name") or "Unnamed resume")
            resume_text = str(version.get("resume_text") or version.get("text") or version.get("content") or "")
            if not resume_text.strip():
                raise ValueError(f"Resume version '{resume_name}' is missing resume_text")
            return await self.score(
                job_description=job_description,
                resume_text=resume_text,
                resume_name=resume_name,
            )

        results = await asyncio.gather(*(score_version(version) for version in resume_versions))
        best_result = max(results, key=lambda result: result.score)
        return best_result.resume_name, best_result
