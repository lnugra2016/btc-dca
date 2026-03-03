"""
backtester.py — Backtester con simulación Monte Carlo y análisis estadístico completo.
Simula 300 trades con los parámetros del sistema y calcula métricas de viabilidad.
No usa datos históricos reales: valida la estrategia a nivel matemático/estadístico.
"""

import logging
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Optional

from config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class TradeSimResult:
    """Resultado de un trade simulado individual."""
    trade_num: int
    outcome: str          # "win" | "loss"
    pnl_pct: float        # Retorno porcentual sobre el capital arriesgado
    pnl_abs: float        # P&L absoluto en USDC
    equity_after: float


@dataclass
class BacktestResult:
    """Resultado completo del backtest de una simulación."""
    trades: list[TradeSimResult]
    initial_equity: float
    final_equity: float
    total_return_pct: float
    win_rate: float
    expectancy: float           # Retorno esperado por trade en USDC
    profit_factor: float        # Gross profit / Gross loss
    max_drawdown_pct: float     # Mayor caída desde peak
    risk_of_ruin: float         # Probabilidad de perder todo (Monte Carlo)
    sharpe_ratio: float
    num_wins: int
    num_losses: int
    num_trades: int
    consecutive_losses_max: int


@dataclass
class MonteCarloResult:
    """Resultado de la simulación Monte Carlo."""
    num_runs: int
    num_simulations_per_run: int
    win_rate_assumed: float
    risk_of_ruin: float           # % de runs que llegaron a ruina
    median_final_equity: float
    pct_5_final_equity: float     # Percentil 5 (peor 5% de casos)
    pct_95_final_equity: float    # Percentil 95 (mejor 5% de casos)
    avg_max_drawdown: float
    probability_of_profit: float  # % de runs que terminaron en positivo
    equity_paths: list[list[float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SIMULADOR DE TRADE INDIVIDUAL
# ---------------------------------------------------------------------------

class TradeSim:
    """Simula el resultado de un trade binario con TP/SL y timeout."""

    def __init__(
        self,
        win_rate: float,
        take_profit_pct: float,
        stop_loss_pct: float,
        risk_per_trade: float,
        commission_pct: float = 0.002,  # 0.2% round-trip
    ):
        self.win_rate = win_rate
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.risk_per_trade = risk_per_trade
        self.commission_pct = commission_pct

    def simulate_trade(self, equity: float, trade_num: int) -> TradeSimResult:
        """
        Simula un trade individual.

        La posición es ganadora con probabilidad win_rate (TP alcanzado),
        perdedora con probabilidad (1 - win_rate) (SL o timeout).

        Returns:
            TradeSimResult con P&L y equity resultante.
        """
        capital_at_risk = equity * self.risk_per_trade

        # Simular outcome con win_rate calibrado
        is_win = random.random() < self.win_rate

        if is_win:
            # Ganancia: TP alcanzado
            gross_gain = capital_at_risk * (self.take_profit_pct / self.stop_loss_pct)
            commission = equity * self.commission_pct
            pnl = gross_gain - commission
            outcome = "win"
        else:
            # Pérdida: SL o timeout
            commission = equity * self.commission_pct
            pnl = -capital_at_risk - commission
            outcome = "loss"

        pnl_pct = pnl / equity if equity > 0 else 0
        equity_after = max(0.0, equity + pnl)

        return TradeSimResult(
            trade_num=trade_num,
            outcome=outcome,
            pnl_pct=pnl_pct,
            pnl_abs=pnl,
            equity_after=equity_after,
        )


# ---------------------------------------------------------------------------
# BACKTESTER PRINCIPAL
# ---------------------------------------------------------------------------

class Backtester:
    """
    Backtester estadístico para la estrategia BTC binaria.

    Ejecuta:
      1. Simulación secuencial de N trades (300 por defecto)
      2. Monte Carlo con M runs para distribución de outcomes
      3. Cálculo de métricas de rendimiento y riesgo
    """

    def __init__(self):
        cfg = CONFIG.backtester
        self.num_simulations = cfg.num_simulations
        self.monte_carlo_runs = cfg.monte_carlo_runs
        self.initial_equity = cfg.initial_equity
        self.risk_per_trade = cfg.risk_per_trade
        self.take_profit_pct = cfg.take_profit_pct
        self.stop_loss_pct = cfg.stop_loss_pct
        self.win_rate = cfg.assumed_win_rate

        self._sim = TradeSim(
            win_rate=self.win_rate,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            risk_per_trade=self.risk_per_trade,
        )
        logger.info(
            "Backtester init | N=%d MC=%d WR=%.2f%% TP=%.1f%% SL=%.1f%%",
            self.num_simulations, self.monte_carlo_runs,
            self.win_rate * 100, self.take_profit_pct * 100, self.stop_loss_pct * 100,
        )

    # -----------------------------------------------------------------------
    # SIMULACIÓN SECUENCIAL
    # -----------------------------------------------------------------------

    def run_simulation(
        self,
        win_rate_override: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> BacktestResult:
        """
        Ejecuta una simulación secuencial de N trades.

        Args:
            win_rate_override: Si se proporciona, usa este win_rate en lugar del configurado.
            seed: Semilla para reproducibilidad.

        Returns:
            BacktestResult con todas las métricas calculadas.
        """
        if seed is not None:
            random.seed(seed)

        win_rate = win_rate_override or self.win_rate
        sim = TradeSim(
            win_rate=win_rate,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            risk_per_trade=self.risk_per_trade,
        )

        equity = self.initial_equity
        trades: list[TradeSimResult] = []
        peak_equity = equity
        max_drawdown = 0.0
        consecutive_losses = 0
        max_consecutive_losses = 0
        current_consec = 0

        for i in range(self.num_simulations):
            if equity <= 0:
                logger.debug("Ruina en trade %d", i)
                break

            trade = sim.simulate_trade(equity=equity, trade_num=i + 1)
            trades.append(trade)
            equity = trade.equity_after

            # Drawdown tracking
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd

            # Pérdidas consecutivas
            if trade.outcome == "loss":
                current_consec += 1
                if current_consec > max_consecutive_losses:
                    max_consecutive_losses = current_consec
            else:
                current_consec = 0

        # -- Métricas --
        num_wins = sum(1 for t in trades if t.outcome == "win")
        num_losses = len(trades) - num_wins
        actual_win_rate = num_wins / len(trades) if trades else 0

        gross_profit = sum(t.pnl_abs for t in trades if t.pnl_abs > 0)
        gross_loss = abs(sum(t.pnl_abs for t in trades if t.pnl_abs < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        total_pnl = equity - self.initial_equity
        total_return_pct = total_pnl / self.initial_equity

        # Expectancy = (WR * avg_win) - ((1-WR) * avg_loss)
        avg_win = gross_profit / num_wins if num_wins > 0 else 0
        avg_loss = gross_loss / num_losses if num_losses > 0 else 0
        expectancy = (actual_win_rate * avg_win) - ((1 - actual_win_rate) * avg_loss)

        # Sharpe simplificado (retorno / desviación)
        returns = [t.pnl_pct for t in trades]
        if len(returns) > 1:
            mean_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            sharpe = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0
        else:
            sharpe = 0.0

        result = BacktestResult(
            trades=trades,
            initial_equity=self.initial_equity,
            final_equity=equity,
            total_return_pct=total_return_pct,
            win_rate=actual_win_rate,
            expectancy=expectancy,
            profit_factor=profit_factor,
            max_drawdown_pct=max_drawdown,
            risk_of_ruin=0.0,  # Se calcula en Monte Carlo
            sharpe_ratio=sharpe,
            num_wins=num_wins,
            num_losses=num_losses,
            num_trades=len(trades),
            consecutive_losses_max=max_consecutive_losses,
        )

        self._log_simulation_result(result)
        return result

    # -----------------------------------------------------------------------
    # MONTE CARLO
    # -----------------------------------------------------------------------

    def run_monte_carlo(self, win_rate_override: Optional[float] = None) -> MonteCarloResult:
        """
        Ejecuta simulación Monte Carlo con múltiples runs independientes.

        Cada run es una secuencia independiente de N trades.
        Calcula distribución de outcomes y risk of ruin.

        Args:
            win_rate_override: Win rate a usar. Default: configurado.

        Returns:
            MonteCarloResult con distribución estadística completa.
        """
        win_rate = win_rate_override or self.win_rate
        logger.info("Iniciando Monte Carlo | runs=%d n=%d WR=%.2f%%",
                    self.monte_carlo_runs, self.num_simulations, win_rate * 100)

        final_equities = []
        max_drawdowns = []
        equity_paths = []
        ruin_count = 0
        profit_count = 0

        for run in range(self.monte_carlo_runs):
            sim_result = self.run_simulation(
                win_rate_override=win_rate,
                seed=None,  # Aleatorio para cada run
            )

            final_eq = sim_result.final_equity
            final_equities.append(final_eq)
            max_drawdowns.append(sim_result.max_drawdown_pct)

            if final_eq <= 0.01:  # Ruina: < $0.01 restante
                ruin_count += 1

            if final_eq > self.initial_equity:
                profit_count += 1

            # Guardar path de equity (muestra 1 de cada 50 para no saturar memoria)
            if run % (self.monte_carlo_runs // 20) == 0:
                path = [t.equity_after for t in sim_result.trades]
                equity_paths.append([self.initial_equity] + path)

        final_equities.sort()
        risk_of_ruin = ruin_count / self.monte_carlo_runs
        probability_of_profit = profit_count / self.monte_carlo_runs

        mc_result = MonteCarloResult(
            num_runs=self.monte_carlo_runs,
            num_simulations_per_run=self.num_simulations,
            win_rate_assumed=win_rate,
            risk_of_ruin=risk_of_ruin,
            median_final_equity=statistics.median(final_equities),
            pct_5_final_equity=final_equities[int(self.monte_carlo_runs * 0.05)],
            pct_95_final_equity=final_equities[int(self.monte_carlo_runs * 0.95)],
            avg_max_drawdown=statistics.mean(max_drawdowns),
            probability_of_profit=probability_of_profit,
            equity_paths=equity_paths,
        )

        self._log_monte_carlo_result(mc_result)
        return mc_result

    # -----------------------------------------------------------------------
    # WIN RATE BREAK-EVEN
    # -----------------------------------------------------------------------

    def calculate_breakeven_win_rate(self) -> float:
        """
        Calcula el win rate mínimo para tener expectancy positiva.

        Fórmula: WR_min = avg_loss / (avg_win + avg_loss)
        Con el ratio TP/SL de la estrategia:
          avg_win / avg_loss = TP_pct / SL_pct (aproximación sin comisiones)

        Returns:
            Win rate mínimo para break-even (sin comisiones).
        """
        rr_ratio = self.take_profit_pct / self.stop_loss_pct  # Ratio recompensa/riesgo
        breakeven_wr = 1 / (1 + rr_ratio)
        logger.info(
            "Break-even WR: %.4f (%.2f%%) | RR ratio: %.2f",
            breakeven_wr, breakeven_wr * 100, rr_ratio,
        )
        return breakeven_wr

    # -----------------------------------------------------------------------
    # ANÁLISIS DE SENSIBILIDAD
    # -----------------------------------------------------------------------

    def sensitivity_analysis(self, win_rates: list[float] = None) -> dict[float, BacktestResult]:
        """
        Analiza el rendimiento de la estrategia bajo diferentes win rates.

        Args:
            win_rates: Lista de win rates a evaluar. Default: 0.45 a 0.65.

        Returns:
            Dict {win_rate: BacktestResult} para cada win rate.
        """
        if win_rates is None:
            win_rates = [0.45, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65]

        results = {}
        logger.info("Análisis de sensibilidad | win_rates=%s", win_rates)

        for wr in win_rates:
            result = self.run_simulation(win_rate_override=wr, seed=42)
            results[wr] = result

        return results

    # -----------------------------------------------------------------------
    # REPORTE COMPLETO
    # -----------------------------------------------------------------------

    def full_report(self) -> dict:
        """
        Genera un reporte completo: simulación principal + Monte Carlo + sensibilidad.

        Returns:
            Dict con todos los resultados organizados.
        """
        logger.info("=" * 60)
        logger.info("INICIANDO BACKTEST COMPLETO")
        logger.info("=" * 60)

        # Simulación principal
        main_sim = self.run_simulation(seed=42)

        # Monte Carlo
        mc = self.run_monte_carlo()

        # Break-even win rate
        be_wr = self.calculate_breakeven_win_rate()

        # Análisis de sensibilidad
        sensitivity = self.sensitivity_analysis()

        report = {
            "config": {
                "initial_equity": self.initial_equity,
                "num_trades": self.num_simulations,
                "win_rate_assumed": self.win_rate,
                "take_profit_pct": self.take_profit_pct,
                "stop_loss_pct": self.stop_loss_pct,
                "risk_per_trade_pct": self.risk_per_trade,
                "rr_ratio": self.take_profit_pct / self.stop_loss_pct,
            },
            "main_simulation": {
                "final_equity": main_sim.final_equity,
                "total_return_pct": main_sim.total_return_pct * 100,
                "win_rate": main_sim.win_rate * 100,
                "expectancy_usdc": main_sim.expectancy,
                "profit_factor": main_sim.profit_factor,
                "max_drawdown_pct": main_sim.max_drawdown_pct * 100,
                "sharpe_ratio": main_sim.sharpe_ratio,
                "num_trades": main_sim.num_trades,
                "num_wins": main_sim.num_wins,
                "num_losses": main_sim.num_losses,
                "max_consecutive_losses": main_sim.consecutive_losses_max,
            },
            "monte_carlo": {
                "runs": mc.num_runs,
                "risk_of_ruin_pct": mc.risk_of_ruin * 100,
                "probability_of_profit_pct": mc.probability_of_profit * 100,
                "median_final_equity": mc.median_final_equity,
                "p5_final_equity": mc.pct_5_final_equity,
                "p95_final_equity": mc.pct_95_final_equity,
                "avg_max_drawdown_pct": mc.avg_max_drawdown * 100,
            },
            "breakeven_win_rate_pct": be_wr * 100,
            "sensitivity": {
                f"wr_{int(wr * 100)}pct": {
                    "final_equity": r.final_equity,
                    "total_return_pct": r.total_return_pct * 100,
                    "max_drawdown_pct": r.max_drawdown_pct * 100,
                    "expectancy": r.expectancy,
                }
                for wr, r in sensitivity.items()
            },
        }

        self._print_report_summary(report)
        return report

    # -----------------------------------------------------------------------
    # LOGGING
    # -----------------------------------------------------------------------

    def _log_simulation_result(self, result: BacktestResult) -> None:
        logger.info(
            "SIM | trades=%d WR=%.1f%% expectancy=%.4f PF=%.2f "
            "max_dd=%.1f%% return=%.2f%% sharpe=%.2f",
            result.num_trades,
            result.win_rate * 100,
            result.expectancy,
            result.profit_factor,
            result.max_drawdown_pct * 100,
            result.total_return_pct * 100,
            result.sharpe_ratio,
        )

    def _log_monte_carlo_result(self, mc: MonteCarloResult) -> None:
        logger.info(
            "MC | runs=%d risk_of_ruin=%.2f%% P(profit)=%.2f%% "
            "median_eq=%.2f p5=%.2f p95=%.2f avg_dd=%.2f%%",
            mc.num_runs,
            mc.risk_of_ruin * 100,
            mc.probability_of_profit * 100,
            mc.median_final_equity,
            mc.pct_5_final_equity,
            mc.pct_95_final_equity,
            mc.avg_max_drawdown * 100,
        )

    def _print_report_summary(self, report: dict) -> None:
        """Imprime resumen del reporte al log en formato legible."""
        sim = report["main_simulation"]
        mc = report["monte_carlo"]
        cfg = report["config"]

        logger.info("=" * 60)
        logger.info("BACKTEST REPORT SUMMARY")
        logger.info("=" * 60)
        logger.info("CONFIGURACIÓN:")
        logger.info("  Capital inicial:    $%.2f", cfg["initial_equity"])
        logger.info("  Trades simulados:   %d", cfg["num_trades"])
        logger.info("  Win rate asumido:   %.2f%%", cfg["win_rate_assumed"] * 100)
        logger.info("  TP / SL:            %.1f%% / %.1f%%",
                    cfg["take_profit_pct"] * 100, cfg["stop_loss_pct"] * 100)
        logger.info("  Riesgo por trade:   %.2f%%", cfg["risk_per_trade_pct"] * 100)
        logger.info("  Ratio RR:           %.1f:1", cfg["rr_ratio"])
        logger.info("")
        logger.info("RESULTADOS PRINCIPALES:")
        logger.info("  Win Rate:           %.2f%%", sim["win_rate"])
        logger.info("  Expectancy:         $%.4f por trade", sim["expectancy_usdc"])
        logger.info("  Profit Factor:      %.2f", sim["profit_factor"])
        logger.info("  Max Drawdown:       %.2f%%", sim["max_drawdown_pct"])
        logger.info("  Retorno total:      %.2f%%", sim["total_return_pct"])
        logger.info("  Equity final:       $%.2f", sim["final_equity"])
        logger.info("  Sharpe Ratio:       %.2f", sim["sharpe_ratio"])
        logger.info("  Consec. losses max: %d", sim["max_consecutive_losses"])
        logger.info("")
        logger.info("MONTE CARLO (%d runs):", mc["runs"])
        logger.info("  Risk of Ruin:       %.3f%%", mc["risk_of_ruin_pct"])
        logger.info("  P(ganancia):        %.2f%%", mc["probability_of_profit_pct"])
        logger.info("  Equity mediana:     $%.2f", mc["median_final_equity"])
        logger.info("  P5 equity:          $%.2f", mc["p5_final_equity"])
        logger.info("  P95 equity:         $%.2f", mc["p95_final_equity"])
        logger.info("  Avg Max Drawdown:   %.2f%%", mc["avg_max_drawdown_pct"])
        logger.info("")
        logger.info("  Break-even WR:      %.2f%%", report["breakeven_win_rate_pct"])
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# ENTRY POINT PARA EJECUCIÓN DIRECTA
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """Función de conveniencia para ejecutar el backtest completo."""
    bt = Backtester()
    return bt.full_report()


if __name__ == "__main__":
    import sys
    import os

    # Configurar logging básico para ejecución directa
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    report = run_backtest()
    sys.exit(0)
