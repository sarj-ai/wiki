from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class McpRegistrationAdmission(Base):
    __tablename__ = "mcp_registration_admission"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_mcp_registration_singleton"),
        CheckConstraint(
            "cardinality(admitted_at) <= 300 AND "
            "(cardinality(admitted_at) = 0 OR array_ndims(admitted_at) = 1) AND "
            "array_position(admitted_at, NULL) IS NULL",
            name="ck_mcp_registration_bounded_history",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admitted_at: Mapped[list[datetime]] = mapped_column(ARRAY(DateTime(timezone=True)))
