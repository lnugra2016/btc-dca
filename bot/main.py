"""
main.py — Loop principal event-driven del bot de trading.
Punto de entrada único del sistema. Orquesta el ciclo completo con:
  - Logging estructurado
  - Manejo global de excepciones
  - Intervalos de evaluación aleatorios (anti front-running)
  - Shutdown graceful
  - Soporte para modo backtest
"""

import argparse
import logging
import logging.handlers
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# SETUP PATHS — garantiza que los módulos del bot son importables
# ---------------------------------------------------------------------------
BOT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BOT_DIR))

from config import CONFIG
from risk_manager import RISK_MANAGER
from strategy import STRATEGY
from backtester import run_backtest


# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE LOGGING
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """
    Configura el sistema de logging con:
      - Handler de archivo con rotación (RotatingFileHandler)
      - Handler de consola (StreamHandler)
      - Formato estructurado con timestamp, nivel, módulo y mensaje
    """
    log_cfg = CONFIG.logging

    # Crear directorio de logs si no existe
    log_path = Path(log_cfg.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Logger raíz
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_cfg.level, logging.INFO))

    formatter = logging.Formatter(
        fmt=log_cfg.format,
        datefmt=log_cfg.date_format,
    )

    # Handler de archivo con rotación
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=log_cfg.max_bytes,
        backupCount=log_cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, log_cfg.level, logging.INFO))

    # Handler de consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SHUTDOWN GRACEFUL
# ---------------------------------------------------------------------------

class GracefulShutdown:
    """
    Captura señales del sistema (SIGTERM, SIGINT) para shutdown limpio.
    Permite que el ciclo actual termine antes de cerrar.
    """

    def __init__(self):
        self._shutdown_requested = False
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger = logging.getLogger(__name__)
        logger.warning("Señal recibida: %s — iniciando shutdown graceful", sig_name)
        self._shutdown_requested = True

    @property
    def should_stop(self) -> bool:
        return self._shutdown_requested


# ---------------------------------------------------------------------------
# MAIN BOT LOOP
# ---------------------------------------------------------------------------

class TradingBot:
    """
    Bot de trading principal. Coordina el loop event-driven y el estado global.
    """

    def __init__(self):
        self._logger = logging.getLogger(self.__class__.__name__)
        self._shutdown = GracefulShutdown()
        self._cycle_count = 0
        self._start_time = time.time()

    def run(self) -> None:
        """
        Loop principal del bot. Evalúa el mercado en intervalos aleatorios
        entre eval_interval_min y eval_interval_max segundos.

        El intervalo aleatorio reduce la predictibilidad frente a otros
        participantes del mercado.
        """
        self._logger.info("=" * 60)
        self._logger.info("BOT DE TRADING BTC/POLYMARKET INICIANDO")
        self._logger.info("Capital inicial: $%.2f", CONFIG.risk.initial_equity)
        self._logger.info("Riesgo por trade: %.2f%%", CONFIG.risk.risk_per_trade * 100)
        self._logger.info("Mercado: %s", CONFIG.market.btc_5min_market_id)
        self._logger.info("=" * 60)

        # Verificar kill-switch previo antes de iniciar
        if RISK_MANAGER.check_kill_switch_file():
            self._logger.critical(
                "Kill-switch activo en disco. Eliminar %s para continuar.",
                CONFIG.kill_switch.flag_file_path,
            )
            sys.exit(1)

        while not self._shutdown.should_stop:
            try:
                self._run_cycle()

                # Intervalo aleatorio entre evaluaciones
                sleep_seconds = random.uniform(
                    CONFIG.market.eval_interval_min,
                    CONFIG.market.eval_interval_max,
                )
                self._logger.debug("Próxima evaluación en %.1fs", sleep_seconds)
                time.sleep(sleep_seconds)

            except KeyboardInterrupt:
                self._logger.info("Interrupción manual recibida")
                break

            except Exception as exc:  # pylint: disable=broad-except
                self._logger.exception(
                    "ERROR INESPERADO en ciclo %d: %s", self._cycle_count, exc
                )
                # Pausa de seguridad antes de reintentar
                time.sleep(30)

        self._shutdown_gracefully()

    def _run_cycle(self) -> None:
        """Ejecuta un ciclo completo del bot."""
        self._cycle_count += 1
        cycle_start = time.time()

        self._logger.debug("--- Ciclo #%d ---", self._cycle_count)

        # Verificar kill-switch (archivo en disco)
        if RISK_MANAGER.check_kill_switch_file():
            self._logger.critical("Kill-switch activado externamente — deteniendo bot")
            self._shutdown._shutdown_requested = True
            return

        # Verificar si el bot puede operar
        if not RISK_MANAGER.is_operational:
            self._logger.info("Bot en pausa o kill-switch activo — saltando ciclo")
            return

        # Ejecutar ciclo de estrategia
        try:
            result = STRATEGY.run_cycle()

            if result is not None and result.executed:
                close = result.close_result or {}
                self._logger.info(
                    "TRADE COMPLETADO | dir=%s PnL=%.4f reason=%s equity=%.2f",
                    result.signal.direction,
                    close.get("pnl", 0),
                    close.get("reason", "unknown"),
                    RISK_MANAGER.equity,
                )

        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("Error en ciclo de estrategia: %s", exc, exc_info=True)

        cycle_duration = time.time() - cycle_start
        self._logger.debug("Ciclo #%d completado en %.2fs", self._cycle_count, cycle_duration)

        # Log de estado cada 10 ciclos
        if self._cycle_count % 10 == 0:
            self._log_bot_status()

    def _log_bot_status(self) -> None:
        """Emite un log de estado del bot cada N ciclos."""
        uptime = time.time() - self._start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)

        RISK_MANAGER.log_state()
        self._logger.info(
            "STATUS | uptime=%dh%dm | ciclos=%d",
            hours, minutes, self._cycle_count,
        )

    def _shutdown_gracefully(self) -> None:
        """Cierra el bot de forma limpia."""
        self._logger.info("Iniciando shutdown graceful...")

        # Cerrar posición activa si existe
        from execution_engine import EXECUTION_ENGINE
        if EXECUTION_ENGINE.has_active_position:
            self._logger.warning("Cerrando posición activa antes de shutdown...")
            result = EXECUTION_ENGINE.close_position(reason="shutdown")
            self._logger.info("Posición cerrada: %s", result)

        # Log estado final
        RISK_MANAGER.log_state()
        uptime = time.time() - self._start_time
        self._logger.info(
            "Bot detenido | uptime=%.0fs | ciclos=%d | equity_final=%.2f",
            uptime, self._cycle_count, RISK_MANAGER.equity,
        )


# ---------------------------------------------------------------------------
# CLI ARGUMENT PARSER
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comando."""
    parser = argparse.ArgumentParser(
        description="BTC Polymarket Trading Bot — Mercados binarios 5 minutos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modos de operación:
  python main.py              → Inicia el bot en modo live
  python main.py --backtest   → Ejecuta backtest completo y sale
  python main.py --dry-run    → Loop sin ejecución real de órdenes
        """,
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Ejecutar backtest estadístico y Monte Carlo",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluar mercado sin ejecutar órdenes reales",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Nivel de logging (override de config)",
    )
    parser.add_argument(
        "--reset-kill-switch",
        action="store_true",
        help="Resetear el kill-switch y salir",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main() -> int:
    """Punto de entrada principal del bot."""
    args = parse_args()

    # Override log level si se especificó
    if args.log_level:
        CONFIG.logging.level = args.log_level

    # Setup logging antes de cualquier otra operación
    logger = setup_logging()

    # -- RESET KILL-SWITCH --
    if args.reset_kill_switch:
        success = RISK_MANAGER.reset_kill_switch()
        if success:
            logger.info("Kill-switch reseteado exitosamente")
            return 0
        else:
            logger.error("Error reseteando kill-switch")
            return 1

    # -- MODO BACKTEST --
    if args.backtest:
        logger.info("Ejecutando backtest completo...")
        try:
            report = run_backtest()
            logger.info("Backtest completado exitosamente")
            return 0
        except Exception as exc:
            logger.exception("Error en backtest: %s", exc)
            return 1

    # -- MODO DRY-RUN --
    if args.dry_run:
        logger.info("MODO DRY-RUN — no se ejecutarán órdenes reales")
        # Parchear el engine para no ejecutar
        from execution_engine import EXECUTION_ENGINE, Order, OrderSide, OrderStatus
        import uuid

        def _mock_place_order(*args, **kwargs):
            logger.info("[DRY-RUN] Orden simulada (no ejecutada)")
            return Order(
                order_id=str(uuid.uuid4())[:8],
                token_id=CONFIG.market.btc_5min_market_id,
                side=OrderSide.BUY,
                size=kwargs.get("size_usdc", 0),
                price=kwargs.get("limit_price", 0.5),
                status=OrderStatus.FILLED,
                avg_fill_price=kwargs.get("limit_price", 0.5),
            )

        EXECUTION_ENGINE.place_limit_order = _mock_place_order

    # -- MODO LIVE --
    logger.info("Iniciando bot en modo %s", "DRY-RUN" if args.dry_run else "LIVE")

    try:
        bot = TradingBot()
        bot.run()
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Error fatal no manejado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
