# src/services/sector_peers_analysis_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import os
import pandas as pd
import numpy as np

from modules.services.company_profile_service import get_company_profile
from modules.data_provider.fmp_company_screener_adapter import fetch_company_screener
from modules.data_provider.fmp_key_metrics_adapter import fetch_key_metrics_ttm


def _get_fmp_key() -> str:
    return (os.getenv("FMP_API_KEY") or "").strip()


@dataclass(frozen=True)
class SectorPeersPanel:
    symbol: str
    company_name: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    peers_limit: int

    peers_table: pd.DataFrame
    value_quality_rank: pd.DataFrame
    value_quality_top: pd.DataFrame
    value_quality_alt: pd.DataFrame

    roic_rank: pd.DataFrame
    roic_top: pd.DataFrame
    roic_alt: pd.DataFrame
    roic_stats: Dict[str, float]


def build_sector_peers_panel(
    *,
    symbol: str,
    peers_limit: int = 20,
    exchanges_allow: Tuple[str, str] = ("NYSE", "NASDAQ"),
    debt_warn: float = 3.0,
    liq_soft: float = 1.0,
    w_value: float = 0.55,
    w_quality: float = 0.45,
    pen_liq: float = 0.05,
    pen_debt: float = 0.05,
    fmp_api_key: Optional[str] = None,
) -> SectorPeersPanel:
    sym = (symbol or "").strip().upper()
    key = (fmp_api_key or _get_fmp_key()).strip()

    empty_cols = [
        "symbol", "companyName", "exchangeShortName",
        "marketCap", "enterpriseValueTTM",
        "evToSalesTTM", "evToOperatingCashFlowTTM", "evToFreeCashFlowTTM", "evToEBITDATTM",
        "netDebtToEBITDATTM", "currentRatioTTM",
        "returnOnInvestedCapitalTTM", "freeCashFlowYieldTTM",
    ]

    empty = SectorPeersPanel(
        symbol=sym,
        company_name=None,
        sector=None,
        industry=None,
        peers_limit=int(peers_limit),
        peers_table=pd.DataFrame(columns=empty_cols),
        value_quality_rank=pd.DataFrame(),
        value_quality_top=pd.DataFrame(),
        value_quality_alt=pd.DataFrame(),
        roic_rank=pd.DataFrame(),
        roic_top=pd.DataFrame(),
        roic_alt=pd.DataFrame(),
        roic_stats={},
    )

    if not sym or not key:
        return empty

    profile = get_company_profile(sym)
    sector = getattr(profile, "sector", None)
    industry = getattr(profile, "industry", None)
    company_name = getattr(profile, "name", None) or getattr(profile, "company_name", None)

    if not sector or not industry:
        return SectorPeersPanel(**{**empty.__dict__, "company_name": company_name, "sector": sector, "industry": industry})

    raw = fetch_company_screener(sector=sector, industry=industry, api_key=key, limit=max(80, int(peers_limit) * 4))

    rows: List[Dict[str, Any]] = []
    for it in raw:
        exch = str(it.get("exchangeShortName") or "").strip().upper()
        if exch and exch not in exchanges_allow:
            continue
        rows.append(it)

    df_scr = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["symbol", "companyName", "exchangeShortName"])

    peers: List[str] = [sym]
    for s in df_scr.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().tolist():
        if s and s not in peers:
            peers.append(s)
        if len(peers) >= int(peers_limit):
            break

    name_map = {}
    exch_map = {}
    for _, r in df_scr.iterrows():
        ss = str(r.get("symbol") or "").strip().upper()
        if not ss:
            continue
        if ss not in name_map:
            nm = r.get("companyName") or r.get("name")
            if nm:
                name_map[ss] = str(nm)
        if ss not in exch_map:
            ex = r.get("exchangeShortName")
            if ex:
                exch_map[ss] = str(ex)

    km_rows: List[Dict[str, Any]] = []
    for s in peers:
        try:
            km = fetch_key_metrics_ttm(symbol=s, api_key=key)
            d = dict(km.data or {})
        except Exception:
            d = {"symbol": s}

        km_rows.append(
            {
                "symbol": s,
                "companyName": name_map.get(s) or (company_name if s == sym else None),
                "exchangeShortName": exch_map.get(s),
                "marketCap": d.get("marketCap"),
                "enterpriseValueTTM": d.get("enterpriseValueTTM"),
                "evToSalesTTM": d.get("evToSalesTTM"),
                "evToOperatingCashFlowTTM": d.get("evToOperatingCashFlowTTM"),
                "evToFreeCashFlowTTM": d.get("evToFreeCashFlowTTM"),
                "evToEBITDATTM": d.get("evToEBITDATTM"),
                "netDebtToEBITDATTM": d.get("netDebtToEBITDATTM"),
                "currentRatioTTM": d.get("currentRatioTTM"),
                "returnOnInvestedCapitalTTM": d.get("returnOnInvestedCapitalTTM"),
                "freeCashFlowYieldTTM": d.get("freeCashFlowYieldTTM"),
            }
        )

    peers_table = pd.DataFrame(km_rows)

    df = peers_table.copy()
    df["evToEBITDATTM"] = pd.to_numeric(df["evToEBITDATTM"], errors="coerce")
    df["roic"] = pd.to_numeric(df["returnOnInvestedCapitalTTM"], errors="coerce")
    df["netDebtToEBITDATTM"] = pd.to_numeric(df["netDebtToEBITDATTM"], errors="coerce")
    df["currentRatioTTM"] = pd.to_numeric(df["currentRatioTTM"], errors="coerce")

    rank_vq = df.loc[(df["evToEBITDATTM"] > 0) & df["roic"].notna()].copy()
    if not rank_vq.empty:
        value_score = 1.0 - rank_vq["evToEBITDATTM"].rank(pct=True, ascending=True)
        quality_score = rank_vq["roic"].rank(pct=True, ascending=True)

        penalty = 0.0
        penalty = penalty + np.where(rank_vq["currentRatioTTM"].notna() & (rank_vq["currentRatioTTM"] < liq_soft), pen_liq, 0.0)
        penalty = penalty + np.where(rank_vq["netDebtToEBITDATTM"].notna() & (rank_vq["netDebtToEBITDATTM"] > debt_warn), pen_debt, 0.0)

        rank_vq["value_score"] = value_score
        rank_vq["quality_score"] = quality_score
        rank_vq["penalty"] = penalty
        rank_vq["score"] = (w_value * rank_vq["value_score"]) + (w_quality * rank_vq["quality_score"]) - rank_vq["penalty"]

        rank_vq = rank_vq.sort_values("score", ascending=False).reset_index(drop=True)
        top_vq = rank_vq.head(5).copy()
        alt_vq = rank_vq.iloc[5:7].copy()
    else:
        top_vq = pd.DataFrame()
        alt_vq = pd.DataFrame()

    rank_roic = df.loc[df["roic"].notna()].copy()
    if not rank_roic.empty:
        rank_roic = rank_roic.sort_values(["roic"], ascending=[False]).reset_index(drop=True)
        top_ro = rank_roic.head(5).copy()
        alt_ro = rank_roic.iloc[5:7].copy()

        roic_series = rank_roic["roic"].dropna()
        stats = {
            "roic_median": float(roic_series.median()),
            "roic_p75": float(roic_series.quantile(0.75)),
            "roic_min": float(roic_series.min()),
            "roic_max": float(roic_series.max()),
            "n_ranked": float(len(roic_series)),
        }
    else:
        top_ro = pd.DataFrame()
        alt_ro = pd.DataFrame()
        stats = {}

    return SectorPeersPanel(
        symbol=sym,
        company_name=company_name,
        sector=sector,
        industry=industry,
        peers_limit=int(peers_limit),
        peers_table=peers_table,
        value_quality_rank=rank_vq,
        value_quality_top=top_vq,
        value_quality_alt=alt_vq,
        roic_rank=rank_roic,
        roic_top=top_ro,
        roic_alt=alt_ro,
        roic_stats=stats,
    )