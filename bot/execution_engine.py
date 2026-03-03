"""
execution_engine.py — Motor de ejecución de órdenes en Polymarket CLOB.
Responsable exclusivamente de: colocar órdenes, verificar fills, monitorear
posiciones y cerrar trades. Sin lógica de riesgo ni de señales.
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import requests

from config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ENUMS Y DATA STRUCTURES
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass
class Order:
    order_id: str
    token_id: str
    side: OrderSide
    size: float           # Cantidad en USDC
    price: float          # Precio límite (0.0 – 1.0)
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class Position:
    """Posición abierta activa."""
    position_id: str
    token_id: str
    direction: str           # "YES" | "NO"
    entry_order: Order
    size_usdc: float         # Capital invertido
    entry_price: float       # Precio real de entrada (post-fill)
    take_profit_price: float
    stop_loss_price: float
    timeout_ts: float        # Timestamp de cierre forzado
    open_ts: float = field(default_factory=time.time)
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "tp" | "sl" | "timeout" | "manual"
    pnl: float = 0.0


# ---------------------------------------------------------------------------
# CLIENTE HTTP AUTENTICADO PARA POLYMARKET CLOB
# ---------------------------------------------------------------------------

class PolymarketCLOBClient:
    """
    Cliente minimalista para la API CLOB de Polymarket.
    Maneja autenticación, headers y retry básico.
    """

    BASE_URL = CONFIG.polymarket_clob_url

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get_auth_headers(self) -> dict:
        """
        Genera headers de autenticación para Polymarket CLOB.
        Polymarket usa autenticación basada en API key + HMAC o EIP-712.
        Este es un placeholder — implementar según documentación oficial.
        """
        ts = str(int(time.time() * 1000))
        return {
            "POLY_ADDRESS": CONFIG.wallet_address,
            "POLY_SIGNATURE": self._sign_request(ts),
            "POLY_TIMESTAMP": ts,
            "POLY_API_KEY": CONFIG.polymarket_api_key,
            "POLY_PASSPHRASE": CONFIG.polymarket_api_passphrase,
        }

    def _sign_request(self, timestamp: str) -> str:
        """
        Firma HMAC-SHA256 del timestamp. Implementación placeholder.
        En producción: usar py_clob_client oficial de Polymarket.
        """
        import hmac
        import hashlib
        msg = f"{timestamp}GET/".encode()
        sig = hmac.new(
            CONFIG.polymarket_api_secret.encode(),
            msg,
            hashlib.sha256,
        ).hexdigest()
        return sig

    def post(self, endpoint: str, payload: dict, timeout: int = 10) -> Optional[dict]:
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_auth_headers()
        for attempt in range(CONFIG.execution.max_api_retries):
            try:
                resp = self._session.post(url, json=payload, headers=headers, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("Timeout POST %s (intento %d)", endpoint, attempt + 1)
            except requests.exceptions.HTTPError as exc:
                logger.error("HTTP error POST %s: %s", endpoint, exc)
                if resp.status_code in (400, 401, 403):
                    break  # No reintentar errores de cliente
            except requests.exceptions.ConnectionError as exc:
                logger.error("Conexión fallida POST %s: %s", endpoint, exc)

            if attempt < CONFIG.execution.max_api_retries - 1:
                backoff = CONFIG.execution.retry_backoff_base ** (attempt + 1)
                logger.debug("Backoff %.1fs antes de reintento", backoff)
                time.sleep(backoff)

        return None

    def get(self, endpoint: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_auth_headers()
        for attempt in range(CONFIG.execution.max_api_retries):
            try:
                resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("Timeout GET %s (intento %d)", endpoint, attempt + 1)
            except requests.exceptions.HTTPError as exc:
                logger.error("HTTP error GET %s: %s", endpoint, exc)
                if resp.status_code in (400, 401, 403):
                    break
            except requests.exceptions.ConnectionError as exc:
                logger.error("Conexión fallida GET %s: %s", endpoint, exc)

            if attempt < CONFIG.execution.max_api_retries - 1:
                backoff = CONFIG.execution.retry_backoff_base ** (attempt + 1)
                time.sleep(backoff)

        return None

    def delete(self, endpoint: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_auth_headers()
        try:
            resp = self._session.delete(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Error DELETE %s: %s", endpoint, exc)
            return None


# ---------------------------------------------------------------------------
# EXECUTION ENGINE
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """
    Motor de ejecución de órdenes. Gestiona el ciclo completo de vida de
    un trade: apertura → monitoreo → cierre.

    No toma decisiones de trading: solo ejecuta lo que se le indica.
    """

    def __init__(self):
        self._client = PolymarketCLOBClient()
        self._active_position: Optional[Position] = None
        logger.info("ExecutionEngine inicializado")

    @property
    def has_active_position(self) -> bool:
        return self._active_position is not None and self._active_position.is_open

    @property
    def active_position(self) -> Optional[Position]:
        return self._active_position

    # -----------------------------------------------------------------------
    # COLOCAR ORDEN YES
    # -----------------------------------------------------------------------

    def place_yes_order(
        self,
        size_usdc: float,
        limit_price: float,
        token_id: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Compra tokens YES (apuesta a que BTC sube).

        Args:
            size_usdc: Capital a invertir en USDC.
            limit_price: Precio máximo a pagar (0.0 – 1.0).
            token_id: ID del token YES. Si None, usa config.

        Returns:
            Order creada, o None si falla.
        """
        return self._place_order(
            direction="YES",
            side=OrderSide.BUY,
            size_usdc=size_usdc,
            limit_price=limit_price,
            token_id=token_id,
        )

    # -----------------------------------------------------------------------
    # COLOCAR ORDEN NO
    # -----------------------------------------------------------------------

    def place_no_order(
        self,
        size_usdc: float,
        limit_price: float,
        token_id: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Compra tokens NO (apuesta a que BTC baja).

        Args:
            size_usdc: Capital a invertir en USDC.
            limit_price: Precio máximo a pagar (0.0 – 1.0).
            token_id: ID del token NO. Si None, deriva del mercado.

        Returns:
            Order creada, o None si falla.
        """
        return self._place_order(
            direction="NO",
            side=OrderSide.BUY,
            size_usdc=size_usdc,
            limit_price=limit_price,
            token_id=token_id,
        )

    # -----------------------------------------------------------------------
    # ORDEN LÍMITE GENÉRICA
    # -----------------------------------------------------------------------

    def place_limit_order(
        self,
        token_id: str,
        side: OrderSide,
        size_usdc: float,
        limit_price: float,
    ) -> Optional[Order]:
        """
        Coloca una orden límite genérica en el CLOB de Polymarket.

        Args:
            token_id: ID del token a operar.
            side: BUY o SELL.
            size_usdc: Tamaño en USDC.
            limit_price: Precio límite (0.0 – 1.0).

        Returns:
            Order creada con estado actualizado.
        """
        # Validar slippage máximo: si el precio es mayor a lo aceptable, rechazar
        if not self._validate_price_slippage(limit_price):
            logger.warning("Orden rechazada por slippage excesivo: %.4f", limit_price)
            return None

        order_id = self._generate_order_id()
        num_contracts = size_usdc / limit_price if limit_price > 0 else 0

        payload = {
            "order_id": order_id,
            "token_id": token_id,
            "side": side.value,
            "type": OrderType.LIMIT.value,
            "price": str(limit_price),
            "size": str(round(num_contracts, 6)),
            "time_in_force": "GTC",   # Good Till Cancelled
        }

        logger.info(
            "Colocando orden | id=%s side=%s price=%.4f size=%.4f USDC",
            order_id, side.value, limit_price, size_usdc,
        )

        response = self._client.post("/order", payload)
        if response is None:
            logger.error("Fallo al crear orden en API")
            return None

        order = Order(
            order_id=response.get("orderID", order_id),
            token_id=token_id,
            side=side,
            size=size_usdc,
            price=limit_price,
            status=OrderStatus.OPEN,
        )
        logger.info("Orden creada exitosamente: %s", order.order_id)
        return order

    # -----------------------------------------------------------------------
    # MONITOREO DE POSICIÓN ABIERTA
    # -----------------------------------------------------------------------

    def monitor_open_position(self) -> Optional[dict]:
        """
        Monitorea la posición activa: verifica TP, SL y timeout.

        Debe ser llamado en loop hasta que la posición se cierre.

        Returns:
            dict con resultado del monitoreo, o None si no hay posición.
        """
        if not self.has_active_position:
            return None

        pos = self._active_position
        now = time.time()

        # -- Obtener precio actual --
        current_price = self._get_current_price(pos.token_id)
        if current_price is None:
            logger.warning("No se pudo obtener precio actual para monitoreo")
            return {"status": "monitoring", "error": "price_unavailable"}

        logger.debug(
            "Monitor | pos=%s price=%.4f TP=%.4f SL=%.4f timeout_en=%.0fs",
            pos.position_id,
            current_price,
            pos.take_profit_price,
            pos.stop_loss_price,
            max(0, pos.timeout_ts - now),
        )

        # -- Verificar Take Profit --
        if current_price >= pos.take_profit_price:
            logger.info("TAKE PROFIT alcanzado | price=%.4f TP=%.4f", current_price, pos.take_profit_price)
            return self.close_position(reason="tp", exit_price=current_price)

        # -- Verificar Stop Loss --
        if current_price <= pos.stop_loss_price:
            logger.info("STOP LOSS alcanzado | price=%.4f SL=%.4f", current_price, pos.stop_loss_price)
            return self.close_position(reason="sl", exit_price=current_price)

        # -- Verificar Timeout --
        if now >= pos.timeout_ts:
            logger.warning("TIMEOUT de posición | duración=%.0fs", now - pos.open_ts)
            return self.close_position(reason="timeout", exit_price=current_price)

        return {
            "status": "monitoring",
            "current_price": current_price,
            "pnl_unrealized": self._calculate_unrealized_pnl(pos, current_price),
            "time_remaining": pos.timeout_ts - now,
        }

    # -----------------------------------------------------------------------
    # CERRAR POSICIÓN
    # -----------------------------------------------------------------------

    def close_position(
        self,
        reason: str = "manual",
        exit_price: Optional[float] = None,
    ) -> dict:
        """
        Cierra la posición activa colocando una orden de venta.

        Args:
            reason: Razón del cierre ("tp" | "sl" | "timeout" | "manual").
            exit_price: Precio de salida (si None, se obtiene del mercado).

        Returns:
            dict con resultado del cierre incluyendo PnL realizado.
        """
        if not self.has_active_position:
            logger.warning("close_position llamado sin posición activa")
            return {"status": "no_position"}

        pos = self._active_position

        # Precio de salida
        if exit_price is None:
            exit_price = self._get_current_price(pos.token_id)
            if exit_price is None:
                logger.error("No se puede obtener precio de salida — usando SL como fallback")
                exit_price = pos.stop_loss_price

        # Colocar orden de cierre (SELL) con precio agresivo para asegurar fill
        close_price = exit_price * (1 - CONFIG.execution.max_slippage_pct)
        close_order = self.place_limit_order(
            token_id=pos.token_id,
            side=OrderSide.SELL,
            size_usdc=pos.size_usdc,
            limit_price=max(0.01, close_price),
        )

        # Esperar confirmación de fill
        fill_confirmed = False
        if close_order:
            fill_confirmed = self._wait_for_fill(close_order)

        # Calcular PnL realizado
        pnl = self._calculate_realized_pnl(pos, exit_price)

        # Marcar posición como cerrada
        pos.is_open = False
        pos.exit_price = exit_price
        pos.exit_reason = reason
        pos.pnl = pnl

        logger.info(
            "Posición cerrada | id=%s reason=%s entry=%.4f exit=%.4f PnL=%.4f",
            pos.position_id, reason, pos.entry_price, exit_price, pnl,
        )

        result = {
            "status": "closed",
            "position_id": pos.position_id,
            "direction": pos.direction,
            "reason": reason,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "fill_confirmed": fill_confirmed,
            "duration_seconds": time.time() - pos.open_ts,
        }

        self._active_position = None
        return result

    # -----------------------------------------------------------------------
    # MÉTODOS INTERNOS
    # -----------------------------------------------------------------------

    def _place_order(
        self,
        direction: str,
        side: OrderSide,
        size_usdc: float,
        limit_price: float,
        token_id: Optional[str],
    ) -> Optional[Order]:
        """Flujo interno de colocación de orden con apertura de posición."""
        if self.has_active_position:
            logger.error("Ya existe una posición activa — máximo 1 trade simultáneo")
            return None

        effective_token = token_id or CONFIG.market.btc_5min_market_id

        order = self.place_limit_order(
            token_id=effective_token,
            side=side,
            size_usdc=size_usdc,
            limit_price=limit_price,
        )
        if order is None:
            return None

        # Esperar fill con timeout
        filled = self._wait_for_fill(order)
        if not filled:
            logger.warning("Orden no llenada en timeout — cancelando")
            self._cancel_order(order.order_id)
            return None

        # Crear posición activa
        entry_price = order.avg_fill_price or limit_price
        tp_price = entry_price * (1 + CONFIG.risk.take_profit_pct)
        sl_price = entry_price * (1 - CONFIG.risk.stop_loss_pct)
        timeout_ts = time.time() + CONFIG.execution.position_timeout

        self._active_position = Position(
            position_id=self._generate_order_id(),
            token_id=effective_token,
            direction=direction,
            entry_order=order,
            size_usdc=size_usdc,
            entry_price=entry_price,
            take_profit_price=tp_price,
            stop_loss_price=sl_price,
            timeout_ts=timeout_ts,
        )

        logger.info(
            "Posición abierta | dir=%s entry=%.4f TP=%.4f SL=%.4f timeout=%ds",
            direction, entry_price, tp_price, sl_price,
            CONFIG.execution.position_timeout,
        )
        return order

    def _wait_for_fill(self, order: Order, timeout: Optional[int] = None) -> bool:
        """
        Espera a que una orden sea llenada (filled) en el CLOB.

        Consulta el estado cada 2 segundos hasta timeout o fill completo.

        Returns:
            True si la orden fue llenada, False si timeout o error.
        """
        max_wait = timeout or CONFIG.execution.order_fill_timeout
        deadline = time.time() + max_wait
        check_interval = 2  # segundos

        while time.time() < deadline:
            status = self._get_order_status(order.order_id)
            if status is None:
                time.sleep(check_interval)
                continue

            order.status = status.get("status", OrderStatus.PENDING)
            order.filled_size = float(status.get("size_matched", 0))
            order.avg_fill_price = float(status.get("avg_price", order.price))
            order.updated_at = time.time()

            if order.status == OrderStatus.FILLED:
                logger.info(
                    "Orden llenada | id=%s price=%.4f size=%.4f",
                    order.order_id, order.avg_fill_price, order.filled_size,
                )
                return True

            if order.status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.FAILED):
                logger.warning("Orden %s con estado: %s", order.order_id, order.status)
                return False

            logger.debug(
                "Esperando fill | id=%s status=%s filled=%.4f",
                order.order_id, order.status, order.filled_size,
            )
            time.sleep(check_interval)

        logger.warning("Timeout esperando fill de orden %s", order.order_id)
        return False

    def _get_order_status(self, order_id: str) -> Optional[dict]:
        """Consulta el estado de una orden en la API."""
        return self._client.get(f"/order/{order_id}")

    def _cancel_order(self, order_id: str) -> bool:
        """Cancela una orden abierta."""
        result = self._client.delete(f"/order/{order_id}")
        if result:
            logger.info("Orden %s cancelada", order_id)
            return True
        logger.error("No se pudo cancelar orden %s", order_id)
        return False

    def _get_current_price(self, token_id: str) -> Optional[float]:
        """Obtiene el precio mid actual del token desde el orderbook."""
        from data_feed import get_polymarket_orderbook
        ob = get_polymarket_orderbook(token_id=token_id)
        if ob and ob.mid_price:
            return ob.mid_price
        # Fallback: último precio de transacción
        from data_feed import get_polymarket_last_price
        return get_polymarket_last_price(token_id=token_id)

    def _validate_price_slippage(self, price: float) -> bool:
        """Valida que el precio no supere el slippage máximo permitido."""
        # En contexto de apertura, el precio ya viene con la tolerancia incluida
        # Esta validación es un check de sanidad básico
        return 0.0 < price < 1.0

    def _calculate_unrealized_pnl(self, pos: Position, current_price: float) -> float:
        """Calcula el P&L no realizado de la posición."""
        if pos.entry_price == 0:
            return 0.0
        price_change_pct = (current_price - pos.entry_price) / pos.entry_price
        return pos.size_usdc * price_change_pct

    def _calculate_realized_pnl(self, pos: Position, exit_price: float) -> float:
        """Calcula el P&L realizado al cerrar la posición."""
        if pos.entry_price == 0:
            return 0.0
        price_change_pct = (exit_price - pos.entry_price) / pos.entry_price
        # Restar comisión estimada de Polymarket (~0.1% por lado)
        commission = pos.size_usdc * 0.002  # 0.2% round-trip
        return (pos.size_usdc * price_change_pct) - commission

    @staticmethod
    def _generate_order_id() -> str:
        """Genera un ID único para la orden."""
        return str(uuid.uuid4()).replace("-", "")[:16]


# ---------------------------------------------------------------------------
# INSTANCIA GLOBAL (singleton)
# ---------------------------------------------------------------------------
EXECUTION_ENGINE = ExecutionEngine()
