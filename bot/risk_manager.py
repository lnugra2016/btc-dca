"""
risk_manager.py — Gestión de riesgo totalmente independiente del motor de ejecución.
Responsable de: tamaño de posición, tracking de equity, drawdown, kill-switch.
Es el único módulo autorizado para modificar el estado financiero del bot.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Registro inmutable de un trade cerrado."""
    trade_id: str
    direction: str          # "YES" | "NO"
    entry_price: float
    exit_price: float
    size: float             # Tamaño en USDC
    pnl: float              # P&L realizado
    outcome: str            # "win" | "loss" | "timeout"
    open_ts: float
    close_ts: float
    duration_seconds: float = 0.0

    def __post_init__(self):
        self.duration_seconds = self.close_ts - self.open_ts


@dataclass
class RiskState:
    """Estado mutable del gestor de riesgo. Thread-safe mediante lock."""
    equity: float
    peak_equity: float
    consecutive_losses: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    kill_switch_active: bool = False
    pause_until: float = 0.0      # epoch timestamp hasta el cual no operar
    trade_history: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# RISK MANAGER
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Gestor centralizado de riesgo. Todas las decisiones de tamaño y
    permiso de operación pasan por esta clase.

    Thread-safe: usa un lock interno para operaciones de escritura.
    """

    def __init__(self):
        initial = CONFIG.risk.initial_equity
        self._state = RiskState(
            equity=initial,
            peak_equity=initial,
        )
        self._lock = threading.Lock()
        logger.info("RiskManager inicializado | equity=%.2f", initial)

    # -----------------------------------------------------------------------
    # PROPIEDADES DE ESTADO (read-only)
    # -----------------------------------------------------------------------

    @property
    def equity(self) -> float:
        return self._state.equity

    @property
    def peak_equity(self) -> float:
        return self._state.peak_equity

    @property
    def current_drawdown(self) -> float:
        """Drawdown actual como fracción del peak equity."""
        if self._state.peak_equity == 0:
            return 0.0
        return (self._state.peak_equity - self._state.equity) / self._state.peak_equity

    @property
    def win_rate(self) -> float:
        if self._state.total_trades == 0:
            return 0.0
        return self._state.winning_trades / self._state.total_trades

    @property
    def is_operational(self) -> bool:
        """True si el bot puede operar (sin kill-switch y sin pausa activa)."""
        with self._lock:
            if self._state.kill_switch_active:
                return False
            if time.time() < self._state.pause_until:
                remaining = self._state.pause_until - time.time()
                logger.debug("Bot en pausa | %.0f segundos restantes", remaining)
                return False
            return True

    @property
    def state_snapshot(self) -> dict:
        """Snapshot del estado para logging."""
        s = self._state
        return {
            "equity": s.equity,
            "peak_equity": s.peak_equity,
            "drawdown_pct": self.current_drawdown * 100,
            "consecutive_losses": s.consecutive_losses,
            "total_trades": s.total_trades,
            "win_rate": self.win_rate,
            "total_pnl": s.total_pnl,
            "kill_switch": s.kill_switch_active,
        }

    # -----------------------------------------------------------------------
    # CÁLCULO DE TAMAÑO DE POSICIÓN
    # -----------------------------------------------------------------------

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
    ) -> Optional[float]:
        """
        Calcula el tamaño de posición usando gestión de riesgo basada en porcentaje.

        Fórmula: size = (equity * risk_pct) / |entry - stop_loss|

        Para contratos binarios Polymarket donde precio ∈ [0,1]:
          - Si compramos YES a 0.60 con stop en 0.59:
            stop_distance = 0.01
            size = (equity * 0.0075) / 0.01 = equity * 0.75

        Args:
            entry_price: Precio de entrada del contrato (0.0 – 1.0).
            stop_loss_price: Precio de stop-loss del contrato.

        Returns:
            Tamaño en USDC a invertir, o None si no hay equity suficiente.
        """
        if not self.is_operational:
            logger.warning("calculate_position_size: bot no operacional")
            return None

        with self._lock:
            equity = self._state.equity

        risk_amount = equity * CONFIG.risk.risk_per_trade
        stop_distance = abs(entry_price - stop_loss_price)

        if stop_distance == 0:
            logger.error("Stop distance = 0, imposible calcular tamaño")
            return None

        # Para contratos binarios: número de contratos = risk / stop_distance
        # Costo en USDC = contratos * entry_price
        num_contracts = risk_amount / stop_distance
        position_cost = num_contracts * entry_price

        # Limitar a un porcentaje máximo del equity (máx 5% del capital en 1 trade)
        max_position = equity * 0.05
        position_cost = min(position_cost, max_position)

        # No invertir menos de $0.50 (mínimo Polymarket)
        if position_cost < 0.50:
            logger.warning(
                "Posición demasiado pequeña: %.4f USDC (mínimo $0.50)", position_cost
            )
            return None

        logger.info(
            "Tamaño posición | equity=%.2f risk=%.4f stop_dist=%.4f size=%.4f USDC",
            equity, risk_amount, stop_distance, position_cost,
        )
        return round(position_cost, 4)

    # -----------------------------------------------------------------------
    # ACTUALIZACIÓN DE EQUITY
    # -----------------------------------------------------------------------

    def update_equity(self, pnl: float, trade_record: Optional[TradeRecord] = None) -> float:
        """
        Actualiza el equity tras cerrar un trade y actualiza estadísticas.

        Args:
            pnl: P&L realizado del trade (positivo = ganancia, negativo = pérdida).
            trade_record: Registro completo del trade para historial.

        Returns:
            Equity actualizado.
        """
        with self._lock:
            self._state.equity += pnl
            self._state.total_pnl += pnl
            self._state.total_trades += 1

            if pnl > 0:
                self._state.winning_trades += 1
                self._state.consecutive_losses = 0  # Reset racha perdedora
                logger.info("Trade GANADOR | PnL=+%.4f | equity=%.4f", pnl, self._state.equity)
            else:
                self._state.losing_trades += 1
                self._state.consecutive_losses += 1
                logger.info(
                    "Trade PERDEDOR | PnL=%.4f | equity=%.4f | pérdidas_consec=%d",
                    pnl, self._state.equity, self._state.consecutive_losses,
                )

            # Actualizar peak equity
            if self._state.equity > self._state.peak_equity:
                self._state.peak_equity = self._state.equity

            # Guardar en historial
            if trade_record:
                self._state.trade_history.append(trade_record)

            current_equity = self._state.equity

        # Verificar condiciones de riesgo tras actualización
        self.check_drawdown()
        self.check_consecutive_losses()

        return current_equity

    # -----------------------------------------------------------------------
    # VERIFICACIÓN DE DRAWDOWN
    # -----------------------------------------------------------------------

    def check_drawdown(self) -> bool:
        """
        Verifica si el drawdown supera el límite máximo.

        Returns:
            True si el drawdown es aceptable, False si se activó el kill-switch.
        """
        dd = self.current_drawdown
        max_dd = CONFIG.risk.max_drawdown_pct

        if dd >= max_dd:
            logger.critical(
                "KILL-SWITCH por DRAWDOWN | dd=%.2f%% >= límite=%.2f%%",
                dd * 100, max_dd * 100,
            )
            if CONFIG.kill_switch.auto_trigger_on_drawdown:
                self.kill_switch(reason=f"max_drawdown_excedido({dd:.2%})")
            return False

        logger.debug("Drawdown: %.2f%% (límite: %.2f%%)", dd * 100, max_dd * 100)
        return True

    # -----------------------------------------------------------------------
    # VERIFICACIÓN DE PÉRDIDAS CONSECUTIVAS
    # -----------------------------------------------------------------------

    def check_consecutive_losses(self) -> bool:
        """
        Verifica si se alcanzó el máximo de pérdidas consecutivas.

        Returns:
            True si se puede continuar, False si se activó pausa o kill-switch.
        """
        with self._lock:
            consec = self._state.consecutive_losses

        max_losses = CONFIG.risk.max_consecutive_losses

        if consec >= max_losses:
            pause_duration = CONFIG.risk.pause_after_losses_seconds
            logger.warning(
                "PAUSA por %d pérdidas consecutivas | pausa=%ds",
                consec, pause_duration,
            )
            with self._lock:
                self._state.pause_until = time.time() + pause_duration

            if CONFIG.kill_switch.auto_trigger_on_consecutive_losses:
                logger.warning("Kill-switch por pérdidas consecutivas")
                self.kill_switch(reason=f"max_consecutive_losses({consec})")
                return False

        return True

    # -----------------------------------------------------------------------
    # KILL-SWITCH
    # -----------------------------------------------------------------------

    def kill_switch(self, reason: str = "manual") -> None:
        """
        Activa el kill-switch, deteniendo toda operación nueva.

        También crea el archivo flag en disco para que el proceso pueda
        detectarlo desde fuera (útil para supervisión externa).

        Args:
            reason: Razón textual de la activación.
        """
        with self._lock:
            self._state.kill_switch_active = True

        logger.critical("=== KILL-SWITCH ACTIVADO === Razón: %s", reason)
        logger.critical("Estado final: %s", self.state_snapshot)

        # Crear archivo flag en disco
        flag_path = CONFIG.kill_switch.flag_file_path
        try:
            with open(flag_path, "w") as f:
                f.write(f"kill_switch_activated | reason={reason} | ts={time.time()}\n")
            logger.info("Flag de kill-switch creado en: %s", flag_path)
        except OSError as exc:
            logger.error("No se pudo crear archivo flag: %s", exc)

    def reset_kill_switch(self) -> bool:
        """
        Resetea el kill-switch (requiere intervención manual).
        Solo útil para testing o reinicio manual supervisado.

        Returns:
            True si se reseteó correctamente.
        """
        flag_path = CONFIG.kill_switch.flag_file_path

        # Verificar y eliminar archivo flag
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
                logger.info("Archivo flag de kill-switch eliminado")
            except OSError as exc:
                logger.error("No se pudo eliminar flag: %s", exc)
                return False

        with self._lock:
            self._state.kill_switch_active = False
            self._state.consecutive_losses = 0
            self._state.pause_until = 0.0

        logger.warning("Kill-switch reseteado manualmente")
        return True

    def check_kill_switch_file(self) -> bool:
        """
        Verifica si existe el archivo flag de kill-switch en disco.
        Útil para detener el bot externamente sin matar el proceso.

        Returns:
            True si el flag existe y se activó el kill-switch.
        """
        flag_path = CONFIG.kill_switch.flag_file_path
        if os.path.exists(flag_path):
            logger.critical("Archivo kill-switch detectado en disco: %s", flag_path)
            with self._lock:
                self._state.kill_switch_active = True
            return True
        return False

    # -----------------------------------------------------------------------
    # LOGGING DE ESTADO
    # -----------------------------------------------------------------------

    def log_state(self) -> None:
        """Emite un log estructurado del estado actual del risk manager."""
        s = self.state_snapshot
        logger.info(
            "RISK STATE | equity=%.2f peak=%.2f dd=%.2f%% "
            "trades=%d WR=%.1f%% PnL=%.4f consec_loss=%d ks=%s",
            s["equity"],
            s["peak_equity"],
            s["drawdown_pct"],
            s["total_trades"],
            s["win_rate"] * 100,
            s["total_pnl"],
            self._state.consecutive_losses,
            s["kill_switch"],
        )


# ---------------------------------------------------------------------------
# INSTANCIA GLOBAL (singleton)
# ---------------------------------------------------------------------------
# Importar con: from risk_manager import RISK_MANAGER
RISK_MANAGER = RiskManager()
