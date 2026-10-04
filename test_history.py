# -*- coding: utf-8 -*-
"""history 검증.   실행:  py -3.13 -m pytest test_history.py -q"""

import numpy as np
import pandas as pd

from history import after_peak, find_peaks, lag_corr, latest_peak, release_reaction, surprises

IDX = pd.period_range("2000-01", periods=60, freq="M")


def tri(peak_at, n=60, top=5.0):
    """peak_at 월에 정점인 삼각형 시계열"""
    return pd.Series([top - abs(i - peak_at) * 0.1 for i in range(n)], index=IDX[:n])


def test_find_peaks_single():
    assert find_peaks(tri(30), window=12) == [IDX[30]]


def test_find_peaks_filters():
    s = tri(30)
    assert find_peaks(s, window=12, min_level=6.0) == []      # 수준 미달
    assert find_peaks(s, window=12, min_drop=2.0) == []       # 이후 하락폭 1.2 < 2.0
    assert find_peaks(tri(5), window=12) == []                # 앞쪽 12개월이 없어 미확정


def test_latest_peak():
    s = tri(52)                                               # 마지막 60개월 중 52번째 정점, 이후 7개월
    assert latest_peak(s, window=12, min_drop=0.5) == IDX[52]
    assert latest_peak(tri(58), window=12) is None            # 정점 뒤 1개월뿐


def test_after_peak():
    asset = pd.Series(100.0, index=IDX)
    asset.iloc[31:40] = [95, 90, 85, 80, 85, 90, 95, 100, 105]
    r = after_peak(asset, IDX[30], horizon=24)
    assert r["months_to_trough"] == 4 and round(r["drawdown"]) == -20
    assert round(r["ret6"]) == -10
    assert r["top_offset"] == 9               # 정점 9개월 뒤(105)가 앞뒤 12개월 중 최고

    early = pd.Series(100.0, index=IDX)
    early.iloc[26] = 120                      # 정점 4개월 전에 주가 고점
    assert after_peak(early, IDX[30])["top_offset"] == -4


def test_lag_corr_finds_lag():
    """지표 변화가 6개월 뒤 주가 하락으로 이어지게 만든 가짜 데이터"""
    rng = np.random.default_rng(0)
    idx = pd.period_range("1990-01", periods=360, freq="M")
    x = pd.Series(rng.normal(0, 1, 360), index=idx)
    rets = -0.03 * x.shift(6).fillna(0) + rng.normal(0, 0.005, 360)
    asset = pd.Series(100 * np.exp(np.cumsum(rets.values)), index=idx)
    c = lag_corr(x, asset, lags=range(0, 13), fwd=1)
    assert c.idxmin() == 5        # (t+5 ~ t+6) 수익률 = 6개월 뒤 반응
    assert c.min() < -0.5


def test_surprises_and_reaction():
    def series(name, pts):
        return {"seriesname": name, "data": [
            {"value": str(v), "tooltext": f"{name}{{br}}{d}{{br}}{v}{{br}}"} for d, v in pts]}
    js = [
        {"chart": {"subcaption": "2025-12"}, "dataset": [
            series("CPI Inflation", [("12/01", 2.9), ("01/12", 2.57)]),
            series("Actual CPI Inflation", [("01/13", 2.68)])]},
        {"chart": {"subcaption": "2025-10"}, "dataset": [      # 셧다운: 실제치 없음
            series("CPI Inflation", [("10/01", 2.95)]), series("Actual CPI Inflation", [])]},
    ]
    df = surprises(js)
    assert len(df) == 1
    assert df.release[0] == pd.Timestamp(2026, 1, 13)         # 12월분은 이듬해 발표
    assert round(df.surprise[0], 2) == 0.11

    px = pd.Series([100.0, 102.0], index=pd.to_datetime(["2026-01-12", "2026-01-13"]))
    assert round(release_reaction(px, [pd.Timestamp(2026, 1, 13)]).iloc[0], 1) == 2.0
