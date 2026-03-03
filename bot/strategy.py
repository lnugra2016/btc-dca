"""
strategy.py — Orquestador de la estrategia de trading.
Coordina: data_feed → edge_model → risk_manager → execution_engine.
No contiene lógica de datos, riesgo ni ejecución: solo orquesta el flujo.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config import CONFIG
from data_feed import (
    Orderbook,
    MomentumResult,
    VolatilityRegime,
    get_polymarket_orderbook,
    get_polymarket_last_price,
    get_btc_spot_price,
    calculate_spot_momentum,
    calculate_volatility_regime,
)
from edge_model import EdgeResult, analyze_edge
from execution_engine import EXECUTION_ENGINE, ExecutionEngine
from risk_manager import RISK_MANAGER, RiskManager, TradeRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ENUMS Y DATA STRUCTURES
# ---------------------------------------------------------------------------

class SignalType(str, Enum):
    BUY_YES = "BUY_YES"     # Comprar YES (BTC sube)
    BUY_NO = "BUY_NO"       # Comprar NO (BTC baja)
    HOLD = "HOLD"           # No operar


@dataclass
class MarketSnapshot:
    """Snapshot completo del mercado en un momento dado."""
    orderbook: Optional[Orderbook]
    spot_price: Optional[float]
    momentum: Optional[MomentumResult]
    volatility: Optional[VolatilityRegime]
    last_poly_price: Optional[float]
    timestamp: float


@dataclass
class Signal:
    """Señal de trading generada por la estrategia."""
    signal_type: SignalType
    direction: str                    # "YES" | "NO" | "NONE"
    edge: float
    confidence: float                 # 0.0 – 1.0
    entry_price: float
    position_size: Optional[float]    # USDC a invertir
    rejection_reason: Optional[str] = None


@dataclass
class TradeResult:
    """Resultado completo de un ciclo de trade."""
    signal: Signal
    executed: bool
    close_result: Optional[dict] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# STRATEGY
# ---------------------------------------------------------------------------

class Strategy:
    """
    Estrategia de trading para mercados binarios BTC en Polymarket.

    Flujo por ciclo de evaluación:
      1. evaluate_market() → recopila datos de mercado
      2. generate_signal() → decide si hay señal operable
      3. execute_trade() → abre, monitorea y cierra la posición
    """

    def __init__(
        self,
        risk_manager: RiskManager = None,
        execution_engine: ExecutionEngine = None,
    ):
        self._rm = risk_manager or RISK_MANAGER
        self._engine = execution_engine or EXECUTION_ENGINE
        self._last_eval_ts: float = 0.0
        logger.info("Strategy inicializada")

    # -----------------------------------------------------------------------
    # PASO 1: EVALUAR MERCADO
    # -----------------------------------------------------------------------

    def evaluate_market(self) -> MarketSnapshot:
        """
        Recopila y estructura todos los datos de mercado necesarios.

        Ejecuta las llamadas a data_feed en orden y crea un snapshot
        coherente para el análisis posterior.

        Returns:
            MarketSnapshot con todos los datos disponibles (algunos pueden ser None
            si hubo errores de conectividad).
        """
        logger.debug("=== Evaluando mercado ===")

        # Obtener precio spot BTC primero (necesario para acumular buffer)
        spot_price = get_btc_spot_price()

        # Orderbook de Polymarket
        orderbook = get_polymarket_orderbook()

        # Último precio del mercado
        last_poly_price = get_polymarket_last_price()

        # Cálculos derivados del buffer de precios spot
        momentum = calculate_spot_momentum(lookback_seconds=60)
        volatility = calculate_volatility_regime(lookback_seconds=300)

        snapshot = MarketSnapshot(
            orderbook=orderbook,
            spot_price=spot_price,
            momentum=momentum,
            volatility=volatility,
            last_poly_price=last_poly_price,
            timestamp=time.time(),
        )

        self._log_snapshot(snapshot)
        return snapshot

    # -----------------------------------------------------------------------
    # PASO 2: GENERAR SEÑAL
    # -----------------------------------------------------------------------

    def generate_signal(self, snapshot: MarketSnapshot) -> Signal:
        """
        Analiza el snapshot de mercado y genera una señal de trading.

        Flujo:
          1. Verificar que el bot puede operar (kill-switch, pausa)
          2. Verificar que no hay posición activa
          3. Verificar datos mínimos disponibles
          4. Correr modelo de edge
          5. Si hay edge: calcular tamaño de posición

        Args:
            snapshot: Datos de mercado actuales.

        Returns:
            Signal con tipo HOLD (sin trade) o señal de entrada con parámetros.
        """
        # -- Check operacional --
        if not self._rm.is_operational:
            return Signal(
                signal_type=SignalType.HOLD,
                direction="NONE",
                edge=0.0,
                confidence=0.0,
                entry_price=0.0,
                position_size=None,
                rejection_reason="bot_no_operacional",
            )

        # -- Verificar posición activa --
        if self._engine.has_active_position:
            return Signal(
                signal_type=SignalType.HOLD,
                direction="NONE",
                edge=0.0,
                confidence=0.0,
                entry_price=0.0,
                position_size=None,
                rejection_reason="posicion_activa",
            )

        # -- Verificar datos mínimos --
        if snapshot.orderbook is None:
            return self._hold("orderbook_unavailable")

        if snapshot.momentum is None:
            return self._hold("momentum_unavailable")

        if snapshot.volatility is None:
            return self._hold("volatility_unavailable")

        # -- Analizar edge --
        edge_result: EdgeResult = analyze_edge(
            orderbook=snapshot.orderbook,
            momentum=snapshot.momentum,
            volatility=snapshot.volatility,
        )

        if not edge_result.has_edge:
            logger.debug("Sin edge | %s", edge_result.rejection_reason)
            return self._hold(edge_result.rejection_reason or "no_edge")

        # -- Calcular precio de entrada y tamaño --
        entry_price = self._determine_entry_price(snapshot.orderbook, edge_result.direction)
        if entry_price is None:
            return self._hold("entry_price_unavailable")

        sl_price = entry_price * (1 - CONFIG.risk.stop_loss_pct)
        position_size = self._rm.calculate_position_size(
            entry_price=entry_price,
            stop_loss_price=sl_price,
        )

        if position_size is None:
            return self._hold("position_size_invalido")

        # -- Construir señal de entrada --
        signal_type = (
            SignalType.BUY_YES if edge_result.direction == "YES" else SignalType.BUY_NO
        )

        # Confianza = edge normalizado al umbral mínimo (1.0 = exactamente en umbral)
        confidence = min(1.0, edge_result.edge / CONFIG.thresholds.min_edge_pct)

        signal = Signal(
            signal_type=signal_type,
            direction=edge_result.direction,
            edge=edge_result.edge,
            confidence=confidence,
            entry_price=entry_price,
            position_size=position_size,
        )

        logger.info(
            "SEÑAL GENERADA | tipo=%s edge=%.4f conf=%.2f entry=%.4f size=%.4f",
            signal_type.value, edge_result.edge, confidence, entry_price, position_size,
        )
        return signal

    # -----------------------------------------------------------------------
    # PASO 3: EJECUTAR TRADE
    # -----------------------------------------------------------------------

    def execute_trade(self, signal: Signal) -> TradeResult:
        """
        Ejecuta la señal de trading: abre posición y la gestiona hasta cierre.

        Flujo:
          1. Si señal es HOLD → retornar sin acción
          2. Abrir posición (YES o NO)
          3. Monitorear posición en loop hasta TP/SL/timeout
          4. Actualizar equity en risk_manager con PnL realizado

        Args:
            signal: Señal generada por generate_signal().

        Returns:
            TradeResult con resultado completo del trade.
        """
        if signal.signal_type == SignalType.HOLD:
            logger.debug("Señal HOLD — sin ejecución")
            return TradeResult(signal=signal, executed=False)

        logger.info(
            "Ejecutando trade | dir=%s entry=%.4f size=%.4f USDC",
            signal.direction, signal.entry_price, signal.position_size or 0,
        )

        # -- Abrir posición --
        open_ts = time.time()
        if signal.signal_type == SignalType.BUY_YES:
            order = self._engine.place_yes_order(
                size_usdc=signal.position_size,
                limit_price=signal.entry_price,
            )
        else:
            order = self._engine.place_no_order(
                size_usdc=signal.position_size,
                limit_price=signal.entry_price,
            )

        if order is None:
            logger.error("Fallo al abrir posición")
            return TradeResult(
                signal=signal,
                executed=False,
                error="order_placement_failed",
            )

        # -- Monitorear en loop hasta cierre --
        close_result = self._monitor_until_close()

        # -- Actualizar risk manager con resultado --
        if close_result and "pnl" in close_result:
            pnl = close_result["pnl"]
            outcome = "win" if pnl > 0 else "loss"
            if close_result.get("reason") == "timeout":
                outcome = "timeout"

            trade_record = TradeRecord(
                trade_id=close_result.get("position_id", "unknown"),
                direction=signal.direction,
                entry_price=close_result.get("entry_price", signal.entry_price),
                exit_price=close_result.get("exit_price", signal.entry_price),
                size=signal.position_size or 0,
                pnl=pnl,
                outcome=outcome,
                open_ts=open_ts,
                close_ts=time.time(),
            )
            self._rm.update_equity(pnl=pnl, trade_record=trade_record)
            self._rm.log_state()

        return TradeResult(
            signal=signal,
            executed=True,
            close_result=close_result,
        )

    # -----------------------------------------------------------------------
    # CICLO COMPLETO (punto de entrada del loop principal)
    # -----------------------------------------------------------------------

    def run_cycle(self) -> Optional[TradeResult]:
        """
        Ejecuta un ciclo completo de evaluación → señal → ejecución.

        Returns:
            TradeResult si se generó señal, None si HOLD.
        """
        # Verificar kill-switch en disco
        if self._rm.check_kill_switch_file():
            logger.critical("Kill-switch detectado — deteniendo ciclo")
            return None

        snapshot = self.evaluate_market()
        signal = self.generate_signal(snapshot)

        if signal.signal_type == SignalType.HOLD:
            logger.debug("Ciclo HOLD | razón=%s", signal.rejection_reason)
            return None

        return self.execute_trade(signal)

    # -----------------------------------------------------------------------
    # MÉTODOS INTERNOS
    # -----------------------------------------------------------------------

    def _monitor_until_close(self) -> Optional[dict]:
        """
        Loop de monitoreo de la posición activa hasta que se cierre.

        Llama a monitor_open_position() cada monitor_interval segundos.
        """
        interval = CONFIG.execution.monitor_interval

        while self._engine.has_active_position:
            result = self._engine.monitor_open_position()

            if result is None:
                break

            if result.get("status") == "closed":
                logger.info(
                    "Posición cerrada | razón=%s PnL=%.4f",
                    result.get("reason"), result.get("pnl", 0),
                )
                return result

            if result.get("status") == "monitoring":
                unrealized = result.get("pnl_unrealized", 0)
                remaining = result.get("time_remaining", 0)
                logger.debug(
                    "Monitoreo | PnL_unreal=%.4f timeout_en=%.0fs",
                    unrealized, remaining,
                )

            time.sleep(interval)

        return None

    def _determine_entry_price(
        self,
        orderbook: Orderbook,
        direction: str,
    ) -> Optional[float]:
        """
        Determina el precio de entrada óptimo basado en el orderbook.

        Para órdenes BUY: usa el best_ask (compramos del vendedor más barato).
        Agrega un pequeño buffer para mejorar probabilidad de fill.

        Args:
            orderbook: Orderbook actual.
            direction: "YES" o "NO".

        Returns:
            Precio de entrada, o None si no disponible.
        """
        # Usamos best_ask para tener fill inmediato
        entry = orderbook.best_ask
        if entry is None:
            entry = orderbook.mid_price

        if entry is None:
            return None

        # Buffer de 0.1% sobre el ask para garantizar fill en mercado rápido
        entry_with_buffer = entry * 1.001

        # Clamp a rango válido
        return max(0.01, min(0.99, entry_with_buffer))

    def _hold(self, reason: str) -> Signal:
        """Helper para crear señales HOLD."""
        return Signal(
            signal_type=SignalType.HOLD,
            direction="NONE",
            edge=0.0,
            confidence=0.0,
            entry_price=0.0,
            position_size=None,
            rejection_reason=reason,
        )

    def _log_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Log estructurado del snapshot de mercado."""
        ob = snapshot.orderbook
        mom = snapshot.momentum
        vol = snapshot.volatility

        logger.debug(
            "Snapshot | spot=$%.2f | poly_price=%.4f | "
            "spread=%.4f%% | imbalance=%.4f | "
            "momentum=%.5f(dir=%d) | vol=%s(σ=%.5f)",
            snapshot.spot_price or 0,
            snapshot.last_poly_price or 0,
            (ob.spread or 0) * 100 if ob else 0,
            (ob.imbalance or 0) if ob else 0,
            (mom.value if mom else 0),
            (mom.direction if mom else 0),
            (vol.regime if vol else "N/A"),
            (vol.sigma if vol else 0),
        )


# ---------------------------------------------------------------------------
# INSTANCIA GLOBAL (singleton)
# ---------------------------------------------------------------------------
STRATEGY = Strategy()
