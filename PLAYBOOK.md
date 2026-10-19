# PLAYBOOK.md — Guia de Execução e Engenharia

Este documento detalha o plano de implementação tático derivado do `AGENTS.md`[cite: 1]. Cada fase possui contratos técnicos estritos e critérios de aceite (Definition of Done) que devem ser validados antes do avanço para a etapa seguinte[cite: 1].

---

## Fase 1: Infraestrutura, Configuração e Persistência Assíncrona

### Passo 1.1: Inicialização do Projeto e Gerenciamento de Dependências
1. Inicializar o repositório e o ambiente virtual com `uv`[cite: 1]:

    uv init --no-workspace
    uv python pin 3.12

2. Adicionar dependências de produção e desenvolvimento no `pyproject.toml`[cite: 1]:

    uv add "sqlalchemy[asyncio]>=2.0" asyncpg pydantic-settings alembic playwright curl_cffi "aiogram>=3.0"
    uv add --dev mypy ruff pytest pytest-asyncio

3. Instalar binários do navegador para o Playwright[cite: 1]:

    uv run playwright install chromium

### Passo 1.2: Provisionamento do Banco de Dados (`docker-compose.yml`)
1. Criar o arquivo `docker-compose.yml` na raiz com o serviço `postgres:16-alpine`[cite: 1].
2. Configurar variáveis de ambiente (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`), mapeamento de porta (`5432:5432`), volume nomeado (`pgdata:/var/lib/postgresql/data`) e `healthcheck` via `pg_isready`[cite: 1].

### Passo 1.3: Configuração Tipada (`src/config.py`)
1. Implementar a classe `Settings(BaseSettings)` utilizando `pydantic-settings`[cite: 1].
2. Declarar os atributos obrigatórios:
   - `DATABASE_URL: PostgresDsn` (driver `postgresql+asyncpg://`)[cite: 1]
   - `TELEGRAM_BOT_TOKEN: SecretStr`[cite: 1]
   - `ALLOWED_USER_ID: int`[cite: 1]
   - `MAX_CONCURRENT_TASKS: int = 5`[cite: 1]
   - `PLAYWRIGHT_HEADLESS: bool = True`[cite: 1]
   - `SESSION_STATE_PATH: Path = Path("session.json")`[cite: 1]
   - `ESHOP_COUNTRIES: list[str] = ["BR", "US", "AR", "CO", "ZA"]`
3. Instanciar o singleton `settings = Settings()` com carregamento automático do arquivo `.env`[cite: 1].

### Passo 1.4: Engine Assíncrona e Sessionmaker (`src/database.py`)
1. Configurar `engine = create_async_engine(str(settings.DATABASE_URL), pool_size=10, max_overflow=20, pool_pre_ping=True)`[cite: 1].
2. Configurar `AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)`[cite: 1].
3. Implementar a classe base declarativa `class Base(DeclarativeBase): pass`[cite: 1].
4. Criar o context manager assíncrono `get_db_session() -> AsyncIterator[AsyncSession]` com `rollback()` automático em caso de exceção[cite: 1].

### Passo 1.5: Modelagem de Dados e Migrations (`src/models.py` & `alembic`)
1. Criar o modelo `MonitoredProduct(Base)` mapeando a tabela `monitored_products`[cite: 1]:
   - `id: Mapped[uuid.UUID]` (`primary_key=True`, `default=uuid.uuid4`)[cite: 1]
   - `url: Mapped[str]` (`Text`, `nullable=False`)[cite: 1]
   - `store: Mapped[str]` (`String(50)`, `nullable=False`)[cite: 1]
   - `target_price: Mapped[Decimal]` (`Numeric(10, 2)`, `nullable=False`)[cite: 1]
   - `last_price: Mapped[Decimal | None]` (`Numeric(10, 2)`, `nullable=True`)[cite: 1]
   - `original_price: Mapped[Decimal | None]` (`Numeric(10, 2)`, `nullable=True`)[cite: 2]
   - `discount_percentage: Mapped[int | None]` (`Integer`, `nullable=True`)[cite: 2]
   - `notify_on_sale_only: Mapped[bool]` (`Boolean`, `default=False`, `nullable=False`)[cite: 2]
   - `extra_data: Mapped[dict | None]` (`JSONB`, `nullable=True`)
   - `in_stock: Mapped[bool]` (`Boolean`, `default=False`)[cite: 1]
   - `auto_buy: Mapped[bool]` (`Boolean`, `default=False`)[cite: 1]
   - `check_interval_sec: Mapped[int]` (`Integer`, `nullable=False`, `default=300`)[cite: 1]
   - `next_check_at: Mapped[datetime]` (`DateTime(timezone=True)`, `server_default=func.now()`)[cite: 1]
   - `is_active: Mapped[bool]` (`Boolean`, `default=True`)[cite: 1]
   - `created_at: Mapped[datetime]` (`DateTime(timezone=True)`, `server_default=func.now()`)[cite: 1]
   - **Índice Composto:** `Index("ix_active_next_check", "is_active", "next_check_at")`[cite: 1].
2. Criar o modelo `ExecutionLog(Base)` mapeando `execution_logs`[cite: 1]:
   - `id: Mapped[int]` (`BigInteger`, `primary_key=True`, `autoincrement=True`)[cite: 1]
   - `product_id: Mapped[uuid.UUID]` (`ForeignKey("monitored_products.id", ondelete="CASCADE")`)[cite: 1]
   - `price_found: Mapped[Decimal | None]` (`Numeric(10, 2)`)[cite: 1]
   - `in_stock: Mapped[bool]` (`Boolean`)[cite: 1]
   - `status: Mapped[str]` (`String(30)`) — ex: `SUCCESS`, `ERROR`, `CHECKOUT_TRIGGERED`, `PURCHASED`[cite: 1]
   - `error_message: Mapped[str | None]` (`Text`)[cite: 1]
   - `executed_at: Mapped[datetime]` (`DateTime(timezone=True)`, `server_default=func.now()`)[cite: 1]
3. Inicializar o Alembic em modo assíncrono (`uv run alembic init -t async migrations`), vincular `Base.metadata` no `env.py`, gerar a migration inicial e aplicar no container[cite: 1].

> **Critério de Aceite (Fase 1):** Container PostgreSQL saudável (`healthy`), `uv run alembic upgrade head` executado sem erros, tabelas e índices criados no banco e verificação `uv run mypy src` aprovada com 0 erros[cite: 1].

---

## Fase 2: Motor de Agendamento e Concorrência (Dispatcher)

### Passo 2.1: Repositório Transacional (`src/repositories.py`)
1. Implementar `ProductRepository` recebendo `AsyncSession` na inicialização[cite: 1].
2. Implementar `fetch_due_products(limit: int) -> Sequence[MonitoredProduct]`:
   - Query utilizando `select(MonitoredProduct).where(MonitoredProduct.is_active.is_(True), MonitoredProduct.next_check_at <= func.now()).order_by(MonitoredProduct.next_check_at.asc()).limit(limit).with_for_update(skip_locked=True)`[cite: 1].
3. Implementar o cálculo de reagendamento com jitter estocástico dentro da mesma transação de captura[cite: 1]:
   - `next_check_at = NOW() + (check_interval_sec * random.uniform(0.8, 1.2))`[cite: 1]
4. Implementar métodos CRUD auxiliares: `add_product()`, `list_active_products()`, `update_interval()`, `set_active_status()` e `record_execution_log()`[cite: 1].

### Passo 2.2: Loop do Dispatcher (`src/dispatcher.py`)
1. Implementar a classe `Dispatcher`:
   - Atributos: `semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)`, `_running = False`, `_tasks: set[asyncio.Task[None]] = set()`[cite: 1].
2. Implementar o método `run_forever()`:
   - Loop principal com tick de `1.0` segundo (`await asyncio.sleep(1.0)`)[cite: 1].
   - A cada tick, verificar quantos slots estão livres no semáforo; buscar produtos elegíveis no banco via `fetch_due_products()`[cite: 1].
   - Para cada produto retornado, adquirir o semáforo e disparar `asyncio.create_task(self._process_product(product_id))` mantendo a referência no set `_tasks` (com `task.add_done_callback(self._tasks.discard)`)[cite: 1].
3. Implementar `_process_product(product_id: UUID)`:
   - Envolver toda a execução em bloco `try...except Exception` para garantir isolamento de falhas e liberação do semáforo via `finally`[cite: 1].
   - Avaliar condições de alerta: se `notify_on_sale_only=True` e `is_discounted=True`, ou se `price <= target_price`, despachar notificação[cite: 1, 2].
   - Em caso de produtos de mídia física com `auto_buy=True`, encaminhar para o pipeline de auto-checkout[cite: 1, 2].

> **Critério de Aceite (Fase 2):** Inserção manual de 3 produtos com `check_interval_sec` distintos (ex: 10s, 30s, 60s) demonstra captura concorrente sem colisão (`SKIP LOCKED`), atualização correta de `next_check_at` com variação de +/- 20% e respeito estrito ao limite do `Semaphore`[cite: 1].

---

## Fase 3: Engine de Scraping e Gerenciamento de Sessão

### Passo 3.1: Contratos e Tipagem (`src/scrapers/base.py`)
1. Definir a estrutura de retorno imutável com suporte a descontos e dados multi-região:

    @dataclass(frozen=True, slots=True)
    class ScrapeResult:
        price: Decimal | None
        in_stock: bool
        raw_status: str
        is_discounted: bool = False
        original_price: Decimal | None = None
        discount_percentage: int | None = None
        discount_ends_at: datetime | None = None
        regional_prices: dict[str, dict] | None = None
        timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

2. Definir a classe abstrata `BaseScraper(ABC)`[cite: 1]:
   - Métodos abstratos: `async def check_product(self, url: str) -> ScrapeResult` e `async def execute_checkout(self, url: str, max_price: Decimal) -> bool`[cite: 1].
3. Implementar `ScraperFactory.get_scraper(url: str) -> BaseScraper` analisando o domínio via `urllib.parse.urlparse`[cite: 1].

### Passo 3.2: Gerenciador de Browser Singleton (`src/scrapers/browser.py`)
1. Implementar `BrowserManager` gerenciando o ciclo de vida do `async_playwright()`[cite: 1].
2. Manter uma única instância de `Browser` (Chromium) ativa em memória[cite: 1].
3. Implementar o context manager `new_isolated_context(authenticated: bool = False) -> AsyncIterator[BrowserContext]`:
   - Configurar User-Agent consistente, `viewport` padrão (`1920x1080`), `locale="pt-BR"` e `timezone_id="America/Sao_Paulo"`[cite: 1].
   - Se `authenticated=True` e `settings.SESSION_STATE_PATH.exists()`, carregar `storage_state=str(settings.SESSION_STATE_PATH)`[cite: 1].
   - Bloquear recursos pesados desnecessários no monitoramento (`route` abortando fontes, mídia de vídeo e trackers de analytics)[cite: 1].

### Passo 3.3: Implementação de Extratores Híbridos e APIs Digitais (`src/scrapers/stores/`)
1. **Lojas Digitais (Fast-Path via HTTP Puro com `curl_cffi`):**
   - **Nintendo eShop:**
     - Extrair o `NSUID` numérico da URL do produto[cite: 2].
     - Consultar concorrentemente (`asyncio.gather`) o endpoint de precificação para múltiplos países (`settings.ESHOP_COUNTRIES`): `https://api.ec.nintendo.com/v1/price?country={country}&lang=en&ids={NSUID}`[cite: 2].
     - Parsear `regular_price`, `discount_price` (com `end_datetime`) e calcular percentuais de desconto[cite: 2].
     - Popular o campo `regional_prices` mapeando moeda, preço original e valor promocional de cada país consultado.
     - Definir `execute_checkout()` como não suportado (`NotImplementedError`), restringindo a operação ao modelo Deal Sniping[cite: 2].
   - **PlayStation Store:**
     - Extrair `productId`/`conceptId` da URL[cite: 2].
     - Consultar o endpoint GraphQL público (`productRetrieveForFetch`)[cite: 2].
     - Parsear `basePrice`, `discountedPrice`, percentual de desconto e flag de exclusividade PlayStation Plus[cite: 2].
     - Definir `execute_checkout()` como não suportado[cite: 2].
2. **E-commerces Físicos (Fast-Path via `curl_cffi`):**
   - Tentar extrair preço e estoque via HTTP direto com `impersonate="chrome124"`, parseando JSON-LD (`application/ld+json`) ou hydration state (`__NEXT_DATA__`)[cite: 1].
3. **Fallback / Checkout (`Playwright`):**
   - Usar o `BrowserManager` caso o Fast-Path sofra bloqueio JS ou quando o produto físico entrar na rota de auto-checkout[cite: 1].

> **Critério de Aceite (Fase 3):** Execução isolada dos extratores da Nintendo eShop e PS Store retorna `ScrapeResult` estruturado em < 500ms via HTTP direto, incluindo metadados de promoção e dicionário multi-região na eShop, sem inicializar o Chromium[cite: 2].

---

## Fase 4: Interface de Comando e Notificação (Telegram Bot)

### Passo 4.1: Segurança e Middleware (`src/bot/middlewares.py`)
1. Implementar `AuthMiddleware(BaseMiddleware)` no `aiogram 3.x`[cite: 1].
2. Interceptar todo `Message` e `CallbackQuery`: se `event.from_user.id != settings.ALLOWED_USER_ID`, ignorar silenciosamente o evento e registrar tentativa não autorizada no log[cite: 1].

### Passo 4.2: Comandos Operacionais (`src/bot/handlers.py`)
1. `/add <url> <preco_alvo> <intervalo_seg> [auto_buy=0|1]`: Valida os parâmetros, identifica a loja via `ScraperFactory`, grava no banco e retorna o `UUID` curto do monitoramento[cite: 1]. Aceita `preco_alvo = 0` ou `sale` para ativar `notify_on_sale_only=True`.
2. `/list`: Lista produtos ativos com último preço lido, status de promoção ativa, preço alvo, cadência e flag `auto_buy`[cite: 1].
3. `/interval <id_prefix> <novo_intervalo_seg>`: Atualiza a periodicidade em tempo real e redefine `next_check_at = NOW()`[cite: 1].
4. `/pause <id_prefix>` e `/resume <id_prefix>`: Alterna a flag `is_active` do produto[cite: 1].

### Passo 4.3: Serviço de Notificação (`src/bot/notifier.py`)
1. Implementar `TelegramNotifier` encapsulando a instância `Bot`[cite: 1].
2. Métodos assíncronos para envio de:
   - Alerta padrão de disponibilidade/preço atingido físico (com botão inline de link direto)[cite: 1].
   - Deal Alert para jogos digitais: título, preço original, preço promocional, porcentagem de desconto, data limite e tabela comparativa de preços por região/país (para eShop).
   - Código PIX Copia-e-Cola formatado em bloco monoespaçado (`<code>...</code>`) + QR Code ou confirmação de compra via cartão[cite: 1].
   - Envio de screenshot de auditoria (`BufferedInputFile`) em caso de tentativa de auto-checkout físico[cite: 1].

> **Critério de Aceite (Fase 4):** Comandos enviados pelo Telegram refletem imediatamente na tabela `monitored_products` do PostgreSQL; endpoints de lojas digitais formatam alertas detalhados de promoção com botão inline e comparação regional[cite: 1].

---

## Fase 5: Módulo Auto-Checkout, Circuit Breaker e Orquestração

### Passo 5.1: Trava de Segurança / Circuit Breaker (`src/dispatcher.py`)
1. Quando `result.in_stock is True` e (`result.price <= product.target_price` ou promoção detectada):
   - Se `product.auto_buy is False` ou se for loja digital: enviar notificação de alerta e manter monitoramento (com cooldown para evitar notificações duplicadas)[cite: 1, 2].
   - Se `product.auto_buy is True` (lojas físicas suportadas): executar imediatamente `UPDATE monitored_products SET is_active = FALSE WHERE id = :id AND is_active = TRUE` **antes** de iniciar o fluxo de compra[cite: 1, 2].
2. Se o `rowcount` do `UPDATE` for `0`, abortar a compra (outro worker já assumiu a execução)[cite: 1].

### Passo 5.2: Máquina de Estados de Compra (`execute_checkout`)
1. Abrir contexto autenticado no Playwright (`authenticated=True`)[cite: 1].
2. Navegar até o produto, adicionar ao carrinho e avançar para a tela final de checkout[cite: 1].
3. **Validação de Segurança Obrigatória:** Extrair o valor total final (produto + frete) na tela de pagamento[cite: 1]. Se `Total > target_price + frete_maximo`, abortar a compra, tirar screenshot do erro, notificar no Telegram e manter `is_active = False` para revisão humana[cite: 1].
4. Confirmar o pedido (Gerar PIX ou submeter CVV do cartão salvo), capturar screenshot do comprovante/tela final, extrair o código PIX (se aplicável) e enviar via `TelegramNotifier`[cite: 1].

### Passo 5.3: Utilitário de Autenticação (`scripts/save_session.py`)
1. Script CLI independente que abre o Chromium em modo `headless=False`, permite ao usuário fazer login manual na loja física (resolvendo 2FA/Captcha) e exporta os cookies e `localStorage` via `context.storage_state(path="session.json")`[cite: 1].

### Passo 5.4: Entrypoint Unificado e Graceful Shutdown (`src/main.py`)
1. Orquestrar os serviços utilizando `asyncio.TaskGroup`[cite: 1]:

    async with asyncio.TaskGroup() as tg:
        tg.create_task(dispatcher.run_forever())
        tg.create_task(bot_dp.start_polling(bot, handle_signals=False))

2. Capturar sinais `SIGINT` e `SIGTERM` via `asyncio.get_running_loop().add_signal_handler` para interromper o loop do `Dispatcher`, aguardar a conclusão de checkouts em andamento, fechar o `BrowserManager` e encerrar o pool do SQLAlchemy limpa e seguramente[cite: 1].

> **Critério de Aceite (Fase 5):** Simulação de ponta a ponta (dry-run de checkout) desativa imediatamente o produto no banco (`is_active = False`), valida o teto de preço no carrinho, envia o screenshot de auditoria no Telegram e encerra todos os serviços sem erros ao receber `CTRL+C`[cite: 1].