"""
data_feed.py — Capa de datos: orderbook, precios spot BTC, momentum y régimen de volatilidad.
Abstrae todas las fuentes externas con manejo de errores, timeouts y reconexión automática.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class Orderbook:
    bids: list[tuple[float, float]]   # [(price, size), ...]
    asks: list[tuple[float, float]]   # [(price, size), ...]
    timestamp: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        """Spread relativo al mid price."""
        if self.best_bid and self.best_ask and self.mid_price:
            return (self.best_ask - self.best_bid) / self.mid_price
        return None

    @property
    def bid_volume(self) -> float:
        return sum(size for _, size in self.bids)

    @property
    def ask_volume(self) -> float:
        return sum(size for _, size in self.asks)

    @property
    def total_volume(self) -> float:
        return self.bid_volume + self.ask_volume

    @property
    def imbalance(self) -> Optional[float]:
        """
        Imbalance = (bid_vol - ask_vol) / total_vol.
        Positivo → presión compradora. Negativo → presión vendedora.
        """
        if self.total_volume == 0:
            return None
        return (self.bid_volume - self.ask_volume) / self.total_volume


@dataclass
class SpotData:
    price: float
    timestamp: float
    source: str = "binance"


@dataclass
class MomentumResult:
    value: float          # Cambio porcentual normalizado
    direction: int        # +1 alcista, -1 bajista, 0 neutral
    lookback_seconds: int


@dataclass
class VolatilityRegime:
    sigma: float          # Desviación estándar de retornos en ventana reciente
    regime: str           # "low" | "medium" | "high"
    is_tradeable: bool    # Dentro del rango aceptable definido en config


# ---------------------------------------------------------------------------
# HTTP SESSION CON RETRY AUTOMÁTICO
# ---------------------------------------------------------------------------

def _build_session(retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """Crea una sesión HTTP con retry exponencial y timeout por defecto."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _build_session(
    retries=CONFIG.execution.max_api_retries,
    backoff=CONFIG.execution.retry_backoff_base,
)

# Buffer circular para cálculo de momentum y volatilidad
_PRICE_BUFFER: deque[tuple[float, float]] = deque(maxlen=120)  # (timestamp, price)
_SPOT_BUFFER: deque[tuple[float, float]] = deque(maxlen=120)


# ---------------------------------------------------------------------------
# POLYMARKET ORDERBOOK
# ---------------------------------------------------------------------------

def get_polymarket_orderbook(
    token_id: Optional[str] = None,
    timeout: int = 5,
) -> Optional[Orderbook]:
    """
    Obtiene el orderbook del mercado BTC en Polymarket CLOB.

    Args:
        token_id: ID del token (YES o NO). Si es None, usa el de config.
        timeout: Timeout HTTP en segundos.

    Returns:
        Orderbook poblado, o None si falla.
    """
    market_id = token_id or CONFIG.market.btc_5min_market_id
    url = f"{CONFIG.polymarket_clob_url}/book"

    try:
        resp = _SESSION.get(
            url,
            params={"token_id": market_id},
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

        bids = [
            (float(entry["price"]), float(entry["size"]))
            for entry in data.get("bids", [])
        ]
        asks = [
            (float(entry["price"]), float(entry["size"]))
            for entry in data.get("asks", [])
        ]

        # Ordenar: bids descendente, asks ascendente
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        ob = Orderbook(bids=bids, asks=asks, timestamp=time.time())
        logger.debug(
            "Orderbook | bid=%.4f ask=%.4f spread=%.4f%% imbalance=%.4f",
            ob.best_bid or 0,
            ob.best_ask or 0,
            (ob.spread or 0) * 100,
            ob.imbalance or 0,
        )
        return ob

    except requests.exceptions.Timeout:
        logger.warning("Timeout obteniendo orderbook de Polymarket")
    except requests.exceptions.ConnectionError as exc:
        logger.error("Error de conexión con Polymarket CLOB: %s", exc)
    except (ValueError, KeyError) as exc:
        logger.error("Error parseando orderbook: %s", exc)

    return None


# ---------------------------------------------------------------------------
# POLYMARKET LAST PRICE
# ---------------------------------------------------------------------------

def get_polymarket_last_price(
    token_id: Optional[str] = None,
    timeout: int = 5,
) -> Optional[float]:
    """
    Obtiene el último precio de transacción del mercado en Polymarket.

    Returns:
        Precio como fracción (0.0 – 1.0), o None si falla.
    """
    market_id = token_id or CONFIG.market.btc_5min_market_id
    url = f"{CONFIG.polymarket_clob_url}/last-trade-price"

    try:
        resp = _SESSION.get(
            url,
            params={"token_id": market_id},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        price = float(data.get("price", 0))

        now = time.time()
        _PRICE_BUFFER.append((now, price))
        logger.debug("Polymarket last price: %.4f", price)
        return price

    except requests.exceptions.Timeout:
        logger.warning("Timeout obteniendo último precio de Polymarket")
    except requests.exceptions.ConnectionError as exc:
        logger.error("Error de conexión con Polymarket: %s", exc)
    except (ValueError, KeyError) as exc:
        logger.error("Error parseando último precio: %s", exc)

    return None


# ---------------------------------------------------------------------------
# BTC SPOT PRICE (Binance — sin autenticación)
# ---------------------------------------------------------------------------

def get_btc_spot_price(timeout: int = 5) -> Optional[float]:
    """
    Obtiene el precio spot BTC/USDT desde Binance.

    Returns:
        Precio en USD, o None si falla. Fallback a CoinGecko si Binance falla.
    """
    price = _fetch_binance_btc(timeout)
    if price is None:
        logger.warning("Binance falló, intentando CoinGecko como fallback")
        price = _fetch_coingecko_btc(timeout)

    if price is not None:
        _SPOT_BUFFER.append((time.time(), price))
        logger.debug("BTC spot price: $%.2f", price)

    return price


def _fetch_binance_btc(timeout: int) -> Optional[float]:
    url = f"{CONFIG.binance_base_url}/api/v3/ticker/price"
    try:
        resp = _SESSION.get(url, params={"symbol": "BTCUSDT"}, timeout=timeout)
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as exc:
        logger.debug("Binance error: %s", exc)
        return None


def _fetch_coingecko_btc(timeout: int) -> Optional[float]:
    url = f"{CONFIG.coingecko_base_url}/simple/price"
    try:
        resp = _SESSION.get(
            url,
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return float(resp.json()["bitcoin"]["usd"])
    except Exception as exc:
        logger.debug("CoinGecko error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# MOMENTUM (basado en buffer de precios spot)
# ---------------------------------------------------------------------------

def calculate_spot_momentum(lookback_seconds: int = 60) -> Optional[MomentumResult]:
    """
    Calcula el momentum del precio spot BTC en una ventana temporal.

    Momentum = (precio_actual - precio_hace_N_seg) / precio_hace_N_seg

    Args:
        lookback_seconds: Ventana de tiempo en segundos.

    Returns:
        MomentumResult o None si no hay suficientes datos.
    """
    if len(_SPOT_BUFFER) < 2:
        logger.debug("Buffer de precios insuficiente para momentum")
        return None

    now = time.time()
    cutoff = now - lookback_seconds

    # Precio más antiguo dentro de la ventana
    past_prices = [(ts, p) for ts, p in _SPOT_BUFFER if ts >= cutoff]
    if not past_prices:
        logger.debug("Sin datos en ventana de momentum de %ds", lookback_seconds)
        return None

    oldest_ts, oldest_price = past_prices[0]
    current_price = _SPOT_BUFFER[-1][1]

    if oldest_price == 0:
        return None

    momentum_value = (current_price - oldest_price) / oldest_price
    threshold = CONFIG.thresholds.min_momentum_threshold

    if momentum_value > threshold:
        direction = 1
    elif momentum_value < -threshold:
        direction = -1
    else:
        direction = 0

    logger.debug(
        "Momentum(%.0fs): %.5f | dirección: %+d",
        lookback_seconds, momentum_value, direction,
    )
    return MomentumResult(
        value=momentum_value,
        direction=direction,
        lookback_seconds=lookback_seconds,
    )


# ---------------------------------------------------------------------------
# VOLATILITY REGIME
# ---------------------------------------------------------------------------

def calculate_volatility_regime(lookback_seconds: int = 300) -> Optional[VolatilityRegime]:
    """
    Calcula el régimen de volatilidad a partir de retornos logarítmicos del spot.

    Clasifica en:
      - "low": σ < vol_regime_min  → mercado muerto, sin edge
      - "medium": vol_regime_min ≤ σ ≤ vol_regime_max → tradeable
      - "high": σ > vol_regime_max → mercado caótico, riesgo excesivo

    Args:
        lookback_seconds: Ventana en segundos para calcular σ.

    Returns:
        VolatilityRegime o None si no hay suficientes datos.
    """
    import math

    if len(_SPOT_BUFFER) < 3:
        logger.debug("Buffer insuficiente para cálculo de volatilidad")
        return None

    now = time.time()
    cutoff = now - lookback_seconds
    window_prices = [p for ts, p in _SPOT_BUFFER if ts >= cutoff]

    if len(window_prices) < 3:
        logger.debug("Menos de 3 muestras en ventana de volatilidad")
        return None

    # Retornos logarítmicos
    log_returns = [
        math.log(window_prices[i] / window_prices[i - 1])
        for i in range(1, len(window_prices))
        if window_prices[i - 1] > 0
    ]

    if not log_returns:
        return None

    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / max(n - 1, 1)
    sigma = math.sqrt(variance)

    vol_min = CONFIG.thresholds.vol_regime_min
    vol_max = CONFIG.thresholds.vol_regime_max

    if sigma < vol_min:
        regime = "low"
        tradeable = False
    elif sigma > vol_max:
        regime = "high"
        tradeable = False
    else:
        regime = "medium"
        tradeable = True

    logger.debug(
        "Volatilidad: σ=%.5f | régimen=%s | tradeable=%s",
        sigma, regime, tradeable,
    )
    return VolatilityRegime(sigma=sigma, regime=regime, is_tradeable=tradeable)
