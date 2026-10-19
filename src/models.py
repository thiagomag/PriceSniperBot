from datetime import UTC, datetime
from decimal import Decimal
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class MonitoredProduct(Base):
    __tablename__ = "monitored_products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    store: Mapped[str] = mapped_column(String(50), nullable=False)

    # Regras de Notificação e Preço
    target_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    discount_percentage: Mapped[int | None] = mapped_column(nullable=True)
    notify_on_sale_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    regional_prices: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Estado Operacional
    in_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_buy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Controle de Agendamento
    check_interval_sec: Mapped[int] = mapped_column(default=300, nullable=False)
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    logs: Mapped[list["ExecutionLog"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_active_next_check", "is_active", "next_check_at"),
    )


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitored_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    price_found: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    product: Mapped[MonitoredProduct] = relationship(back_populates="logs")