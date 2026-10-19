import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
import logging
from typing import Any, Protocol
import uuid

from src.config import settings
from src.database import get_db_session
from src.models import MonitoredProduct
from src.repositories import ProductRepository

logger = logging.getLogger(__name__)


class ScrapeDataProtocol(Protocol):
    """Protocolo estrutural compatível com o ScrapeResult da Fase 3."""

    @property
    def price(self) -> Decimal | None: ...
    @property
    def in_stock(self) -> bool: ...
    @property
    def raw_status(self) -> str: ...
    @property
    def is_discounted(self) -> bool: ...
    @property
    def original_price(self) -> Decimal | None: ...
    @property
    def discount_percentage(self) -> int | None: ...
    @property
    def regional_prices(self) -> dict[str, Any] | None: ...


ScraperCallback = Callable[[MonitoredProduct], Awaitable[ScrapeDataProtocol]]
AlertCallback = Callable[[MonitoredProduct, ScrapeDataProtocol], Awaitable[None]]


class Dispatcher:
    def __init__(
        self,
        scraper_fn: ScraperCallback | None = None,
        alert_fn: AlertCallback | None = None,
    ) -> None:
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)
        self._running = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._scraper_fn = scraper_fn
        self._alert_fn = alert_fn

    async def run_forever(self) -> None:
        self._running = True
        logger.info(
            "Dispatcher iniciado (MAX_CONCURRENT_TASKS=%d)",
            settings.MAX_CONCURRENT_TASKS,
        )

        while self._running:
            try:
                available_slots = self.semaphore._value
                if available_slots > 0:
                    due_ids: list[uuid.UUID] = []
                    async with get_db_session() as session:
                        repo = ProductRepository(session)
                        products = await repo.fetch_due_products(limit=available_slots)
                        due_ids = [p.id for p in products]

                    for product_id in due_ids:
                        await self.semaphore.acquire()
                        task = asyncio.create_task(self._process_product(product_id))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)

            except Exception:
                logger.exception("Erro crítico no loop principal do Dispatcher")

            await asyncio.sleep(1.0)

        if self._tasks:
            logger.info(
                "Aguardando conclusão de %d tarefas em andamento...",
                len(self._tasks),
            )
            await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Dispatcher encerrado com sucesso.")

    def stop(self) -> None:
        logger.info("Sinal de parada recebido pelo Dispatcher.")
        self._running = False

    async def _process_product(self, product_id: uuid.UUID) -> None:
        try:
            async with get_db_session() as session:
                repo = ProductRepository(session)
                product = await repo.get_by_id(product_id)
                if not product or not product.is_active:
                    return

                logger.info(
                    "Processando produto [%s] | Loja: %s | Prox checagem agendada: %s",
                    str(product.id)[:8],
                    product.store,
                    product.next_check_at.isoformat(),
                )

                # Enquanto a Fase 3 não está acoplada, registra simulação de agendamento (Dry-Run)
                if self._scraper_fn is None:
                    await repo.record_execution_log(
                        product_id=product.id,
                        in_stock=product.in_stock,
                        status="SCHEDULED_DRY_RUN",
                        price_found=product.last_price,
                    )
                    return

                scrape_result = await self._scraper_fn(product)

                await repo.update_product_scrape_state(
                    product_id=product.id,
                    price=scrape_result.price,
                    in_stock=scrape_result.in_stock,
                    original_price=scrape_result.original_price,
                    discount_percentage=scrape_result.discount_percentage,
                    regional_prices=scrape_result.regional_prices,
                )

                should_trigger = False
                if scrape_result.in_stock and scrape_result.price is not None:
                    if product.notify_on_sale_only and scrape_result.is_discounted:
                        should_trigger = True
                    elif (
                        not product.notify_on_sale_only
                        and scrape_result.price <= product.target_price
                    ):
                        should_trigger = True

                status = "SUCCESS"
                if should_trigger:
                    is_digital_store = product.store.lower() in {
                        "eshop",
                        "nintendo",
                        "psn",
                        "playstation",
                    }
                    if product.auto_buy and not is_digital_store:
                        claimed = await repo.claim_for_checkout(product.id)
                        if claimed:
                            status = "CHECKOUT_TRIGGERED"
                            logger.info(
                                "Circuit breaker ativado para auto-checkout do produto %s",
                                product.id,
                            )
                    else:
                        status = "ALERT_TRIGGERED"

                    if self._alert_fn is not None:
                        await self._alert_fn(product, scrape_result)

                await repo.record_execution_log(
                    product_id=product.id,
                    in_stock=scrape_result.in_stock,
                    status=status,
                    price_found=scrape_result.price,
                )

        except Exception as exc:
            logger.exception("Falha ao processar produto %s", product_id)
            try:
                async with get_db_session() as err_session:
                    err_repo = ProductRepository(err_session)
                    await err_repo.record_execution_log(
                        product_id=product_id,
                        in_stock=False,
                        status="ERROR",
                        error_message=str(exc),
                    )
            except Exception:
                logger.exception("Falha ao gravar log de erro para %s", product_id)
        finally:
            self.semaphore.release()