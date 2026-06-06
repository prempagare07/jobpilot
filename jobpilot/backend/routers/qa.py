from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.qa_engine import QAAnswer, QAEngine
from backend.db.models import Profile, QAMemory
from backend.services.apply_common import model_to_dict
from backend.services.database import get_db

router = APIRouter(prefix="/api/qa", tags=["qa"])


class QAMemoryOut(BaseModel):
    id: int
    question_hash: str
    question_text: str
    answer_text: str
    answer_type: str
    confidence: float
    times_used: int
    last_used_at: datetime | None
    source: str
    tags_json: list[str]

    model_config = {"from_attributes": True}


class HumanAnswerIn(BaseModel):
    question_hash: str
    answer: str


class QAMemoryUpdateIn(BaseModel):
    question_text: str | None = None
    answer_text: str | None = None
    answer_type: str | None = Field(default=None, pattern="^(text|yesno|number|select)$")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str | None = Field(default=None, pattern="^(user_provided|ai_generated)$")
    tags_json: list[str] | None = None


class QATestIn(BaseModel):
    question: str
    context: dict[str, Any] = Field(default_factory=dict)


class QAAnswerOut(BaseModel):
    answer: str
    confidence: float
    source: str
    question_hash: str


@router.get("/memory", response_model=list[QAMemoryOut])
def list_memory(db: Annotated[Session, Depends(get_db)]) -> list[QAMemory]:
    return list(db.scalars(select(QAMemory).order_by(QAMemory.last_used_at.desc().nullslast(), QAMemory.id.desc())))


@router.get("/pending", response_model=list[QAMemoryOut])
def pending_questions(db: Annotated[Session, Depends(get_db)]) -> list[QAMemory]:
    return list(db.scalars(select(QAMemory).where(QAMemory.confidence < 0.6).order_by(QAMemory.id.desc())))


@router.post("/answer", response_model=QAMemoryOut)
async def submit_human_answer(payload: HumanAnswerIn, db: Annotated[Session, Depends(get_db)]) -> QAMemory:
    await QAEngine(db).learn(payload.question_hash, payload.answer)
    memory = db.scalar(select(QAMemory).where(QAMemory.question_hash == payload.question_hash))
    if memory is None:
        raise HTTPException(status_code=404, detail="Q&A memory not found after update")
    db.refresh(memory)
    return memory


@router.put("/memory/{memory_id}", response_model=QAMemoryOut)
def update_memory(memory_id: int, payload: QAMemoryUpdateIn, db: Annotated[Session, Depends(get_db)]) -> QAMemory:
    memory = db.get(QAMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Q&A memory not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(memory, key, value)
    db.commit()
    db.refresh(memory)
    return memory


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, db: Annotated[Session, Depends(get_db)]) -> dict[str, bool]:
    memory = db.get(QAMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Q&A memory not found")
    db.delete(memory)
    db.commit()
    return {"deleted": True}


@router.post("/test", response_model=QAAnswerOut)
async def test_qa(payload: QATestIn, db: Annotated[Session, Depends(get_db)]) -> QAAnswer:
    profile = db.scalar(select(Profile).order_by(Profile.id.asc()))
    context = dict(payload.context)
    if profile is not None and "profile" not in context:
        context["profile"] = model_to_dict(profile)
    return await QAEngine(db).answer(payload.question, context)
