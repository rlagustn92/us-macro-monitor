# -*- coding: utf-8 -*-
"""
시차 분석 · CPI 서프라이즈 계산 (화면/데이터 수집과 분리된 순수 함수, test_history.py)
================================================================
모든 월간 시계열은 PeriodIndex('M') 로 맞춰서 다룸.

  find_peaks        지표의 정점 찾기 (앞뒤 window개월 중 최고 + 이후 충분히 하락)
  latest_peak       아직 확정 안 된 최근 사이클의 잠정 정점
  after_peak        정점 이후 자산의 바닥까지 걸린 개월 수 / 낙폭 / 수익률
  lag_corr          지표 변화와 L개월 뒤 주가 수익률의 상관계수 (L<0 이면 주가가 먼저)
  surprises         클리블랜드 연준 JSON -> 발표일별 나우캐스트 vs 실제
================================================================
"""

import pandas as pd


def find_peaks(s, window=12, min_level=None, min_drop=0.0):
    """앞뒤 window개월 안에서 최고값이고, 이후 window개월 안에 min_drop 이상 내려간 지점.
    양쪽 window가 다 채워진 정점만 (= 확정된 정점)."""
    s = s.dropna()
    out = []
    for i in range(window, len(s) - window):
        v = s.iloc[i]
        seg = s.iloc[i - window: i + window + 1]
        if v < seg.max() or (seg == v).idxmax() != s.index[i]:   # 동률이면 첫 지점만
            continue
        if min_level is not None and v < min_level:
            continue
        if v - s.iloc[i + 1: i + window + 1].min() < min_drop:
            continue
        out.append(s.index[i])
    return out


def latest_peak(s, window=12, min_drop=0.0):
    """최근 window개월 안의 최고점이 '잠정 정점'인지. 최소 3개월 지났고 그 뒤 min_drop 이상 하락.
    반환: 정점 시점 또는 None"""
    s = s.dropna()
    recent = s.iloc[-window:]
    p = recent.idxmax()
    after = s[s.index > p]
    if len(after) >= 3 and recent.max() - after.min() >= min_drop:
        return p
    return None


def after_peak(asset, peak, horizon=24):
    """정점 월 종가를 기준으로 이후 horizon개월. 데이터가 모자라면 있는 만큼만."""
    a = asset.dropna()
    if peak not in a.index:
        return None
    p0 = a[peak]
    fut = a[(a.index > peak) & (a.index <= peak + horizon)]
    if fut.empty:
        return None
    trough = fut.idxmin()

    def ret(n):
        t = peak + n
        return (a[t] / p0 - 1) * 100 if t in a.index else None

    # 정점 앞뒤 12개월 중 자산 고점이 언제였나: 음수 = 주가가 지표보다 먼저 꺾임
    around = a[(a.index >= peak - 12) & (a.index <= peak + 12)]
    return dict(months_to_trough=(trough - peak).n, trough=trough,
                drawdown=(fut.min() / p0 - 1) * 100, ret6=ret(6), ret12=ret(12),
                top_offset=(around.idxmax() - peak).n, complete=len(fut) >= horizon)


def lag_corr(x_change, asset, lags=range(-12, 25), fwd=3):
    """x_change(t) 와 (t+L ~ t+L+fwd) 자산 수익률의 상관계수.
    음수 = 지표가 오르면 L개월 뒤 주가가 약했다. L<0 = 주가가 지표보다 먼저 움직임."""
    r = (asset.shift(-fwd) / asset - 1)
    out = {}
    for L in lags:
        j = pd.concat([x_change, r.shift(-L)], axis=1).dropna()
        out[L] = j.iloc[:, 0].corr(j.iloc[:, 1]) if len(j) > 24 else float("nan")
    return pd.Series(out)


def surprises(nowcast_json):
    """클리블랜드 연준 nowcast_year.json -> 발표별 표.
    대상월 / 발표일 / 발표 직전 나우캐스트 / 실제 / 서프라이즈(실제 - 나우캐스트, %p)"""
    rows = []
    for e in nowcast_json:
        try:
            y, m = map(int, e["chart"]["subcaption"].split("-"))
        except ValueError:
            continue

        def pts(name):
            s = next((s for s in e["dataset"] if s["seriesname"] == name), None)
            return [(x["tooltext"].split("{br}")[1], float(x["value"]))
                    for x in (s or {"data": []})["data"] if x.get("value")]

        now, act = pts("CPI Inflation"), pts("Actual CPI Inflation")
        if not now or not act:
            continue                          # 미발표(셧다운 2025.10 등) 또는 아직 발표 전
        md, actual = act[-1]
        rm, rd = map(int, md.split("/"))
        ry = y + 1 if rm < m else y           # 12월분은 이듬해 1월 발표
        rows.append(dict(target=pd.Period(year=y, month=m, freq="M"),
                         release=pd.Timestamp(ry, rm, rd),
                         nowcast=now[-1][1], actual=actual,
                         surprise=actual - now[-1][1]))
    return pd.DataFrame(rows).sort_values("release").reset_index(drop=True)


def release_reaction(daily, dates):
    """발표일 당일 종가 / 직전 거래일 종가 - 1 (%). 금리(^TNX 등)는 bp 차이로 따로 계산."""
    out = {}
    for d in dates:
        if d not in daily.index:
            out[d] = None
            continue
        i = daily.index.get_loc(d)
        out[d] = (daily.iloc[i] / daily.iloc[i - 1] - 1) * 100 if i > 0 else None
    return pd.Series(out, dtype=float)
