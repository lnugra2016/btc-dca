"""
config.py — Parámetros globales, API keys y configuración del sistema.
Centraliza toda la configuración para facilitar ajustes sin tocar lógica de negocio.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# API KEYS — reemplazar con variables de entorno en producción
# ---------------------------------------------------------------------------
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "YOUR_API_KEY_HERE")
POLYMARKET_API_SECRET: str = os.getenv("POLYMARKET_API_SECRET", "YOUR_API_SECRET_HERE")
POLYMARKET_API_PASSPHRASE: str = os.getenv("POLYMARKET_API_PASSPHRASE", "YOUR_PASSPHRASE_HERE")

# Wallet privada para firma de transacciones (Polygon)
WALLET_PRIVATE_KEY: str = os.getenv("WALLET_PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")
WALLET_ADDRESS: str = os.getenv("WALLET_ADDRESS", "YOUR_WALLET_ADDRESS_HERE")

# CoinGecko / Binance para precio spot BTC (sin auth requerida)
BINANCE_BASE_URL: str = "https://api.binance.com"
COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"

# Polymarket CLOB API
POLYMARKET_CLOB_URL: str = "https://clob.polymarket.com"
POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"


# ---------------------------------------------------------------------------
# MARKET CONFIGURATION
# ---------------------------------------------------------------------------
@dataclass
class MarketConfig:
    # Token ID del mercado BTC Arriba/Abajo (5 min) — reemplazar con ID real
    btc_5min_market_id: str = os.getenv("BTC_MARKET_ID", "YOUR_MARKET_TOKEN_ID")

    # Horizon en segundos
    trade_horizon_seconds: int = 300  # 5 minutos

    # Intervalo de evaluación del loop principal
    eval_interval_min: int = 10   # segundos
    eval_interval_max: int = 20   # segundos (aleatorizado para evitar front-running)


# ---------------------------------------------------------------------------
# RISK PARAMETERS
# ---------------------------------------------------------------------------
@dataclass
class RiskConfig:
    # Capital inicial
    initial_equity: float = 100.0

    # Riesgo por trade como fracción del equity
    risk_per_trade: float = 0.0075  # 0.75%

    # Máximo trades activos simultáneos
    max_active_trades: int = 1

    # Take Profit y Stop Loss como fracción del precio de entrada
    take_profit_pct: float = 0.02   # +2%
    stop_loss_pct: float = 0.01     # -1%

    # Drawdown máximo antes de activar kill-switch (% del equity inicial)
    max_drawdown_pct: float = 0.10  # -10%

    # Pérdidas consecutivas máximas antes de pausa
    max_consecutive_losses: int = 3

    # Pausa en segundos tras alcanzar max_consecutive_losses
    pause_after_losses_seconds: int = 1800  # 30 minutos


# ---------------------------------------------------------------------------
# TRADING THRESHOLDS (filtros de entrada)
# ---------------------------------------------------------------------------
@dataclass
class ThresholdConfig:
    # Spread máximo permitido del orderbook (fracción del mid-price)
    max_spread_pct: float = 0.012   # 1.2%

    # Imbalance mínimo del orderbook (bid_vol / total_vol o ask_vol / total_vol)
    min_imbalance_pct: float = 0.12  # 12%

    # Edge mínimo requerido para operar
    min_edge_pct: float = 0.02      # 2%

    # Rango de volatilidad aceptable (σ diaria normalizada)
    vol_regime_min: float = 0.01    # 1% — evita mercado muerto
    vol_regime_max: float = 0.04    # 4% — evita mercado caótico

    # Momentum mínimo para confirmar dirección
    min_momentum_threshold: float = 0.001  # 0.1% de cambio de precio


# ---------------------------------------------------------------------------
# EXECUTION SETTINGS
# ---------------------------------------------------------------------------
@dataclass
class ExecutionConfig:
    # Slippage máximo aceptado (fracción del precio)
    max_slippage_pct: float = 0.005  # 0.5%

    # Timeout para fill de orden (segundos)
    order_fill_timeout: int = 30

    # Timeout total de la posición (segundos) — cierre forzado
    position_timeout: int = 300  # 5 minutos

    # Intervalo de monitoreo de posición abierta (segundos)
    monitor_interval: int = 5

    # Reintentos máximos en fallos de API
    max_api_retries: int = 3
    retry_backoff_base: float = 2.0  # segundos base para backoff exponencial


# ---------------------------------------------------------------------------
# KILL-SWITCH SETTINGS
# ---------------------------------------------------------------------------
@dataclass
class KillSwitchConfig:
    # Archivo flag: si existe, el bot se detiene inmediatamente
    flag_file_path: str = "/tmp/bot_kill_switch.flag"

    # Activación automática por drawdown
    auto_trigger_on_drawdown: bool = True

    # Activación automática por pérdidas consecutivas
    auto_trigger_on_consecutive_losses: bool = True


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_file: str = "bot/logs/trading_bot.log"
    max_bytes: int = 10_485_760   # 10 MB
    backup_count: int = 5
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# BACKTESTER SETTINGS
# ---------------------------------------------------------------------------
@dataclass
class BacktesterConfig:
    num_simulations: int = 300
    monte_carlo_runs: int = 1000
    initial_equity: float = 100.0
    risk_per_trade: float = 0.0075
    take_profit_pct: float = 0.02
    stop_loss_pct: float = 0.01
    # Estimación base de win-rate para Monte Carlo (se calibra con datos reales)
    assumed_win_rate: float = 0.52


# ---------------------------------------------------------------------------
# GLOBAL CONFIG INSTANCE (singleton de configuración)
# ---------------------------------------------------------------------------
@dataclass
class BotConfig:
    market: MarketConfig = field(default_factory=MarketConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    kill_switch: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    backtester: BacktesterConfig = field(default_factory=BacktesterConfig)

    # Acceso directo a constantes de API (delegadas a nivel de módulo)
    polymarket_api_key: str = field(default_factory=lambda: POLYMARKET_API_KEY)
    polymarket_api_secret: str = field(default_factory=lambda: POLYMARKET_API_SECRET)
    polymarket_api_passphrase: str = field(default_factory=lambda: POLYMARKET_API_PASSPHRASE)
    wallet_private_key: str = field(default_factory=lambda: WALLET_PRIVATE_KEY)
    wallet_address: str = field(default_factory=lambda: WALLET_ADDRESS)
    polymarket_clob_url: str = field(default_factory=lambda: POLYMARKET_CLOB_URL)
    polymarket_gamma_url: str = field(default_factory=lambda: POLYMARKET_GAMMA_URL)
    binance_base_url: str = field(default_factory=lambda: BINANCE_BASE_URL)
    coingecko_base_url: str = field(default_factory=lambda: COINGECKO_BASE_URL)


# Instancia global importable por todos los módulos
CONFIG = BotConfig()
