from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import random
from typing import Any
import uuid

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ExecutionLog, MonitoredProduct


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_due_products(self, limit: int) -> Sequence[MonitoredProduct]:
        """
        Busca produtos ativos cujo next_check_at já venceu, aplicando lock de linha
        com SKIP LOCKED e reagendando imediatamente com jitter de +/- 20%.
        """
        stmt = (
            select(MonitoredProduct)
            .where(
                MonitoredProduct.is_active.is_(True),
                MonitoredProduct.next_check_at <= func.now(),
            )
            .order_by(MonitoredProduct.next_check_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(stmt)
        products = result.scalars().all()

        now = datetime.now(UTC)
        for product in products:
            jitter = random.uniform(0.8, 1.2)
            next_delay = product.check_interval_sec * jitter
            product.next_check_at = now + timedelta(seconds=next_delay)

        return products

    async def get_by_id(self, product_id: uuid.UUID) -> MonitoredProduct | None:
        stmt = select(MonitoredProduct).where(MonitoredProduct.id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_prefix(self, prefix: str) -> MonitoredProduct | None:
        stmt = select(MonitoredProduct).where(
            cast(MonitoredProduct.id, String).ilike(f"{prefix}%")
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_product(
        self,
        url: str,
        store: str,
        target_price: Decimal,
        check_interval_sec: int = 300,
        auto_buy: bool = False,
        notify_on_sale_only: bool = False,
    ) -> MonitoredProduct:
        product = MonitoredProduct(
            url=url,
            store=store,
            target_price=target_price,
            check_interval_sec=check_interval_sec,
            auto_buy=auto_buy,
            notify_on_sale_only=notify_on_sale_only,
        )
        self.session.add(product)
        await self.session.flush()
        return product

    async def list_active_products(self) -> Sequence[MonitoredProduct]:
        stmt = (
            select(MonitoredProduct)
            .where(MonitoredProduct.is_active.is_(True))
            .order_by(MonitoredProduct.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_interval(self, product_id: uuid.UUID, new_interval_sec: int) -> bool:
        stmt = (
            update(MonitoredProduct)
            .where(MonitoredProduct.id == product_id)
            .values(
                check_interval_sec=new_interval_sec,
                next_check_at=func.now(),
            )
            .returning(MonitoredProduct.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def set_active_status(self, product_id: uuid.UUID, is_active: bool) -> bool:
        stmt = (
            update(MonitoredProduct)
            .where(MonitoredProduct.id == product_id)
            .values(is_active=is_active)
            .returning(MonitoredProduct.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def claim_for_checkout(self, product_id: uuid.UUID) -> bool:
        """
        Trava otimista (Circuit Breaker): desativa imediatamente o produto antes de
        iniciar o fluxo de auto-checkout, garantindo que apenas 1 worker compre.
        """
        stmt = (
            update(MonitoredProduct)
            .where(
                MonitoredProduct.id == product_id,
                MonitoredProduct.is_active.is_(True),
            )
            .values(is_active=False)
            .returning(MonitoredProduct.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def update_product_scrape_state(
        self,
        product_id: uuid.UUID,
        price: Decimal | None,
        in_stock: bool,
        original_price: Decimal | None = None,
        discount_percentage: int | None = None,
        regional_prices: dict[str, Any] | None = None,
    ) -> None:
        stmt = (
            update(MonitoredProduct)
            .where(MonitoredProduct.id == product_id)
            .values(
                last_price=price,
                in_stock=in_stock,
                original_price=original_price,
                discount_percentage=discount_percentage,
                regional_prices=regional_prices,
            )
        )
        await self.session.execute(stmt)

    async def record_execution_log(
        self,
        product_id: uuid.UUID,
        in_stock: bool,
        status: str,
        price_found: Decimal | None = None,
        error_message: str | None = None,
    ) -> ExecutionLog:
        log_entry = ExecutionLog(
            product_id=product_id,
            price_found=price_found,
            in_stock=in_stock,
            status=status,
            error_message=error_message,
        )
        self.session.add(log_entry)
        await self.session.flush()
        return log_entry