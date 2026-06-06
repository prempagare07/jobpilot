"""AI agents for scoring, writing, classification, and application Q&A."""

from backend.agents.ats_scorer import ATSResult, ATSScorer
from backend.agents.cover_letter import CoverLetterAgent, CoverLetterResult
from backend.agents.email_writer import ColdEmailWriter, EmailDraft
from backend.agents.job_classifier import JobClassification, JobClassifier
from backend.agents.ollama_client import FAST_MODEL, SMART_MODEL, OllamaClient, OllamaOfflineError
from backend.agents.qa_engine import QAAnswer, QAEngine

__all__ = [
    "ATSResult",
    "ATSScorer",
    "ColdEmailWriter",
    "CoverLetterAgent",
    "CoverLetterResult",
    "EmailDraft",
    "FAST_MODEL",
    "JobClassification",
    "JobClassifier",
    "OllamaClient",
    "OllamaOfflineError",
    "QAAnswer",
    "QAEngine",
    "SMART_MODEL",
]
