from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db.models import QAMemory
from backend.services.qa_memory import answer_question, save_user_answer


async def get_application_answer(db: Session, question: str, answer_type: str = "text") -> QAMemory:
    return await answer_question(db=db, question=question, answer_type=answer_type, tags=["application"])


def teach_application_answer(db: Session, question: str, answer: str, answer_type: str = "text") -> QAMemory:
    return save_user_answer(db=db, question=question, answer=answer, answer_type=answer_type, tags=["application"])
