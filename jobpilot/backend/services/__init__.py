"""Service layer for JobPilot."""

from backend.services.apollo import ApolloClient, ApolloContact
from backend.services.application_service import ApplicationResult, ApplicationService, LimitReachedError
from backend.services.email_service import EmailSendResult, EmailService
from backend.services.form_filler import FilledFormResult, FormFiller
from backend.services.generic_apply import GenericApplyService
from backend.services.indeed_apply import IndeedApplyService
from backend.services.linkedin_apply import LinkedInApplyService
from backend.services.outreach_service import OutreachResult, OutreachService

__all__ = [
    "ApplicationResult",
    "ApplicationService",
    "ApolloClient",
    "ApolloContact",
    "EmailSendResult",
    "EmailService",
    "FilledFormResult",
    "FormFiller",
    "GenericApplyService",
    "IndeedApplyService",
    "LimitReachedError",
    "LinkedInApplyService",
    "OutreachResult",
    "OutreachService",
]
