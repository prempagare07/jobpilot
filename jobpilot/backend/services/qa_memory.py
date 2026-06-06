from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import QAMemory
from backend.services.hash_utils import sha256_text
from backend.services.ollama_client import ollama_client
from backend.services.vector_store import vector_store


async def answer_question(
    db: Session,
    question: str,
    answer_type: str = "text",
    tags: list[str] | None = None,
) -> QAMemory:
    question_hash = sha256_text(question)
    memory = db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))
    if memory:
        memory.times_used += 1
        memory.last_used_at = datetime.utcnow()
        db.commit()
        db.refresh(memory)
        return memory

    prompt = (
        "Answer this job application form question as the applicant. "
        "Be truthful, concise, and do not invent credentials.\n\n"
        f"Question: {question}\n"
        f"Answer type: {answer_type}"
    )
    answer = await ollama_client.quick(prompt)
    memory = QAMemory(
        question_hash=question_hash,
        question_text=question,
        answer_text=answer,
        answer_type=answer_type,
        confidence=0.55,
        times_used=1,
        last_used_at=datetime.utcnow(),
        source="ai_generated",
        tags_json=tags or [],
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    vector_store.upsert_text(f"qa:{memory.id}", f"{question}\n{answer}", {"kind": "qa", "question_hash": question_hash})
    return memory


def save_user_answer(
    db: Session,
    question: str,
    answer: str,
    answer_type: str = "text",
    tags: list[str] | None = None,
) -> QAMemory:
    question_hash = sha256_text(question)
    memory = db.scalar(select(QAMemory).where(QAMemory.question_hash == question_hash))
    if memory is None:
        memory = QAMemory(
            question_hash=question_hash,
            question_text=question,
            answer_text=answer,
            answer_type=answer_type,
            confidence=1.0,
            times_used=0,
            source="user_provided",
            tags_json=tags or [],
        )
        db.add(memory)
    else:
        memory.question_text = question
        memory.answer_text = answer
        memory.answer_type = answer_type
        memory.confidence = 1.0
        memory.source = "user_provided"
        memory.tags_json = tags or []
    db.commit()
    db.refresh(memory)
    vector_store.upsert_text(f"qa:{memory.id}", f"{question}\n{answer}", {"kind": "qa", "question_hash": question_hash})
    return memory
