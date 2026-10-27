"""Import every table module so ``Base.metadata`` is complete (used by Alembic autogenerate and tests)."""

from kompas.core import outbox
from kompas.core.db import Base
from kompas.modules.adherence import models as adherence
from kompas.modules.adverse_events import models as adverse_events
from kompas.modules.appointments import models as appointments
from kompas.modules.assessments import models as assessments
from kompas.modules.audit import models as audit
from kompas.modules.cases import models as cases
from kompas.modules.diary import models as diary
from kompas.modules.documents import models as documents
from kompas.modules.identity import models as identity
from kompas.modules.labs import models as labs
from kompas.modules.monitoring import models as monitoring
from kompas.modules.notifications import models as notifications
from kompas.modules.referrals import models as referrals
from kompas.modules.therapy import models as therapy

__all__ = [
    "Base",
    "adherence",
    "adverse_events",
    "appointments",
    "assessments",
    "audit",
    "cases",
    "diary",
    "documents",
    "identity",
    "labs",
    "monitoring",
    "notifications",
    "outbox",
    "referrals",
    "therapy",
]
