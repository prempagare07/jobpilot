from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.json_utils import clamp_float, extract_json_object
from backend.agents.ollama_client import FAST_MODEL, OllamaClient, ollama_client
from backend.db.models import QAMemory

QA_SOURCE = Literal["memory", "ai", "needs_human"]


@dataclass(frozen=True)
class QAAnswer:
    answer: str
    confidence: float
    source: QA_SOURCE
    question_hash: str


class QAEngine:
    def __init__(self, db: Session, client: OllamaClient = ollama_client) -> None:
        self.db = db
        self.client = client

    async def answer(self, question: str, context: dict) -> QAAnswer:
        profile = context.get("profile") or context.get("profile_data") or {}
        self.prepopulate_common_questions(profile)

        normalized = normalize_question(question)
        question_hash = hash_question(normalized)
        memory = self.db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))

        if memory and memory.confidence >= 0.8:
            memory.times_used += 1
            memory.last_used_at = datetime.utcnow()
            self.db.commit()
            return QAAnswer(
                answer=memory.answer_text,
                confidence=memory.confidence,
                source="memory",
                question_hash=question_hash,
            )

        if memory and 0.5 <= memory.confidence < 0.8:
            raw = await self._ask_ollama(question=question, context=context, existing_answer=memory.answer_text)
        else:
            raw = await self._ask_ollama(question=question, context=context, existing_answer=None)

        data = extract_json_object(raw)
        answer_text = str(data.get("answer") or "").strip()
        confidence = clamp_float(data.get("confidence"), 0.0, 1.0, default=0.55)
        if not answer_text:
            answer_text = "Needs human input before this answer can be used."
            confidence = 0.0

        needs_human = confidence < 0.6 or question_needs_human(question)
        stored = self._upsert_memory(
            question_hash=question_hash,
            question_text=question,
            answer_text=answer_text,
            confidence=confidence,
            source="ai_generated",
        )
        return QAAnswer(
            answer=stored.answer_text,
            confidence=stored.confidence,
            source="needs_human" if needs_human else "ai",
            question_hash=question_hash,
        )

    async def learn(self, question_hash: str, human_answer: str) -> None:
        memory = self.db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))
        if memory is None:
            memory = QAMemory(
                question_hash=question_hash,
                question_text=question_hash,
                answer_text=human_answer,
                answer_type="text",
                confidence=1.0,
                times_used=0,
                source="user_provided",
                tags_json=["application", "human_reviewed"],
            )
            self.db.add(memory)
        else:
            memory.answer_text = human_answer
            memory.confidence = 1.0
            memory.source = "user_provided"
            memory.last_used_at = datetime.utcnow()
            tags = set(memory.tags_json or [])
            tags.update({"application", "human_reviewed"})
            memory.tags_json = sorted(tags)
        self.db.commit()

    def prepopulate_common_questions(self, profile: Any) -> None:
        common_answers = common_profile_answers(profile)
        for question, answer in common_answers.items():
            normalized = normalize_question(question)
            question_hash = hash_question(normalized)
            existing = self.db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))
            if existing is not None:
                continue
            confidence = 1.0 if answer and "needs human input" not in answer.lower() else 0.0
            self.db.add(
                QAMemory(
                    question_hash=question_hash,
                    question_text=question,
                    answer_text=answer or "Needs human input before this answer can be used.",
                    answer_type="text",
                    confidence=confidence,
                    times_used=0,
                    source="user_provided" if confidence == 1.0 else "ai_generated",
                    tags_json=["application", "common_question"],
                )
            )
        self.db.commit()

    async def _ask_ollama(self, question: str, context: dict, existing_answer: str | None) -> str:
        prompt = f"""
Answer this job application question using only the supplied profile and context.
If the answer is uncertain, sensitive, salary-related, date-specific, or depends on a unique personal situation,
lower confidence below 0.6.
Be concise and truthful. Do not invent credentials.

Return only valid JSON:
{{"answer": "short answer", "confidence": 0.0}}

Question:
{question}

Existing memory answer to refine:
{existing_answer or ""}

Context:
{context}
""".strip()
        return await self.client.generate(prompt=prompt, model=FAST_MODEL, json_mode=True)

    def _upsert_memory(
        self,
        question_hash: str,
        question_text: str,
        answer_text: str,
        confidence: float,
        source: Literal["user_provided", "ai_generated"],
    ) -> QAMemory:
        memory = self.db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))
        if memory is None:
            memory = QAMemory(
                question_hash=question_hash,
                question_text=question_text,
                answer_text=answer_text,
                answer_type="text",
                confidence=confidence,
                times_used=1,
                last_used_at=datetime.utcnow(),
                source=source,
                tags_json=["application"],
            )
            self.db.add(memory)
        else:
            memory.question_text = question_text
            memory.answer_text = answer_text
            memory.confidence = confidence
            memory.times_used += 1
            memory.last_used_at = datetime.utcnow()
            memory.source = source
        self.db.commit()
        self.db.refresh(memory)
        return memory


def normalize_question(question: str) -> str:
    lowered = question.lower()
    no_punctuation = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", no_punctuation).strip()


def hash_question(normalized_question: str) -> str:
    return hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()


def question_needs_human(question: str) -> bool:
    lowered = question.lower()
    human_markers = (
        "salary",
        "compensation",
        "pay range",
        "specific date",
        "exact date",
        "start date",
        "unique situation",
        "explain a gap",
        "criminal",
        "disability",
    )
    return any(marker in lowered for marker in human_markers)


def common_profile_answers(profile: Any) -> dict[str, str]:
    work_auth = str(profile_value(profile, "work_authorization") or "").strip()
    years = profile_value(profile, "years_experience")
    willing_relocate = profile_value(profile, "willing_to_relocate")
    if willing_relocate is None:
        willing_relocate = profile_value(profile, "willing_relocate")
    salary_min = profile_value(profile, "salary_min")
    salary_max = profile_value(profile, "salary_max")
    preferences = profile_value(profile, "application_preferences_json") or {}
    if not isinstance(preferences, dict):
        preferences = {}
    target_roles = profile_value(profile, "target_roles_json") or []
    preferred_locations = profile_value(profile, "preferred_locations_json") or []

    sponsorship = derive_sponsorship_answer(work_auth, preferences.get("requires_sponsorship"))
    relocate_answer = derive_yes_no_answer(
        willing_relocate,
        "Needs human input: relocation preference has not been set.",
    )
    salary_answer = salary_range_answer(salary_min, salary_max)
    years_answer = str(years) if years is not None else "Needs human input: years of experience has not been set."

    return {
        "Are you authorized to work in the US?": work_auth or "Needs human input: work authorization has not been set.",
        "Do you require visa sponsorship?": sponsorship,
        "Are you willing to relocate?": relocate_answer,
        "What is your desired salary?": salary_answer,
        "Are you available for full-time employment?": "Yes.",
        "How many years of experience do you have?": years_answer,
        "What roles are you targeting?": join_answer(target_roles, "Needs human input: target roles have not been set."),
        "What locations are you open to?": join_answer(
            preferred_locations,
            "Needs human input: preferred locations have not been set.",
        ),
        "What is your earliest start date?": str(
            preferences.get("earliest_start_date") or "Needs human input: earliest start date has not been set."
        ),
        "Are you open to remote work?": str(
            preferences.get("remote_preference") or "Needs human input: remote preference has not been set."
        ),
    }


def profile_value(profile: Any, key: str) -> Any:
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)


def derive_sponsorship_answer(work_auth: str, requires_sponsorship: Any = None) -> str:
    if isinstance(requires_sponsorship, bool):
        return "Yes, I require or will require visa sponsorship." if requires_sponsorship else "No."
    normalized = work_auth.strip().lower()
    if normalized in {"us citizen", "gc", "green card", "permanent resident"}:
        return "No."
    if normalized in {"h1b", "h-1b", "opt", "cpt"}:
        return "Yes, I require or will require visa sponsorship."
    return "Needs human input: work authorization has not been set."


def derive_yes_no_answer(value: Any, fallback: str) -> str:
    if isinstance(value, bool):
        return "Yes." if value else "No."
    if isinstance(value, str) and value.strip():
        lowered = value.strip().lower()
        if lowered in {"yes", "true", "y", "1"}:
            return "Yes."
        if lowered in {"no", "false", "n", "0"}:
            return "No."
        return value.strip()
    return fallback


def salary_range_answer(salary_min: Any, salary_max: Any) -> str:
    if salary_min and salary_max:
        return f"${salary_min} - ${salary_max}"
    if salary_min:
        return f"At least ${salary_min}"
    if salary_max:
        return f"Up to ${salary_max}"
    return "Needs human input: desired salary has not been set."


def join_answer(values: Any, fallback: str) -> str:
    if isinstance(values, list):
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        return ", ".join(cleaned) if cleaned else fallback
    if isinstance(values, str) and values.strip():
        return values.strip()
    return fallback
