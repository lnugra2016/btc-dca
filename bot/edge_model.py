"""
edge_model.py — Modelo de ventaja estadística (edge).
Calcula si existe una ventaja real frente a la probabilidad implícita del mercado.
Decisión binaria: trade válido o no válido. Sin lógica de ejecución.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

from config import CONFIG
from data_feed import MomentumResult, Orderbook, VolatilityRegime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class EdgeResult:
    """Resultado completo del análisis de edge para una oportunidad."""
    implied_prob: float           # Probabilidad implícita del precio de mercado
    estimated_prob: float         # Probabilidad estimada por el modelo
    edge: float                   # estimated_prob - implied_prob
    direction: str                # "YES" | "NO" | "NONE"
    has_edge: bool                # True si edge ≥ umbral mínimo
    rejection_reason: Optional[str] = None  # Razón de rechazo si has_edge=False


@dataclass
class MarketConditions:
    """Snapshot de condiciones de mercado evaluadas."""
    spread_ok: bool
    imbalance_ok: bool
    vol_ok: bool
    spread_value: Optional[float]
    imbalance_value: Optional[float]
    vol_sigma: Optional[float]
    vol_regime: Optional[str]


# ---------------------------------------------------------------------------
# PROBABILIDAD IMPLÍCITA
# ---------------------------------------------------------------------------

def implied_probability(price: float) -> float:
    """
    Convierte el precio de un contrato binario en probabilidad implícita.

    En Polymarket, el precio del token YES oscila entre 0 y 1 (cents USD).
    price=0.60 significa que el mercado asigna 60% de prob al evento YES.

    Args:
        price: Precio del contrato entre 0.0 y 1.0.

    Returns:
        Probabilidad implícita entre 0.0 y 1.0.

    Raises:
        ValueError: Si el precio está fuera del rango válido.
    """
    if not (0.0 < price < 1.0):
        raise ValueError(f"Precio inválido para contrato binario: {price}")
    # En Polymarket, precio = probabilidad directamente (mercado eficiente asume esto)
    return float(price)


# ---------------------------------------------------------------------------
# PROBABILIDAD DIRECCIONAL ESTIMADA
# ---------------------------------------------------------------------------

def estimated_directional_probability(
    momentum: MomentumResult,
    volatility: VolatilityRegime,
    base_probability: float = 0.50,
) -> tuple[float, float]:
    """
    Estima las probabilidades de YES (BTC sube) y NO (BTC baja) basándose
    en el momentum del precio spot y el régimen de volatilidad.

    Usa un modelo simple bayesiano:
      - Base rate: 50/50 en mercados eficientes
      - Ajuste por momentum: señal direccional del spot
      - Ajuste por volatilidad: escala la confianza

    Args:
        momentum: Resultado del cálculo de momentum.
        volatility: Régimen de volatilidad actual.
        base_probability: Probabilidad base de cada evento (default 0.50).

    Returns:
        Tupla (prob_yes, prob_no) que suman 1.0.
    """
    # Factor de señal: cuánto nos alejamos del 50% por unidad de momentum
    # Calibrado conservadoramente: 1% de momentum → ~5% de ajuste de prob
    MOMENTUM_SENSITIVITY = 5.0

    # Ajuste de volatilidad: a mayor volatilidad, menor confianza en la señal
    # vol_regime "medium" → confianza plena; "high"/"low" → confianza reducida
    vol_confidence = {
        "low": 0.3,
        "medium": 1.0,
        "high": 0.5,
    }.get(volatility.regime, 0.5)

    # Ajuste bruto de probabilidad basado en momentum
    raw_adjustment = momentum.value * MOMENTUM_SENSITIVITY * vol_confidence

    # Clamp para evitar probabilidades extremas (máx ajuste ±15%)
    adjustment = max(-0.15, min(0.15, raw_adjustment))

    prob_yes = base_probability + adjustment
    prob_no = 1.0 - prob_yes

    # Asegurar probabilidades válidas
    prob_yes = max(0.01, min(0.99, prob_yes))
    prob_no = max(0.01, min(0.99, prob_no))

    # Re-normalizar para garantizar que suman exactamente 1.0
    total = prob_yes + prob_no
    prob_yes /= total
    prob_no /= total

    logger.debug(
        "Prob estimada | YES=%.4f NO=%.4f | adj=%.4f | vol_conf=%.2f",
        prob_yes, prob_no, adjustment, vol_confidence,
    )
    return prob_yes, prob_no


# ---------------------------------------------------------------------------
# CÁLCULO DE EDGE
# ---------------------------------------------------------------------------

def probability_edge(implied: float, estimated: float) -> float:
    """
    Calcula el edge como diferencia entre probabilidad estimada e implícita.

    Edge positivo: nuestra estimación es mayor que la del mercado → oportunidad.
    Edge negativo: el mercado nos tiene desventaja → no operar.

    Args:
        implied: Probabilidad implícita del precio de mercado.
        estimated: Probabilidad estimada por el modelo.

    Returns:
        Edge como fracción (ej: 0.03 = 3%).
    """
    edge = estimated - implied
    logger.debug("Edge calculado: %.4f (estimado=%.4f, implícito=%.4f)", edge, estimated, implied)
    return edge


# ---------------------------------------------------------------------------
# VALIDACIÓN DE CONDICIONES DE MERCADO
# ---------------------------------------------------------------------------

def validate_trade_conditions(
    orderbook: Orderbook,
    volatility: Optional[VolatilityRegime],
) -> MarketConditions:
    """
    Valida si las condiciones del mercado cumplen los filtros de entrada.

    Verifica:
      - Spread ≤ max_spread_pct
      - |Imbalance| ≥ min_imbalance_pct
      - Régimen de volatilidad = "medium"

    Args:
        orderbook: Snapshot actual del orderbook.
        volatility: Régimen de volatilidad calculado.

    Returns:
        MarketConditions con flags booleanos y valores actuales.
    """
    cfg = CONFIG.thresholds

    # --- Spread ---
    spread = orderbook.spread
    spread_ok = (spread is not None) and (spread <= cfg.max_spread_pct)

    # --- Imbalance ---
    imbalance = orderbook.imbalance
    imbalance_ok = (imbalance is not None) and (abs(imbalance) >= cfg.min_imbalance_pct)

    # --- Volatilidad ---
    vol_ok = (volatility is not None) and volatility.is_tradeable
    vol_sigma = volatility.sigma if volatility else None
    vol_regime = volatility.regime if volatility else None

    conditions = MarketConditions(
        spread_ok=spread_ok,
        imbalance_ok=imbalance_ok,
        vol_ok=vol_ok,
        spread_value=spread,
        imbalance_value=imbalance,
        vol_sigma=vol_sigma,
        vol_regime=vol_regime,
    )

    logger.debug(
        "Condiciones | spread_ok=%s(%.4f) imbalance_ok=%s(%.4f) vol_ok=%s(%s)",
        spread_ok, spread or 0,
        imbalance_ok, imbalance or 0,
        vol_ok, vol_regime or "N/A",
    )
    return conditions


# ---------------------------------------------------------------------------
# ANÁLISIS COMPLETO DE EDGE
# ---------------------------------------------------------------------------

def analyze_edge(
    orderbook: Orderbook,
    momentum: Optional[MomentumResult],
    volatility: Optional[VolatilityRegime],
) -> EdgeResult:
    """
    Función principal del módulo: analiza si existe edge operable.

    Flujo:
      1. Valida condiciones de mercado (spread, imbalance, vol)
      2. Calcula probabilidad implícita del precio de mercado
      3. Estima probabilidad direccional desde momentum
      4. Determina si el edge supera el umbral mínimo

    Args:
        orderbook: Orderbook actual del mercado.
        momentum: Momentum spot calculado.
        volatility: Régimen de volatilidad actual.

    Returns:
        EdgeResult con toda la información de decisión.
    """
    cfg = CONFIG.thresholds

    # -- Verificar que tenemos todos los datos necesarios --
    if momentum is None:
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason="momentum_unavailable",
        )

    if volatility is None:
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason="volatility_unavailable",
        )

    # -- Validar condiciones de mercado --
    conditions = validate_trade_conditions(orderbook, volatility)

    if not conditions.spread_ok:
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason=f"spread_too_wide({conditions.spread_value:.4f})",
        )

    if not conditions.imbalance_ok:
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason=f"imbalance_too_low({abs(conditions.imbalance_value or 0):.4f})",
        )

    if not conditions.vol_ok:
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason=f"vol_regime_{conditions.vol_regime}",
        )

    # -- Precio mid del orderbook como referencia de probabilidad implícita --
    mid = orderbook.mid_price
    if mid is None or not (0.01 < mid < 0.99):
        return EdgeResult(
            implied_prob=0.0, estimated_prob=0.0, edge=0.0,
            direction="NONE", has_edge=False,
            rejection_reason=f"invalid_mid_price({mid})",
        )

    # -- Calcular probabilidades --
    impl_yes = implied_probability(mid)
    impl_no = 1.0 - impl_yes

    est_yes, est_no = estimated_directional_probability(momentum, volatility)

    # -- Edge para cada dirección --
    edge_yes = probability_edge(implied=impl_yes, estimated=est_yes)
    edge_no = probability_edge(implied=impl_no, estimated=est_no)

    # -- Seleccionar la dirección con mayor edge positivo --
    min_edge = cfg.min_edge_pct

    if edge_yes >= edge_no and edge_yes >= min_edge:
        best_edge = edge_yes
        direction = "YES"
        estimated_prob = est_yes
        implied_prob = impl_yes
    elif edge_no > edge_yes and edge_no >= min_edge:
        best_edge = edge_no
        direction = "NO"
        estimated_prob = est_no
        implied_prob = impl_no
    else:
        best_edge = max(edge_yes, edge_no)
        logger.info(
            "Sin edge suficiente | edge_yes=%.4f edge_no=%.4f umbral=%.4f",
            edge_yes, edge_no, min_edge,
        )
        return EdgeResult(
            implied_prob=impl_yes,
            estimated_prob=est_yes,
            edge=best_edge,
            direction="NONE",
            has_edge=False,
            rejection_reason=f"edge_insuficiente({best_edge:.4f}<{min_edge:.4f})",
        )

    logger.info(
        "EDGE DETECTADO | dir=%s edge=%.4f impl=%.4f est=%.4f",
        direction, best_edge, implied_prob, estimated_prob,
    )

    return EdgeResult(
        implied_prob=implied_prob,
        estimated_prob=estimated_prob,
        edge=best_edge,
        direction=direction,
        has_edge=True,
    )
