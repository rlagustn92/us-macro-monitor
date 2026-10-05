# -*- coding: utf-8 -*-
"""
미국 물가·금리 신호 모니터  (+ 반도체 / WTI / 나스닥 트라이팟)
================================================================
로컬 실행:   streamlit run app.py        (또는 대시보드_실행.bat 더블클릭)
신호 규칙:   macro_rules.py  (macro_monitor_spec.md 3~6장, 테스트: test_macro_rules.py)

데이터 출처 (모두 무료, API 키 불필요)
  - 시세 (약 10~15분 지연): Yahoo Finance  ^IRX ^FVX ^TNX ^TYX CL=F ^SOX 반도체 종목 ^NDX ^VIX
  - 국채 수익률 곡선 전 만기 / TIPS 실질금리: 미 재무부 (매 영업일 장 마감 후)
  - CPI · 실업률: 미 노동통계국 BLS (월 1회)  /  실패 시 FRED  /  그것도 실패하면 수동 입력
================================================================
"""

import io
import json
import re
import urllib.request
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import history
import macro_rules as mr
import nasdaq_tripod as tp

st.set_page_config(page_title="물가·금리 신호 모니터", page_icon="📈", layout="wide")

UA = {"User-Agent": "Mozilla/5.0"}
KST = "Asia/Seoul"

# Yahoo 티커. ^TNX 등은 수익률 그 자체(%)로 나옴 (예전처럼 ÷10 할 필요 없음)
RT_YIELDS = {"^IRX": "3개월", "^FVX": "5년", "^TNX": "10년", "^TYX": "30년"}
WTI, SOX = "CL=F", "^SOX"
LEVERAGE = ["SOXL", "TQQQ"]
LONG_ASSETS = {"^GSPC": "S&P500", "^NDX": "나스닥100", "^SOX": "반도체(SOX)"}

SEMI_ETFS = ["SOXL", "SOXX", "SMH"]
SEMI_STOCKS = ["NVDA", "AVGO", "TSM", "AMD", "MU", "ASML", "AMAT", "LRCX", "QCOM", "INTC"]
NAMES = {"^SOX": "필라델피아 반도체지수", "SOXL": "SOXL (3배)", "SOXX": "SOXX", "SMH": "SMH",
         "NVDA": "엔비디아", "AVGO": "브로드컴", "TSM": "TSMC", "AMD": "AMD", "MU": "마이크론",
         "ASML": "ASML", "AMAT": "어플라이드머티리얼즈", "LRCX": "램리서치", "QCOM": "퀄컴",
         "INTC": "인텔", "TQQQ": "TQQQ (3배)"}

# 2026 FOMC 결정일 (연준 공표 일정). 이후 일정은 사이드바에서 직접 입력
FOMC = [date(2026, m, d) for m, d in
        ((1, 28), (3, 18), (4, 29), (6, 17), (7, 29), (9, 16), (10, 28), (12, 9))]
NEXT_CPI = date(2026, 10, 14)   # 스펙 7-2

DISCLAIMER = ("이 대시보드는 특정 인터뷰 영상의 프레임워크와 개인적으로 정한 휴리스틱 규칙을 "
              "시각화한 것이며, 투자 자문이 아닙니다. 임계값은 검증된 정답이 아니며, "
              "최종 판단과 책임은 본인에게 있습니다.")


# ================================================================
# 색 / 차트 공통
# ================================================================
def theme():
    dark = getattr(getattr(st.context, "theme", None), "type", None) == "dark"
    if dark:
        return dict(series=["#3987e5", "#d95926", "#199e70", "#c98500"],
                    text="#ffffff", text2="#c3c2b7", muted="#898781",
                    grid="#2c2c2a", axis="#383835", surface="#1a1a19",
                    border="rgba(255,255,255,0.10)")
    return dict(series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
                text="#0b0b0b", text2="#52514e", muted="#898781",
                grid="#e1e0d9", axis="#c3c2b7", surface="#fcfcfb",
                border="rgba(11,11,11,0.10)")


def line_chart(series, ysuffix="%", height=320, yfmt=".2f", label_ends=True,
               zero_line=False, xcat=False, markers=False, intraday=False, refs=None):
    """series: [(이름, pd.Series)]  — 순서대로 고정 색 배정.
    2개 이상이면 범례 + 선 끝 직접 라벨, 마우스 올리면 세로선 + 툴팁.
    refs: [(y, 라벨)] 기준 수평선"""
    c = theme()
    fig = go.Figure()
    for i, (name, s) in enumerate(series):
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=name,
            mode="lines+markers" if markers else "lines",
            line=dict(width=2, color=c["series"][i]),
            marker=dict(size=8, line=dict(width=2, color=c["surface"])),
            hovertemplate=f"%{{y:{yfmt}}}{ysuffix}"))
        if label_ends and len(series) > 1 and len(s.dropna()):
            s2 = s.dropna()
            fig.add_annotation(x=s2.index[-1], y=s2.iloc[-1], xanchor="left", xshift=6,
                               text=f"{name} {s2.iloc[-1]:{yfmt}}{ysuffix}",
                               showarrow=False, font=dict(size=11, color=c["text2"]))
    if zero_line:
        fig.add_hline(y=0, line=dict(color=c["axis"], width=1))
    for y, lab in refs or []:
        fig.add_hline(y=y, line=dict(color=c["muted"], width=1, dash="dot"),
                      annotation_text=lab, annotation_position="top left",
                      annotation_font=dict(size=11, color=c["text2"]))
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=110 if (label_ends and len(series) > 1) else 8, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif',
                  color=c["text2"], size=12),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=c["surface"], font_color=c["text"], bordercolor=c["border"]),
        showlegend=len(series) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None,
                    font=dict(color=c["text2"])),
        xaxis=dict(showgrid=False, linecolor=c["axis"], tickfont=dict(color=c["muted"]),
                   type="category" if xcat else None, automargin=True,
                   showspikes=True, spikemode="across", spikecolor=c["axis"],
                   spikethickness=1, spikedash="solid"),
        yaxis=dict(gridcolor=c["grid"], gridwidth=1, zeroline=False,
                   tickfont=dict(color=c["muted"]), ticksuffix=ysuffix, automargin=True),
    )
    if intraday:   # 장 닫힌 시간(밤·주말)을 접어서 거래 구간만 이어 붙임
        idx = series[0][1].index
        missing = pd.date_range(idx.min(), idx.max(), freq="5min").difference(idx)
        fig.update_xaxes(rangebreaks=[dict(values=missing, dvalue=5 * 60 * 1000)],
                         tickformat="%m/%d %H:%M")
    return fig


def mark_extrema(fig, s, slot=0):
    """정점·바닥 마커 (스펙 8-1④)."""
    c = theme()
    for lab, ts, dy in (("정점", s.idxmax(), 16), ("바닥", s.idxmin(), -16)):
        fig.add_trace(go.Scatter(
            x=[ts], y=[s[ts]], mode="markers", showlegend=False, hoverinfo="skip",
            marker=dict(size=10, color=c["series"][slot], line=dict(width=2, color=c["surface"]))))
        fig.add_annotation(x=ts, y=s[ts], yshift=dy, showarrow=False,
                           text=f"{lab} {s[ts]:.1f}%", font=dict(size=11, color=c["text2"]))
    return fig


def bar_chart(s, height=340, yfmt="+.2f", xlab="", ylab="", hover_x=""):
    """단일 계열 막대 (양/음 같은 색, 0 기준선). s.index = x"""
    c = theme()
    fig = go.Figure(go.Bar(
        x=list(s.index), y=s.values, marker=dict(color=c["series"][0], line=dict(width=0)),
        hovertemplate=f"{hover_x}%{{x}}<br>%{{y:{yfmt}}}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=c["axis"], width=1))
    fig.update_layout(
        height=height, margin=dict(l=60, r=16, t=28, b=64), bargap=0.25,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif',
                  color=c["text2"], size=12),
        hoverlabel=dict(bgcolor=c["surface"], font_color=c["text"], bordercolor=c["border"]),
        xaxis=dict(title=dict(text=xlab, standoff=12), showgrid=False, linecolor=c["axis"],
                   tickfont=dict(color=c["muted"]), automargin=True, dtick=3),
        yaxis=dict(title=ylab, gridcolor=c["grid"], zeroline=False, tickformat="+.2f",
                   tickfont=dict(color=c["muted"]), automargin=True))
    return fig


def show(fig):
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


def bp(x):
    """%p 단위 차이 -> bp (0.04 -> +4bp)"""
    return f"{x * 100:+.0f}bp"


# ---------------------------------------------------------------- 한눈에 보기 부품
ZONE_RGB = {"good": "12,163,12", "warning": "250,178,25", "serious": "236,131,90",
            "critical": "208,59,59", "neutral": "127,127,127"}
ZONE_ICON = {"good": "🟢", "warning": "🟡", "serious": "🟠", "critical": "🔴", "neutral": "⚪"}


def _zone_of(v, zones):
    return next((z for z in zones if v < z[0]), zones[-1])


def gauge_html(title, what, value, vfmt, zones, lo, hi, note="", ghost=None, foot=""):
    """온도계 막대: 구간(색 + 글자) 위에 현재값 핀, 예상값은 점선 핀.
    zones = [(상한, 상태키, 쉬운 말), ...] 오름차순. 색만으로 구분하지 않도록 글자를 같이 씀."""
    c = theme()
    pos = lambda v: max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))
    cur = _zone_of(value, zones)
    segs, ticks, prev = "", "", lo
    for ub, key, lab in zones:
        u = min(ub, hi)
        on = (ub, key, lab) == cur
        segs += (f'<div style="width:{pos(u) - pos(prev):.2f}%;background:rgba({ZONE_RGB[key]},'
                 f'{.42 if on else .16});display:flex;align-items:center;justify-content:center;'
                 f'font-size:11px;{"font-weight:700;" if on else ""}color:{c["text"] if on else c["text2"]};'
                 f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 2px">{lab}</div>')
        if u < hi:
            ticks += (f'<span style="position:absolute;left:{pos(u):.2f}%;transform:translateX(-50%);'
                      f'font-size:10px;color:{c["muted"]}">{vfmt.format(u).rstrip("%p").rstrip("%")}</span>')
        prev = u
    pin = (f'<div style="position:absolute;left:{pos(value):.2f}%;top:-5px;bottom:-5px;width:3px;'
           f'margin-left:-1.5px;background:{c["text"]};border-radius:2px"></div>')
    if ghost is not None:
        pin += (f'<div title="예상" style="position:absolute;left:{pos(ghost):.2f}%;top:-5px;bottom:-5px;'
                f'border-left:2px dashed {c["text2"]};margin-left:-1px"></div>')
    return (
        f'<div style="border:1px solid {c["border"]};border-radius:10px;padding:10px 14px 8px;'
        f'background:rgba(127,127,127,.04)">'
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">'
        f'<div><div style="font-weight:700;font-size:.95rem">{title}</div>'
        f'<div style="font-size:.75rem;color:{c["muted"]}">{what}</div></div>'
        f'<div style="text-align:right;white-space:nowrap"><span style="font-size:1.55rem;font-weight:800">'
        f'{vfmt.format(value)}</span><br><span style="font-size:.85rem;font-weight:600">'
        f'{ZONE_ICON[cur[1]]} {cur[2]}</span></div></div>'
        f'<div style="position:relative;margin:8px 0 2px"><div style="display:flex;height:24px;'
        f'border-radius:6px;overflow:hidden">{segs}</div>{pin}</div>'
        f'<div style="position:relative;height:14px">{ticks}</div>'
        f'<div style="font-size:.8rem;color:{c["text2"]};margin-top:2px">{note}</div>'
        + (f'<div style="font-size:.76rem;color:{c["muted"]};border-top:1px solid {c["border"]};'
           f'margin-top:6px;padding-top:5px">{foot}</div>' if foot else "") + '</div>')


def takeaway(lines, title="📌 한 줄 요약"):
    """탭 맨 위 큰 결론 박스. lines = [문장, ...] (마크다운 굵게 ** 대신 <b> 사용)"""
    c = theme()
    body = "".join(f'<div style="margin:.15rem 0">{s}</div>' for s in lines)
    st.markdown(
        f'<div style="border-left:6px solid {c["series"][0]};background:rgba(42,120,214,.07);'
        f'border-radius:8px;padding:12px 16px;margin:4px 0 14px;font-size:1.08rem;line-height:1.55">'
        f'<div style="font-size:.85rem;font-weight:700;color:{c["text2"]};margin-bottom:4px">{title}</div>'
        f'{body}</div>', unsafe_allow_html=True)


def stage_badge(key, title="현재 신호 단계", size="2rem"):
    s = mr.STAGES[key]
    st.markdown(
        f'<div style="border-left:8px solid {s["color"]};padding:.55rem 1rem;'
        f'border-radius:6px;background:rgba(127,127,127,.08)">'
        f'<div style="font-size:.85rem;opacity:.7">{title}</div>'
        f'<div style="font-size:{size};font-weight:700;line-height:1.25">'
        f'{s["icon"]} {s["label"]}</div></div>', unsafe_allow_html=True)


# ================================================================
# 데이터 수집  (전부 1시간 캐시, 화면의 갱신 버튼으로 즉시 초기화)
# ================================================================
def _get(url, timeout=30, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")


def _close(df):
    """yf.download 결과에서 Close만, 티커가 열이 되도록."""
    c = df["Close"]
    return c.to_frame() if isinstance(c, pd.Series) else c


@st.cache_data(ttl=3600, show_spinner=False)
def yf_daily():
    """금리 4종 + WTI + SOX + 레버리지 일봉 1년. 장중엔 오늘 봉이 '현재가'."""
    df = _close(yf.download(list(RT_YIELDS) + [WTI, SOX] + LEVERAGE, period="1y",
                            interval="1d", progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def yf_intraday():
    df = _close(yf.download(list(RT_YIELDS) + [WTI, SOX], period="5d", interval="5m",
                            progress=False, auto_adjust=False))
    idx = pd.to_datetime(df.index)
    df.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(KST)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def tnx_recent_high():
    """최근 1개월 10년물 장중 최고치 (재무부 자료는 종가뿐이라 Yahoo 고가 사용)."""
    d = yf.download("^TNX", period="1mo", interval="1d", progress=False, auto_adjust=False)
    h = d["High"]
    return float((h.iloc[:, 0] if isinstance(h, pd.DataFrame) else h).max())


@st.cache_data(ttl=3600, show_spinner=False)
def semis(tickers):
    df = _close(yf.download([SOX] + list(tickers), period="2y", interval="1d",
                            progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


TSY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           "daily-treasury-rates.csv/{y}/all?type={t}&field_tdr_date_value={y}&page&_format=csv")


def _maturity_months(col):
    m = re.match(r"\s*([\d.]+)\s*(mo|month|yr|year)", col, re.I)
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2).lower().startswith("mo") else n * 12


def _maturity_kr(months):
    if months < 12:
        return f"{months:g}개월"
    return f"{months / 12:g}년"


@st.cache_data(ttl=3600, show_spinner=False)
def treasury(kind="daily_treasury_yield_curve", years=3):
    """미 재무부 공식 수익률 곡선. 열 = 만기(개월, 숫자), 행 = 날짜."""
    this = date.today().year
    frames = []
    for y in range(this - years + 1, this + 1):
        txt = _get(TSY_URL.format(y=y, t=kind))
        if txt.strip().count("\n") >= 1:
            frames.append(pd.read_csv(io.StringIO(txt)))
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    df = df.set_index("Date").sort_index()
    df.columns = [_maturity_months(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c is not None]]
    return df[sorted(df.columns)].apply(pd.to_numeric, errors="coerce")


@st.cache_data(ttl=3 * 3600, show_spinner=False)   # BLS 무료 API 하루 25회 제한 -> 3시간
def macro():
    """월간 지표. 1순위 BLS, 실패하면 FRED.
    CPI·근원 = 원계열(전년비용, BLS 발표 기준) / _SA = 계절조정(전월비용) / 실업률 = 계절조정"""
    ids = {"CUUR0000SA0": "CPI", "CUUR0000SA0L1E": "근원 CPI",
           "CUSR0000SA0": "CPI_SA", "CUSR0000SA0L1E": "근원_SA", "LNS14000000": "실업률"}
    this = date.today().year
    try:
        body = json.dumps({"seriesid": list(ids), "startyear": str(this - 5),
                           "endyear": str(this)}).encode()
        res = json.loads(_get("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                              data=body, headers={"Content-Type": "application/json"}))
        if res.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(res.get("message"))
        out = {}
        for s in res["Results"]["series"]:
            # 미발표 달은 값이 "-" (예: 2025.10 셧다운) -> NaN 으로 두고 월 인덱스는 유지
            pts = {pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1):
                   pd.to_numeric(p["value"], errors="coerce")
                   for p in s["data"] if p["period"].startswith("M") and p["period"] != "M13"}
            out[ids[s["seriesID"]]] = pd.Series(pts, dtype=float).sort_index()
        return pd.DataFrame(out), "BLS (미 노동통계국)"
    except Exception:
        out = {}
        for sid, name in (("CPIAUCSL", "CPI"), ("CPILFESL", "근원 CPI"), ("UNRATE", "실업률")):
            d = pd.read_csv(io.StringIO(_get(
                f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=15)))
            d.iloc[:, 0] = pd.to_datetime(d.iloc[:, 0])
            out[name] = pd.to_numeric(d.set_index(d.columns[0]).iloc[:, 0], errors="coerce")
        df = pd.DataFrame(out)
        df["CPI_SA"], df["근원_SA"] = df["CPI"], df["근원 CPI"]   # FRED 쪽은 계절조정 계열
        return df[df.index >= pd.Timestamp(this - 5, 1, 1)], "FRED"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def cpi_history():
    """시차 분석용 장기 CPI 지수 (원계열, 1986~). PeriodIndex('M').
    FRED 키가 있으면 FRED 한 번에, 없으면 BLS를 10년씩 나눠서 (v1 한 번에 10년 제한)."""
    key = _fred_key()
    if key:
        r = json.loads(_get("https://api.stlouisfed.org/fred/series/observations"
                            f"?series_id=CPIAUCNS&api_key={key}&file_type=json"
                            "&observation_start=1986-01-01"))
        s = pd.Series({pd.Period(o["date"][:7], "M"): pd.to_numeric(o["value"], errors="coerce")
                       for o in r["observations"]})
        return s.sort_index(), "FRED"
    try:
        pts, this = {}, date.today().year
        for a in range(1986, this + 1, 10):
            body = json.dumps({"seriesid": ["CUUR0000SA0"], "startyear": str(a),
                               "endyear": str(min(a + 9, this))}).encode()
            res = json.loads(_get("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                                  data=body, headers={"Content-Type": "application/json"}))
            if res.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError(res.get("message"))
            for p in res["Results"]["series"][0]["data"]:
                if p["period"].startswith("M") and p["period"] != "M13":
                    pts[pd.Period(year=int(p["year"]), month=int(p["period"][1:]), freq="M")] = \
                        pd.to_numeric(p["value"], errors="coerce")
        return pd.Series(pts, dtype=float).sort_index(), "BLS"
    except Exception:   # BLS 하루 한도 초과 등 -> FRED 공개 CSV (키 불필요)
        d = pd.read_csv(io.StringIO(_get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS&cosd=1986-01-01",
            timeout=20)))
        s = pd.to_numeric(d.iloc[:, 1], errors="coerce")
        s.index = pd.PeriodIndex(pd.to_datetime(d.iloc[:, 0]), freq="M")
        return s, "FRED"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def monthly_px():
    """S&P500 · 나스닥100 · 반도체 · 10년물 월말 종가 (가능한 전체 기간). PeriodIndex('M')."""
    df = _close(yf.download(list(LONG_ASSETS) + ["^TNX"], period="max", interval="1mo",
                            progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None).to_period("M")
    return df[~df.index.duplicated(keep="last")]


@st.cache_data(ttl=3600, show_spinner=False)
def daily_px_since_2013():
    """CPI 발표일 반응 계산용 일봉 (나우캐스트 기록이 2013-08부터)."""
    df = _close(yf.download(list(LONG_ASSETS) + ["^TNX"], start="2013-07-01", interval="1d",
                            progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def tripod_data():
    df = _close(yf.download(["^NDX", "^VIX"], period="10y", interval="1d",
                            progress=False, auto_adjust=False))
    df = df.rename(columns={"^NDX": "ndx", "^VIX": "vix"})[["ndx", "vix"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return tp.drop_unclosed_bar(df)


@st.cache_data(ttl=3600, show_spinner=False)
def fetched_at():
    """데이터 받은 시각. 다른 캐시와 같이 1시간마다 / 갱신 버튼으로 초기화."""
    return pd.Timestamp.now(tz=KST)


# ---------------------------------------------------------------- 사이드바 자동값
@st.cache_data(ttl=3600, show_spinner=False)
def nowcast_raw():
    """클리블랜드 연준 인플레이션 나우캐스트 원본 (2013-08~, 대상월별 일간 예측치 + 실제치)."""
    return json.loads(_get("https://www.clevelandfed.org/-/media/files/webcharts/"
                           "inflationnowcasting/nowcast_year.json?sc_lang=en", timeout=60)
                      .lstrip("﻿"))


def cleveland_nowcast(target):
    """target = '2026-9' 형식의 대상 월.
    반환: (CPI 전년비, 근원 전년비, 기준일 'MM/DD') — 해당 월 항목이 없으면 None"""
    for e in nowcast_raw():
        if e["chart"]["subcaption"] != target:
            continue
        def last(name):
            # 날짜는 categories 대신 tooltext("CPI Inflation{br}10/02{br}3.6...")에서 읽음
            # (categories엔 'PCE Aug' 같은 발표 표시가 섞여 있어 인덱스가 어긋남)
            s = next(s for s in e["dataset"] if s["seriesname"] == name)["data"]
            pts = [(x["tooltext"].split("{br}")[1], float(x["value"])) for x in s if x.get("value")]
            return pts[-1] if pts else (None, None)

        asof, cpi_now = last("CPI Inflation")
        _, core_now = last("Core CPI Inflation")
        return (cpi_now, core_now, asof) if cpi_now is not None else None
    return None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fomc_dates():
    """연준 공식 FOMC 일정 페이지에서 결정일(회의 마지막 날) 목록."""
    h = _get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    mon = {m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"], 1)}
    out = []
    for ym in re.finditer(r"(\d{4}) FOMC Meetings", h):
        nxt = h.find("FOMC Meetings", ym.end())
        seg = h[ym.end(): nxt if nxt > 0 else None]
        for m in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>'
                             r'.*?fomc-meeting__date[^>]*>([^<]+)<', seg, re.S):
            name = m.group(1).strip().split("/")[-1].lower()   # "Apr/May" -> may
            days = re.findall(r"\d+", m.group(2))
            if name in mon and days:
                out.append(date(int(ym.group(1)), mon[name], int(days[-1])))
    return sorted(set(out))


@st.cache_data(ttl=3600, show_spinner=False)
def fed_target():
    """뉴욕 연준 EFFR 이력에서 기준금리 목표 범위 변경 이력. [(적용일, 하단, 상단), ...]"""
    start = date.today() - timedelta(days=800)
    r = json.loads(_get("https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
                        f"?startDate={start}&endDate={date.today()}"))
    rows = sorted((x["effectiveDate"], x["targetRateFrom"], x["targetRateTo"])
                  for x in r["refRates"])
    changes, prev = [], None
    for d, lo, hi in rows:
        if prev is not None and (lo, hi) != prev:
            changes.append((date.fromisoformat(d), lo, hi, hi - prev[1]))
        prev = (lo, hi)
    return changes, prev


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def spy_pe():
    return yf.Ticker("SPY").info.get("trailingPE")


def _fred_key():
    try:
        return st.secrets.get("FRED_API_KEY")
    except Exception:          # secrets 파일 자체가 없을 때
        return None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def release_dates(key):
    """CPI / 고용보고서 발표일. FRED API 키가 있을 때만 (BLS 일정 페이지는 봇 차단).
    FRED release id: 10 = CPI, 50 = Employment Situation
    반환: {"CPI": 다음 발표일, "JOBS": ..., "CPI_last": 직전 발표일, "JOBS_last": ...}"""
    out, today_ = {}, date.today()
    for name, rid in (("CPI", 10), ("JOBS", 50)):
        r = json.loads(_get(
            "https://api.stlouisfed.org/fred/release/dates"
            f"?release_id={rid}&api_key={key}&file_type=json&sort_order=asc"
            f"&include_release_dates_with_no_data=true"
            f"&realtime_start={today_ - timedelta(days=120)}"))
        ds = sorted(date.fromisoformat(x["date"]) for x in r.get("release_dates", []))
        out[name] = next((d for d in ds if d >= today_), None)
        out[name + "_last"] = next((d for d in reversed(ds) if d < today_), None)
    return out


def surprise_frame():
    """CPI 발표별 나우캐스트 vs 실제 + 발표 당일 S&P500·나스닥·SOX(%)·10년물(bp) 반응."""
    sp = history.surprises(nowcast_raw())
    dpx = daily_px_since_2013()
    for t in LONG_ASSETS:
        sp[t] = history.release_reaction(dpx[t].dropna(), sp["release"]).values
    tnx = dpx["^TNX"].dropna()
    sp["10년물"] = [(tnx.iloc[tnx.index.get_loc(d)] - tnx.iloc[tnx.index.get_loc(d) - 1]) * 100
                   if d in tnx.index else None for d in sp["release"]]
    return sp


@st.cache_data(ttl=3600, show_spinner=False)
def energy_daily():
    """휘발유 선물(RBOB, $/갤런) · WTI 일봉 6개월. CPI 에너지 항목의 방향을 보는 용도."""
    df = _close(yf.download(["RB=F", WTI], period="6mo", interval="1d",
                            progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[df.index.dayofweek < 5]          # 일요일 저녁 선물 행 제외


def safe(fn, what, quiet=False):
    try:
        return fn()
    except Exception as e:
        if not quiet:
            st.warning(f"{what} 데이터를 불러오지 못했습니다: {e}")
        return None


def parse_list(txt):
    return [float(x) for x in re.split(r"[,\s]+", txt.strip()) if x]


# ================================================================
# 사이드바 — 설정 (스펙 8-1⑦)
# ================================================================
sb = st.sidebar
sb.header("⚙️ 설정")
sb.caption("새로고침하면 기본값으로 돌아갑니다. 기본값을 바꾸려면 macro_rules.py 의 DEFAULTS 수정")

th = dict(mr.DEFAULTS)
with sb.expander("신호 임계값 (휴리스틱)"):
    th["regime_cpi"] = st.number_input("레짐 기준: CPI 전년비 12개월 평균 (%)",
                                       value=th["regime_cpi"], step=0.1)
    th["cpi_red"] = st.number_input("🔴/🟠 CPI 기준 (%)", value=th["cpi_red"], step=0.1)
    th["y10_green"] = st.number_input("🟢 10년물 상한 (%)", value=th["y10_green"], step=0.05)
    th["y10_strong"] = st.number_input("🟢🟢 10년물 상한 (%)", value=th["y10_strong"], step=0.05)
    high_mode = st.radio("10년물 직전 고점", ["고정값", "52주 최고치 자동 (종가)"],
                         horizontal=True)
    th["y10_high"] = st.number_input("직전 고점 고정값 (%)", value=th["y10_high"], step=0.01,
                                     disabled=high_mode != "고정값")
    th["streak"] = int(st.number_input("연속 개월 수", value=th["streak"], min_value=1, step=1))
    th["mom_warn"] = st.number_input("버블 조건2 경고: CPI 전월비 (%)", value=th["mom_warn"],
                                     step=0.1)
    th["unrate_rise"] = st.number_input("저물가 🔴: 실업률 12개월 최저 대비 상승 (%p)",
                                        value=th["unrate_rise"], step=0.1)
    th["stop_loss"] = st.number_input("레버리지 손절선 (%)", value=th["stop_loss"], step=1.0)
    th["take_profit"] = st.number_input("레버리지 익절 시작 (%)", value=th["take_profit"], step=1.0)

# ---- 아래 값들은 자동으로 채워짐. 사이드바에서 고치면 그 값이 우선 (새로고침하면 다시 자동값)
today = date.today()
mac = safe(macro, "CPI·실업률")
fomc = safe(fomc_dates, "FOMC 일정", quiet=True) or FOMC

# 연준 인상 중단: 마지막 금리 변경 이후 '동결한 FOMC'가 한 번이라도 있었나 (또는 마지막이 인하)
pause_auto, pause_why = False, "자동 판정 실패 → 수동으로 체크"
ft = safe(fed_target, "연준 기준금리", quiet=True)
if ft and ft[0]:
    (chg_d, lo, hi, step), _ = ft[0][-1], ft[1]
    holds = [d for d in fomc if chg_d <= d <= today]
    pause_auto = step < 0 or bool(holds)
    pause_why = (f"자동: {chg_d:%Y-%m-%d} {step * 100:+.0f}bp {'인하' if step < 0 else '인상'} "
                 f"(현재 {lo:.2f}~{hi:.2f}%) 후 " +
                 (f"{holds[-1]:%m/%d} FOMC 동결" if holds else "아직 동결한 회의 없음"))
fed_pause = sb.checkbox("연준 금리 인상 중단 시그널", value=pause_auto, help=pause_why)
sb.caption(pause_why)

# 나우캐스트: 다음 발표될 CPI(최신 발표월 + 1개월) 대상
last_m = (mac[0]["CPI"].dropna().index[-1] if mac is not None
          else pd.Timestamp(today.year, today.month, 1) - pd.DateOffset(months=2))
target = last_m + pd.DateOffset(months=1)
nc = safe(lambda: cleveland_nowcast(f"{target.year}-{target.month}"), "나우캐스트", quiet=True)
nowcast = sb.number_input(
    f"나우캐스트: {target:%Y.%m} CPI 전년비 (%)", step=0.01, placeholder="예: 3.57",
    value=round(nc[0], 2) if nc else None,
    help="클리블랜드 연준 인플레이션 나우캐스트 (매일 갱신)")
sb.caption(f"자동: 클리블랜드 연준 {nc[2]} 기준 · 근원 {nc[1]:.2f}%" if nc
           else "자동 수신 실패 → 직접 입력")

pe = safe(spy_pe, "PER", quiet=True)
fwd_per = sb.number_input("S&P500 PER", value=round(pe, 1) if pe else None, step=0.1,
                          placeholder="예: 22.5",
                          help="이익수익률(1/PER)과 10년물 비교. 선행 PER을 알면 덮어쓰기")
sb.caption("자동: SPY 후행 PER (무료로 받을 수 있는 선행 PER 출처가 없음)" if pe
           else "자동 수신 실패 → 직접 입력")

with sb.expander("레버리지 진입가 (손절·익절 점검)"):
    entries = {t: st.number_input(f"{t} 진입가 ($)", value=None, step=0.01, key=f"entry_{t}")
               for t in LEVERAGE}

with sb.expander("다음 이벤트 일정"):
    key = _fred_key()
    rel = safe(lambda: release_dates(key), "발표 일정", quiet=True) if key else None
    first_fri = lambda y, m: date(y, m, 1) + timedelta(days=(4 - date(y, m, 1).weekday()) % 7)
    jobs = first_fri(today.year, today.month)
    if jobs < today:
        nm = date(today.year + today.month // 12, today.month % 12 + 1, 1)
        jobs = first_fri(nm.year, nm.month)
    cpi_d = (rel or {}).get("CPI") or (NEXT_CPI if NEXT_CPI >= today else None)
    ev_cpi = st.date_input("다음 CPI 발표", value=cpi_d)
    ev_jobs = st.date_input("다음 고용보고서", value=(rel or {}).get("JOBS") or jobs)
    ev_fomc = st.date_input("다음 FOMC 결정", value=next((d for d in fomc if d >= today), None))
    st.caption("FOMC: 연준 공식 일정에서 자동  \n" + (
        "CPI·고용보고서: FRED에서 자동" if rel and rel.get("CPI") and rel.get("JOBS") else
        "CPI·고용보고서: FRED API 키를 넣으면 자동 (없으면 고용은 첫째 금요일로 추정, "
        "CPI는 직접 입력)"))

with sb.expander("반도체 종목"):
    semi_txt = st.text_input("ETF + 종목 (쉼표로 구분)", ", ".join(SEMI_ETFS + SEMI_STOCKS))
semi_list = tuple(dict.fromkeys(t.strip().upper() for t in semi_txt.split(",") if t.strip()))


# ================================================================
# 지표 조립 + 신호 판정
# ================================================================
curve = safe(treasury, "재무부 수익률")
daily = safe(yf_daily, "시세")

if mac is not None:
    mdf, msrc = mac
    cpi_yoy = (mdf["CPI"].pct_change(12, fill_method=None) * 100).round(1).dropna()
    core_yoy = (mdf["근원 CPI"].pct_change(12, fill_method=None) * 100).round(1).dropna()
    unrate = mdf["실업률"].dropna()
    cpi_mom = float((mdf["CPI_SA"].pct_change(fill_method=None) * 100).dropna().iloc[-1])
else:   # 데이터 실패 -> 수동 입력 (스펙 2장)
    cpi_mom = None
    msrc = "수동 입력"
    with sb.expander("⚠️ 수동 입력 (월간 데이터 수신 실패)", expanded=True):
        c_txt = st.text_input("CPI 전년비 (오래된→최신, 쉼표)",
                              "3.0, 2.7, 2.7, 2.4, 2.4, 3.3, 3.8, 4.2, 3.5, 3.4, 3.4")
        u_txt = st.text_input("실업률 (오래된→최신, 쉼표)", "4.1, 4.2, 4.2, 4.3, 4.3, 4.2, 4.1, 4.1, 4.2")
    cpi_yoy, unrate = pd.Series(parse_list(c_txt)), pd.Series(parse_list(u_txt))
    core_yoy = None

if curve is not None:
    y10s = curve[120].dropna()
    y10_src = f"재무부 종가 {y10s.index[-1]:%m/%d}"
elif daily is not None:
    y10s = daily["^TNX"].dropna()
    y10_src = f"Yahoo {y10s.index[-1]:%m/%d}"
else:
    y10s = pd.Series([sb.number_input("⚠️ 10년물 수동 입력 (%)", value=5.28, step=0.01)])
    y10_src = "수동 입력"

y10 = float(y10s.iloc[-1])
if high_mode != "고정값" and len(y10s) > 5:
    prev = y10s.iloc[:-1]
    th["y10_high"] = float(prev[prev.index >= prev.index[-1] - pd.Timedelta(days=365)].max())

ind = dict(cpi=list(cpi_yoy.values), y10=y10, y10_high=th["y10_high"],
           unrate=list(unrate.values), fed_pause=fed_pause, nowcast=nowcast, cpi_mom=cpi_mom)
stage, reasons = mr.judge(ind, th)
regime, regime_why = mr.judge_regime(ind, th)


# ================================================================
# 상단 헤더 (스펙 8-1①)
# ================================================================
head, btn = st.columns([5, 1], vertical_alignment="bottom")
head.title("물가·금리 신호 모니터")
if btn.button("🔄 데이터 갱신", width="stretch"):
    st.cache_data.clear()
    st.rerun()

# ================================================================
# 한눈에 보기 — 초보도 바로: ① 지금 상태 ② 왜 ③ 뭐가 바뀌면 달라지나
# ================================================================
cpi_m = f"{cpi_yoy.index[-1]:%Y.%m}" if hasattr(cpi_yoy.index[-1], "strftime") else "수동"
ps = mr.plain_summary(ind, th, stage, regime)
S, C = mr.STAGES[stage], theme()

# ① 큰 결론 카드: 단계 + 한 줄 결론 + 단계 사다리 + 이유 + 할 일
ladder = mr.LADDER if regime == "HIGH" else ["RED", "YELLOW", "GREEN", "GOLD"]
chips = "".join(
    f'<span style="display:inline-block;padding:3px 9px;margin:2px 3px 2px 0;border-radius:999px;'
    f'font-size:.8rem;border:2px solid {mr.STAGES[k]["color"] if k == stage else C["border"]};'
    f'{"font-weight:800;" if k == stage else "opacity:.5;"}">'
    f'{mr.STAGES[k]["icon"]} {mr.STAGES[k]["label"]}</span>'
    for k in ladder)
why = "".join(f"<li>{r}</li>" for r in ps["reasons"])
hero_l, hero_r = st.columns([1.05, 1.6], gap="medium")
with hero_l:
    st.markdown(
        f'<div style="border:1px solid {C["border"]};border-left:10px solid {S["color"]};'
        f'border-radius:12px;padding:14px 18px">'
        f'<div style="font-size:.85rem;color:{C["text2"]}">지금은 · '
        f'{"🔥 고물가 시대" if regime == "HIGH" else "🧊 저물가 시대"} 규칙 적용</div>'
        f'<div style="font-size:2.6rem;font-weight:800;line-height:1.15;margin-top:2px">'
        f'{S["icon"]} {S["label"]}</div>'
        f'<div style="font-size:1.3rem;font-weight:700;margin:2px 0 8px">→ {ps["headline"]}</div>'
        f'<div style="margin-bottom:8px">{chips}</div>'
        f'<ul style="margin:0 0 10px 1.1rem;padding:0;font-size:.92rem;line-height:1.5">{why}</ul>'
        f'<div style="background:rgba(127,127,127,.08);border-radius:8px;padding:8px 12px;'
        f'font-size:.92rem;line-height:1.55"><b>할 일</b><br>'
        f'레버리지: <b>{ps["lev"]}</b><br>현금: <b>{ps["cash"]}</b><br>'
        f'<span style="color:{C["text2"]}">몸통 적립 · 커버드콜: 지표와 상관없이 그대로</span>'
        f'</div></div>', unsafe_allow_html=True)

# ② 왜? — 지표별 온도계
with hero_r:
    up_n, dn_n = mr.rising_streak(ind["cpi"]), mr.falling_streak(ind["cpi"])
    trend = f"{up_n}개월 연속 상승" if up_n else f"{dn_n}개월 연속 하락" if dn_n else "지난달과 같음"
    cpi_zones = [(th["gold_cpi"], "good", "안정"), (th["cpi_red"], "warning", "높음"),
                 (99, "critical", "위험")]
    nz = (f" · 다음 예상 <b>{nowcast:.2f}%</b> ({ZONE_ICON[_zone_of(nowcast, cpi_zones)[1]]} "
          f"{_zone_of(nowcast, cpi_zones)[2]})" if nowcast is not None else "")
    # 연준 목표 지표(근원 PCE)는 판정엔 안 쓰고 참고 한 줄로만
    pce = safe(lambda: history.latest_actual(nowcast_raw()), "근원 PCE", quiet=True)
    pce_foot = (f"참고 · 연준 기준 근원 PCE <b>{pce[1]:.1f}%</b> ({pce[0].month}월) · 목표 2%"
                if pce else "")
    g = [gauge_html("물가", "CPI 전년비 · 오르면 금리도 못 내려 주식에 불리",
                    ind["cpi"][-1], "{:.1f}%", cpi_zones, 1.0, 6.0,
                    note=f"최근: {trend}{nz} <span style='opacity:.7'>(점선 = 예상)</span>",
                    ghost=nowcast, foot=pce_foot)]
    hi_ = th["y10_high"]
    g.append(gauge_html(
        "금리", "미국 10년물 · 자산시장의 중력, 높을수록 주식·반도체가 무거움",
        y10, "{:.2f}%",
        [(th["y10_strong"], "good", "숨통"), (th["y10_green"], "warning", "보통"),
         (hi_, "serious", "부담"), (99, "critical", "신고가")], 4.0, max(5.8, hi_ + 0.3),
        note=(f"직전 고점 {hi_:.2f}%를 넘음" if y10 >= hi_ else
              f"직전 고점 {hi_:.2f}%까지 <b>{hi_ - y10:.2f}%p</b>")))
    u = ind["unrate"]
    if len(u) >= 4:
        du, aux = round(u[-1] - u[-4], 2), th["unrate_aux"]
        g.append(gauge_html(
            "고용", "실업률 3개월 변화 · " + ("고물가 시대엔 일자리가 식어야 물가가 잡힘"
                                         if regime == "HIGH" else "저물가 시대엔 실업률이 핵심"),
            du, "{:+.1f}%p",
            ([(-aux, "serious", "과열→물가↑"), (aux, "neutral", "중립"), (99, "good", "식는 중→물가↓")]
             if regime == "HIGH" else
             [(-aux, "good", "개선"), (aux, "neutral", "중립"), (99, "critical", "악화")]),
            -0.6, 0.6, note=f"실업률 {u[-1]:.1f}% (3개월 전 {u[-4]:.1f}%)"))
    if fwd_per:
        ey = 100 / fwd_per
        g.append(gauge_html(
            "주식 vs 채권", "주식 이익수익률(1/PER) − 10년물 · 마이너스면 국채가 더 매력",
            ey - y10, "{:+.2f}%p",
            [(-0.5, "serious", "채권 우위"), (0.5, "warning", "비슷"), (99, "good", "주식 우위")],
            -3.0, 3.0, note=f"주식 {ey:.2f}% vs 국채 {y10:.2f}% (PER {fwd_per:.1f})"))
    # PC에선 2x2 고정 (3+1로 어긋나지 않게), 폰에선 한 줄
    st.markdown('<style>.gauges{display:grid;grid-template-columns:1fr 1fr;gap:10px}'
                '@media (max-width:640px){.gauges{grid-template-columns:1fr}}</style>'
                f'<div class="gauges">{"".join(g)}</div>', unsafe_allow_html=True)

# ③ 뭐가 바뀌면 달라지나 — 다음 CPI 결과별 + 단계별 조건
outs = mr.next_cpi_outcomes(ind, th)
nc_r = round(nowcast, 1) if nowcast is not None else None


def _in_range(txt, v):
    nums = [float(x) for x in re.findall(r"\d+\.\d", txt)]
    return ((("이하" in txt) and v <= nums[0]) or (("이상" in txt) and v >= nums[0]) or
            ("~" in txt and nums[0] <= v <= nums[1]) or (len(nums) == 1 and "이" not in txt
                                                        and v == nums[0]))


cards = ""
for txt, k in outs:
    s2 = mr.STAGES[k]
    mark = (f'<div style="font-size:.78rem;margin-top:2px">← 예상 {nowcast:.2f}%</div>'
            if nc_r is not None and _in_range(txt, nc_r) else "")
    cards += (f'<div style="flex:1;min-width:150px;border:1px solid {C["border"]};'
              f'border-top:5px solid {s2["color"]};border-radius:8px;padding:8px 12px">'
              f'<div style="font-size:1.05rem;font-weight:800">CPI {txt}</div>'
              f'<div style="font-size:1rem">→ {s2["icon"]} {s2["label"]}'
              f'{" (유지)" if k == stage else ""}</div>{mark}</div>')
when = f" ({ev_cpi:%m/%d})" if ev_cpi else ""
st.markdown(
    f'<div style="margin-top:14px;font-weight:700">다음 CPI 발표{when} 결과에 따라</div>'
    f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">{cards}</div>',
    unsafe_allow_html=True)

trig = ""
for t in mr.stage_triggers(ind, th, regime):
    if t["stage"] == stage:
        continue
    s2 = mr.STAGES[t["stage"]]
    rows = ""
    for cond, met, now in t["conds"]:
        now_html = f'<span style="color:{C["muted"]}"> · {now}</span>' if now else ""
        rows += f'<div style="margin:2px 0">{"✅" if met else "⬜"} {cond}{now_html}</div>'
    rule = {"그리고": "모두 충족 시", "또는": "하나만 충족해도"}.get(t["logic"], "충족 시")
    trig += (f'<div style="border:1px solid {C["border"]};border-top:4px solid {s2["color"]};'
             f'border-radius:8px;padding:8px 12px;font-size:.85rem">'
             f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
             f'<span style="font-weight:800;font-size:1rem">→ {s2["icon"]} {s2["label"]}</span>'
             f'<span style="color:{C["muted"]};font-size:.78rem">{rule}</span></div>{rows}</div>')
st.markdown(
    f'<div style="margin-top:14px;font-weight:700">단계가 바뀌는 조건 — ✅ 충족 · ⬜ 아직</div>'
    f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;'
    f'margin-top:6px">{trig}</div>', unsafe_allow_html=True)

st.caption(f"데이터 기준 · CPI {cpi_m} ({msrc}) · 10년물 {y10:.2f}% ({y10_src}) · {regime_why} · "
           f"시세 받은 시각 {fetched_at():%m/%d %H:%M} · 1시간마다 갱신 · 투자 권유 아님")
# ================================================================
# CPI 발표 패널 — 평소엔 접힌 한 줄, 누르면 펼침
#   발표 전: 직전 CPI / 클리블랜드 예상(이번 달 흐름) / 에너지 / 실제(발표 후)
#   발표 후 5일간: 방금 나온 결과와 서프라이즈
# ================================================================
_rel_kst = lambda d: pd.Timestamp(d.year, d.month, d.day, 8, 30,
                                  tz="America/New_York").tz_convert(KST)
sp_all = safe(surprise_frame, "CPI 서프라이즈 기록", quiet=True)
latest_p = pd.Period(cpi_yoy.index[-1], "M") if hasattr(cpi_yoy.index[-1], "strftime") else None
last_rel, row_latest = (rel or {}).get("CPI_last"), None
if sp_all is not None and latest_p is not None:
    hit = sp_all[sp_all.target == latest_p]
    if len(hit):
        row_latest = hit.iloc[-1]
        last_rel = last_rel or row_latest.release.date()
just_out = last_rel is not None and 0 <= (today - last_rel).days <= 5

if just_out:
    p_target, prev_cpi, actual = latest_p, cpi_yoy.iloc[-2], cpi_yoy.iloc[-1]
    fc = float(row_latest.nowcast) if row_latest is not None else None
else:
    p_target, prev_cpi, actual = pd.Period(target, "M"), cpi_yoy.iloc[-1], None
    fc = nc[0] if nc else None

# A안 한 줄 (접힌 상태의 제목)
outs = mr.next_cpi_outcomes(ind, th)
risky = " · ".join(f"{txt}이면 {mr.STAGES[k]['icon']} {mr.STAGES[k]['label']}"
                   for txt, k in outs if k != stage)
if just_out:
    line = (f"📅 CPI 발표 완료 ({last_rel:%m/%d}) · {p_target.month}월분 **{actual:.1f}%**"
            + (f" · 예상 {fc:.2f}% 대비 {actual - fc:+.2f}%p" if fc is not None else ""))
else:
    dn = (ev_cpi - today).days if ev_cpi else None
    line = ("📅 CPI " + ("발표일 미정" if dn is None else "오늘 발표" if dn == 0 else f"D-{dn}")
            + (f" · {_rel_kst(ev_cpi):%m/%d %H:%M}" if ev_cpi else "")
            + (f" · {p_target.month}월분 · 예상 **{fc:.2f}%** (직전 {prev_cpi:.1f}%)"
               if fc is not None else f" · {p_target.month}월분 · 클리블랜드 최신 전망 없음"))
    if risky:
        line += f" · {risky}"

with st.expander(line, expanded=False):
    # 클리블랜드 예상치의 이번 달 흐름 (작은 선)
    path = history.nowcast_path(nowcast_raw(), f"{p_target.year}-{p_target.month}") \
        if sp_all is not None else []
    spark = ""
    if len(path) >= 2:
        vs = [v for _, v in path]
        lo_, hi_ = min(vs), max(vs)
        rng_ = (hi_ - lo_) or 1
        pts = " ".join(f"{i / (len(vs) - 1) * 100:.1f},{20 - (v - lo_) / rng_ * 16:.1f}"
                       for i, v in enumerate(vs))
        spark = (f'<svg viewBox="0 0 100 22" preserveAspectRatio="none" width="100%" height="22">'
                 f'<polyline points="{pts}" fill="none" stroke="{C["series"][0]}" stroke-width="2" '
                 f'vector-effect="non-scaling-stroke"/></svg>'
                 f'<div style="font-size:.75rem;color:{C["muted"]}">{path[0][0]} {vs[0]:.2f}% → '
                 f'{path[-1][0]} {vs[-1]:.2f}%</div>')

    # 에너지: 대상월 평균 vs 전월 평균
    en = safe(energy_daily, "휘발유·유가", quiet=True)
    en_rows = ""
    if en is not None:
        mon = en.index.to_period("M")
        for tkr, lab in (("RB=F", "휘발유"), (WTI, "WTI")):
            a = en.loc[mon == p_target, tkr].mean()
            b = en.loc[mon == p_target - 1, tkr].mean()
            if pd.notna(a) and pd.notna(b):
                en_rows += (f'<div style="font-size:1rem;font-weight:700">{lab} '
                            f'{(a / b - 1) * 100:+.1f}%</div>')
    ongoing = " (진행 중)" if p_target == pd.Period(today, "M") else ""

    # 과거 발표일 반도체 반응 (2021~, 고물가 시대)
    react = ""
    if sp_all is not None:
        r21 = sp_all[sp_all.release >= "2021-01-01"]
        hi_r = r21[r21.surprise >= 0.1]["^SOX"].mean()
        lo_r = r21[r21.surprise <= -0.1]["^SOX"].mean()
        react = (f'<div>예상보다 높게 → 평균 <b>{hi_r:+.2f}%</b> ▼</div>'
                 f'<div>예상보다 낮게 → 평균 <b>{lo_r:+.2f}%</b> ▲</div>')

    def cell(title, big, sub, extra=""):
        return (f'<div style="background:rgba(127,127,127,.07);border-radius:8px;padding:10px 12px">'
                f'<div style="font-size:.78rem;color:{C["text2"]}">{title}</div>'
                f'<div style="font-size:1.45rem;font-weight:800;line-height:1.3">{big}</div>{extra}'
                f'<div style="font-size:.75rem;color:{C["muted"]}">{sub}</div></div>')

    cells = (
        cell("직전 CPI", f"{prev_cpi:.1f}%", f"{(p_target - 1).month}월분")
        + cell("클리블랜드 예상", f"{fc:.2f}%" if fc is not None else "없음",
               "발표 직전 최종치" if just_out else "매 영업일 갱신", spark)
        + cell(f"에너지 ({p_target.month}월 평균, 전월 대비){ongoing}", "", "휘발유 = RBOB 선물",
               en_rows or f'<div style="color:{C["muted"]}">자료 없음</div>')
        + cell("실제 발표치", f"{actual:.1f}%" if actual is not None else "—",
               (f"예상 대비 {actual - fc:+.2f}%p" if actual is not None and fc is not None
                else "발표 후 자동")))
    if just_out:
        res = (f'<div>결과: <b>{mr.STAGES[stage]["icon"]} {mr.STAGES[stage]["label"]}</b></div>'
               f'<div style="color:{C["text2"]}">→ {mr.PLAIN[stage][0]}</div>')
    else:
        res = "".join(
            f'<div>{txt} → <b>{mr.STAGES[k]["icon"]} {mr.STAGES[k]["label"]}</b>'
            f'{" (유지)" if k == stage else ""}'
            f'{" ← 예상" if fc is not None and _in_range(txt, round(fc, 1)) else ""}</div>'
            for txt, k in outs)
    box = (f'<div style="border:1px solid {C["border"]};border-radius:8px;padding:8px 12px;'
           f'font-size:.9rem"><div style="font-size:.78rem;color:{C["text2"]};margin-bottom:2px">')
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;'
        f'font-size:.8rem;color:{C["muted"]};margin-bottom:8px"><span>{p_target.month}월분 CPI'
        + (f' · {_rel_kst(ev_cpi):%m/%d %H:%M} 발표' if ev_cpi and not just_out else "")
        + f'</span><span>레버리지 · 대기 현금 판단용 (몸통 적립과 무관)</span></div>'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));'
        f'gap:8px">{cells}</div>'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));'
        f'gap:8px;margin-top:8px">'
        f'{box}{"결과" if just_out else "결과에 따라 단계"}</div>{res}</div>'
        f'{box}과거 발표일 반도체 반응 (2021~)</div>{react or "자료 없음"}</div></div>',
        unsafe_allow_html=True)

st.markdown("#### 시장 시세")

# 시세 타일 — 괄호 = 그 숫자가 몇 시 시세인지 (한국시간, 분 단위)
intra_q = safe(yf_intraday, "장중 시세", quiet=True)


def quote(t):
    """(현재값, 직전 거래일 종가, 시각 문구). 5분봉 마지막 체결이 우선이고 숫자도 그 시각 값.
    5분봉이 없으면 일봉 종가."""
    d = daily[t].dropna() if daily is not None and t in daily else None
    if intra_q is not None and t in intra_q and intra_q[t].notna().any():
        s = intra_q[t].dropna()
        ts = s.index[-1]
        ny = ts.tz_convert("America/New_York")
        # 원유 선물은 18:00(뉴욕)부터 다음 날 거래일로 침
        sess = ny.date() + timedelta(days=1 if t == WTI and ny.hour >= 18 else 0)
        prev = None
        if d is not None:
            # 주말 날짜 행 제외: Yahoo가 일요일 저녁 선물 거래를 '일요일' 일봉으로 넣기도 함
            before = d[(d.index.date < sess) & (d.index.dayofweek < 5)]
            prev = before.iloc[-1] if len(before) else None
        return s.iloc[-1], prev, f"{ts:%m/%d %H:%M}"
    if d is not None and len(d) >= 2:
        return d.iloc[-1], d.iloc[-2], f"{d.index[-1]:%m/%d} 종가"
    return None


def release_kst(d):
    """미국 경제지표 발표 시각(동부 08:30) -> 한국시간. 서머타임 자동 반영."""
    return pd.Timestamp(d.year, d.month, d.day, 8, 30, tz="America/New_York").tz_convert(KST)


# 1줄: 금리 4종 / 2줄: CPI · 다음 CPI 예상 · 실업률 / 3줄: WTI · 반도체
# (괄호에 시각이 붙어 라벨이 길어서 2·3줄은 칸을 넓게)
row1, row2, row3 = st.columns(4), st.columns(3), st.columns(2)
cols = row1 + [row2[0], row2[2], row3[0], row3[1]]   # 4=CPI 5=실업률 6=WTI 7=SOX
for i, (t, name) in enumerate(RT_YIELDS.items()):
    q = quote(t)
    if q:
        v, p, when = q
        cols[i].metric(f"미국채 {name} ({when})", f"{v:.3f}%",
                       bp(v - p) if p is not None else None, delta_color="off", border=True)

# 클리블랜드 연준 다음 CPI 예상 (매일 갱신, 분 단위 시각은 공개 안 됨 -> 날짜까지)
if nc:
    row2[1].metric(
        f"다음 CPI 예상 · 클리블랜드 연준 ({target:%Y.%m}분 · {nc[2]} 기준)", f"{nc[0]:.2f}%",
        f"{nc[0] - cpi_yoy.iloc[-1]:+.2f}%p vs 최신 CPI", delta_color="off", border=True,
        help=f"근원 CPI 예상 {nc[1]:.2f}% · 출처: Cleveland Fed Inflation Nowcasting (매 영업일 갱신)")
else:
    row2[1].metric(f"다음 CPI 예상 · 클리블랜드 연준 ({target:%Y.%m}분)", "최신 전망 없음",
                   "아직 안 나왔거나 받아오지 못함", delta_color="off", border=True)

for col, t, lab in ((cols[6], WTI, "WTI $"), (cols[7], SOX, "반도체 SOX")):
    q = quote(t)
    if q:
        v, p, when = q
        col.metric(f"{lab} ({when})", f"{v:,.2f}",
                   f"{(v / p - 1) * 100:+.2f}%" if p is not None else None,
                   delta_color="off", border=True)

# CPI · 실업률: 월간 지표라 분 단위 '시세' 대신 발표 시각
cpi_rel = (rel or {}).get("CPI_last")
if cpi_rel is None and hasattr(cpi_yoy.index[-1], "strftime"):
    sp_all = safe(lambda: history.surprises(nowcast_raw()), "CPI 발표일", quiet=True)
    if sp_all is not None:
        hit = sp_all[sp_all.target == pd.Period(cpi_yoy.index[-1], "M")]
        cpi_rel = hit.release.iloc[-1].date() if len(hit) else None
cpi_when = (f"{cpi_m}분 · {release_kst(cpi_rel):%m/%d %H:%M} 발표" if cpi_rel
            else f"{cpi_m}분" if cpi_m != "수동" else "수동 입력")
cols[4].metric(f"CPI 전년比 ({cpi_when})", f"{cpi_yoy.iloc[-1]:.1f}%",
               f"{cpi_yoy.iloc[-1] - cpi_yoy.iloc[-2]:+.1f}%p", delta_color="off", border=True)

if hasattr(unrate.index[-1], "strftime"):
    um_ = unrate.index[-1]
    job_rel, est = (rel or {}).get("JOBS_last"), ""
    if job_rel is None:   # 키 없으면 '다음 달 첫째 금요일'로 추정
        nm_ = (um_ + pd.DateOffset(months=1)).date()
        job_rel, est = first_fri(nm_.year, nm_.month), " 추정"
    u_when = f"{um_:%Y.%m}분 · {release_kst(job_rel):%m/%d %H:%M} 발표{est}"
else:
    u_when = "수동 입력"
cols[5].metric(f"실업률 ({u_when})", f"{unrate.iloc[-1]:.1f}%",
               f"{unrate.iloc[-1] - unrate.iloc[-2]:+.1f}%p", delta_color="off", border=True)
st.caption("괄호 = 그 숫자가 몇 시 시세인지 (한국시간, 5분봉 마지막 체결 · Yahoo는 원래 10~15분 늦게 들어옴) · "
           "CPI·실업률은 월 1회 지표라 발표 시각 · 변화는 직전 거래일 종가 대비")

(t_signal, t_lag, t_surp, t_rate, t_infl, t_semi, t_oil, t_tri, t_help) = st.tabs(
    ["🧭 판정 근거", "📊 시차 분석", "🎯 CPI 서프라이즈", "국채 금리", "물가·고용", "반도체",
     "WTI 유가", "나스닥 트라이팟", "도움말"])

# ---------------------------------------------------------------- 신호 판정
with t_signal:
    # ② 지금 할 일은 맨 위 '한눈에 보기'에. 여기선 판정 근거를 자세히
    st.caption("몸통은 지표와 무관하게 매달 적립. 신호 규칙은 레버리지와 대기 현금에만 적용 · "
               "하락 베팅(인버스·공매도)은 하지 않음")

    with st.expander("단계별 액션 표 전체"):
        st.dataframe(pd.DataFrame(
            [(mr.STAGES[k]["icon"] + " " + mr.STAGES[k]["label"]
              + ("  ◀ 현재" if k == stage else ""),) + mr.ACTIONS[k]
             for k in ["RED", "ORANGE", "YELLOW", "GREEN", "GREEN2"]],
            columns=["단계", "몸통 적립", "커버드콜", "레버리지 10%", "대기 현금"]),
            hide_index=True, width="stretch")

    # ③ 버블 체크리스트 + 판정 근거
    left, right = st.columns(2)
    with left:
        st.markdown("**버블 붕괴 조건** (두 개 동시 충족 시 붕괴했던 패턴)")
        rh = safe(tnx_recent_high, "10년물 고가", quiet=True)
        for name, mark, txt in mr.bubble_checklist(ind, th, recent_high=rh):
            with st.container(border=True):
                st.markdown(f"{mark} **{name}**  \n{txt}")
    with right:
        st.markdown("**판정 근거**")
        st.code("\n".join(reasons), language=None, wrap_lines=True)

    # ④ 차트
    st.markdown("**CPI 전년비 — 최근 24개월** (정점·바닥 표시)")
    if hasattr(cpi_yoy.index[-1], "strftime"):
        cut = cpi_yoy.index[-1] - pd.DateOffset(months=23)
        cs = [("CPI", cpi_yoy[cpi_yoy.index >= cut])]
        if core_yoy is not None:
            cs.append(("근원 CPI", core_yoy[core_yoy.index >= cut]))
        fig = line_chart(cs, yfmt=".1f", markers=True,
                         refs=[(th["cpi_red"], f"{th['cpi_red']:.1f}% (🔴/🟠 기준)")])
        show(mark_extrema(fig, cs[0][1]))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**미국 10년물 금리 — 최근 1년** ({y10_src})")
        if len(y10s) > 5:
            ys = y10s[y10s.index >= y10s.index[-1] - pd.Timedelta(days=365)]
            show(line_chart([("10년물", ys)], refs=[
                (th["y10_high"], f"직전 고점 {th['y10_high']:.2f}%"),
                (th["y10_green"], f"🟢 {th['y10_green']:.1f}%"),
                (th["y10_strong"], f"🟢🟢 {th['y10_strong']:.1f}%")]))
    with c2:
        st.markdown("**실업률 — 최근 36개월**")
        if hasattr(unrate.index[-1], "strftime"):
            show(line_chart([("실업률", unrate[unrate.index >= unrate.index[-1]
                                                - pd.DateOffset(months=35)])], yfmt=".1f"))

    # ⑤ 일정
    st.markdown("**다음 이벤트**")
    ev = st.columns(3)
    for col, lab, d in zip(ev, ["CPI 발표", "고용보고서 (실업률)", "FOMC"],
                           [ev_cpi, ev_jobs, ev_fomc]):
        if d:
            n = (d - today).days
            col.metric(lab, f"{d:%Y-%m-%d}", "오늘" if n == 0 else f"D-{n}" if n > 0 else
                       f"{-n}일 지남", delta_color="off", border=True)
        else:
            col.metric(lab, "미정", "사이드바에서 입력", delta_color="off", border=True)

    # ⑥ 시나리오 계산기
    st.markdown("**CPI 시나리오 계산기** — 다음 CPI가 이렇게 나오면?")
    s1, s2 = st.columns([1, 2])
    with s1:
        guess = st.number_input("다음 CPI 전년비 (%)", step=0.1,
                                value=float(nowcast if nowcast is not None else cpi_yoy.iloc[-1]))
        g_stage, g_why = mr.scenario(ind, th, guess)
        stage_badge(g_stage, title=f"CPI {round(guess, 1):.1f}% 발표 시", size="1.5rem")
        st.caption("발표치처럼 소수 1자리로 반올림해서 판정")
    with s2:
        rows = []
        for v in [x / 10 for x in range(int(cpi_yoy.iloc[-1] * 10) - 5,
                                         int(cpi_yoy.iloc[-1] * 10) + 6)]:
            k, _ = mr.scenario(ind, th, v)
            rows.append((f"{v:.1f}%", f"{mr.STAGES[k]['icon']} {mr.STAGES[k]['label']}",
                         "  /  ".join(mr.ACTIONS[k][2:])))
        st.dataframe(pd.DataFrame(rows, columns=["다음 CPI", "단계", "레버리지 / 대기 현금"]),
                     hide_index=True, width="stretch", height=300)
    with st.expander("판정 근거 (시나리오)"):
        st.code("\n".join(g_why), language=None, wrap_lines=True)

    # ⑧ 보조 지표 + 레버리지 점검
    st.markdown("**보조 지표**")
    a1, a2, a3 = st.columns(3)
    with a1.container(border=True):
        side, txt = mr.unemployment_aux(list(unrate.values), th)
        icon = {"GREEN": "🟢", "ORANGE": "🟠"}.get(side, "⚪")
        st.markdown(f"{icon} **실업률 보조 신호**" + (" (고물가 시대 해석)" if regime == "HIGH" else ""))
        st.caption(txt + " · 단독으로 단계를 바꾸지 않음")
    with a2.container(border=True):
        if fwd_per:
            ey = 100 / fwd_per
            st.markdown(f"{'🟠' if ey < y10 else '🟢'} **주식 이익수익률 {ey:.2f}% vs 10년물 {y10:.2f}%**")
            st.caption("이익수익률 < 10년물 → 중력 신호: 주식 상대 매력 낮음" if ey < y10
                       else "이익수익률 ≥ 10년물 → 주식 상대 매력 유지")
        else:
            st.markdown("⚪ **주식 이익수익률 vs 10년물**")
            st.caption("사이드바에 S&P500 PER을 넣으면 계산")
    with a3.container(border=True):
        st.markdown("**🟢에서 산 물량 청산 조건** (하나라도 나오면)")
        st.caption("  \n".join(f"{mr.ok(hit)} {txt}" for hit, txt in mr.exit_check(ind, th)))

    st.markdown("**레버리지 손절·익절 점검**")
    lv = st.columns(len(LEVERAGE))
    for col, t in zip(lv, LEVERAGE):
        with col.container(border=True):
            px = daily[t].dropna().iloc[-1] if daily is not None else None
            if entries[t] and px:
                ret, flag, txt = mr.leverage_check(px, entries[t], th)
                icon = {"STOP": "🔴", "TAKE": "🟢"}.get(flag, "⚪")
                st.markdown(f"{icon} **{t}** \\${px:,.2f} · 진입가 \\${entries[t]:,.2f} "
                            f"대비 **{ret:+.1f}%**")   # \\$ : $ 두 개가 수식으로 해석되는 것 방지
                st.caption(txt)
            else:
                st.markdown(f"⚪ **{t}**" + (f" \\${px:,.2f}" if px else ""))
                st.caption("사이드바 '레버리지 진입가'에 입력하면 손절선/익절 구간 표시")

# ---------------------------------------------------------------- 시차 분석
def _ts(s):
    """PeriodIndex -> 월초 Timestamp (plotly용)"""
    s = s.copy()
    s.index = s.index.to_timestamp()
    return s


with t_lag:
    st.markdown("지표가 정점을 찍거나 튄 뒤 주가가 **몇 개월 뒤에** 반응했는지 과거 데이터로 봅니다. "
                "정점 사례가 5~9번뿐이라 **경향을 보는 용도**이지 규칙이 아닙니다.")
    hist = safe(cpi_history, "장기 CPI")
    mpx = safe(monthly_px, "장기 시세")
    if hist is not None and mpx is not None:
        cpi_idx, hsrc = hist
        IND = {  # 이름: (시계열, 정점 조건)
            "CPI 전년비": ((cpi_idx.pct_change(12, fill_method=None) * 100).dropna(),
                         dict(min_level=3.0, min_drop=1.0)),
            "10년물 금리": (mpx["^TNX"].dropna().loc[pd.Period("1987-01", "M"):],
                         dict(min_drop=0.75)),
        }
        c1, c2 = st.columns(2)
        ind_name = c1.segmented_control("지표", list(IND), default="CPI 전년비",
                                        key="lag_ind") or "CPI 전년비"
        asset = c2.segmented_control("자산", list(LONG_ASSETS), default="^SOX", key="lag_asset",
                                     format_func=LONG_ASSETS.get) or "^SOX"
        x, kw = IND[ind_name]
        pa = mpx[asset].dropna()
        peaks = history.find_peaks(x, 12, **kw)
        cur = history.latest_peak(x, 12, kw["min_drop"] * 0.5)
        if cur in peaks:
            cur = None
        box = st.empty()     # 📌 한 줄 요약 자리 (아래에서 계산 후 채움)

        # 위: 지표 + 정점 / 아래: 자산 (같은 기간, 정점 세로선)
        cth = theme()

        def add_peak_lines(fig, on=None):
            for p in peaks + ([cur] if cur else []):
                fig.add_vline(x=p.to_timestamp(), line=dict(color=cth["muted"], width=1,
                                                             dash="dash" if p == cur else "dot"))
                if on is not None:
                    fig.add_trace(go.Scatter(
                        x=[p.to_timestamp()], y=[on[p]], mode="markers", showlegend=False,
                        hovertemplate=f"{'잠정 ' if p == cur else ''}정점 {p}<br>%{{y:.2f}}%<extra></extra>",
                        marker=dict(size=10, color=cth["series"][0],
                                    line=dict(width=2, color=cth["surface"]))))
            return fig

        st.markdown(f"**{ind_name}** · 점 = 정점 (점선 세로줄), 파선 = 아직 확정 안 된 최근 정점")
        show(add_peak_lines(line_chart([(ind_name, _ts(x))], height=250), on=x))
        st.markdown(f"**{LONG_ASSETS[asset]}** (로그 스케일, 같은 세로줄)")
        f2 = add_peak_lines(line_chart([(LONG_ASSETS[asset], _ts(pa.loc[x.index[0]:]))],
                                       ysuffix="", yfmt=",.0f", height=250))
        f2.update_yaxes(type="log")
        show(f2)

        rows = []
        for p in peaks + ([cur] if cur else []):
            r = history.after_peak(pa, p)
            if r is None:
                rows.append({"지표 정점": str(p), "정점 값": x[p], "비고": "자산 데이터 없음"})
                continue
            rows.append({
                "지표 정점": str(p), "정점 값": x[p],
                "주가 고점 (정점 대비)": r["top_offset"],
                "바닥까지 (개월)": r["months_to_trough"],
                "정점 대비 최저": r["drawdown"], "6개월 뒤": r["ret6"], "12개월 뒤": r["ret12"],
                "비고": "잠정 (진행 중)" if p == cur else ("" if r["complete"] else "24개월 미경과"),
            })
        tbl = pd.DataFrame(rows)
        for col in ["주가 고점 (정점 대비)", "바닥까지 (개월)", "정점 대비 최저", "6개월 뒤", "12개월 뒤"]:
            if col in tbl:   # 아직 안 지난 기간(None)이 'None' 글자로 보이지 않게 숫자로
                tbl[col] = pd.to_numeric(tbl[col], errors="coerce")
        pct = st.column_config.NumberColumn(format="%+.1f%%")
        st.dataframe(tbl, hide_index=True, width="stretch", column_config={
            "정점 값": st.column_config.NumberColumn(format="%.2f%%"),
            "주가 고점 (정점 대비)": st.column_config.NumberColumn(
                format="%+d개월", help="정점 앞뒤 12개월 중 주가 최고점 시점. 음수 = 주가가 먼저 꺾임"),
            "정점 대비 최저": pct, "6개월 뒤": pct, "12개월 뒤": pct})

        # 요약은 확정된(24개월 지난) 정점만
        done = (tbl[(tbl["비고"] == "") & tbl["바닥까지 (개월)"].notna()]
                if "바닥까지 (개월)" in tbl else tbl.iloc[0:0])
        an = LONG_ASSETS[asset]

        st.markdown(f"**시차별 상관계수** — {ind_name}의 6개월 변화와, 그 L개월 뒤부터 3개월간 "
                    f"{an} 수익률의 관계")
        corr = history.lag_corr(x.diff(6), pa, lags=range(-12, 25), fwd=3)
        after, before = corr[corr.index >= 0], corr[corr.index < 0]
        L_min, c_min = int(after.idxmin()), after.min()
        L_lead = int(before.abs().idxmax())
        fig = bar_chart(corr, xlab="지표가 변한 시점(0) 기준 개월 수",
                        ylab="상관계수 (− = 주가 약세)", hover_x="L = ")
        fig.add_vline(x=0, line=dict(color=cth["text2"], width=1, dash="dot"))
        ymax = float(corr.abs().max()) * 1.25
        fig.update_yaxes(range=[-ymax, ymax])
        for xx, txt, anc in ((-0.6, "← 주가가 지표보다 먼저 움직인 구간", "right"),
                             (0.6, "지표가 변한 뒤 주가 반응 →", "left")):
            fig.add_annotation(x=xx, y=ymax * 0.97, text=txt, showarrow=False, xanchor=anc,
                               font=dict(size=11, color=cth["text2"]))
        fig.add_annotation(x=L_min, y=c_min, text=f"가장 약함: {L_min}개월 뒤", showarrow=True,
                           arrowhead=0, ay=28, font=dict(size=11, color=cth["text"]))
        show(fig)
        st.caption("막대가 아래로 길수록 '지표가 오르면 그만큼 뒤에 주가가 약했다'는 뜻. "
                   "±0.2 안쪽은 약한 관계이고, 겹치는 구간으로 계산해서 실제보다 강해 보일 수 있음 · "
                   f"CPI 출처 {hsrc}")

        # 📌 한 줄 요약 (맨 위 자리에 채움)
        strength = lambda v: ("거의 없음" if abs(v) < 0.1 else "약함" if abs(v) < 0.2
                              else "보통" if abs(v) < 0.35 else "강함")
        lines = []
        if len(done):
            fell = done[done["정점 대비 최저"] < -10]
            n, k = len(done), len(fell)
            yrs = ", ".join(s[:4] for s in fell["지표 정점"])
            lines.append(f"{an} 기준, 과거 <b>{n}번</b>의 {ind_name} 정점 뒤 2년 안에 10% 넘게 빠진 건 "
                         f"<b>{k}번</b>" + (f" ({yrs})" if k else "") + "이었습니다.")
            m = done["주가 고점 (정점 대비)"].median()
            if m <= -2:
                lines.append(f"주가 고점은 보통 지표 정점보다 <b>{-m:.0f}개월 먼저</b> 왔습니다 "
                             "→ 주가가 지표보다 앞서 움직입니다.")
            elif m >= 9:
                lines.append("정점 뒤에도 주가가 1년 가까이 더 오른 경우가 많았습니다 "
                             "→ 지표 정점이 곧 주가 고점은 아닙니다.")
            else:
                lines.append(f"주가 고점은 지표 정점 무렵(중간값 {m:+.0f}개월)에 왔습니다.")
        lines.append(f"{ind_name}가 오르면 {an} 주가는 <b>약 {L_min}개월 뒤</b>에 가장 약했습니다 "
                     f"(관계 {strength(c_min)}).")
        if cur is not None and cur in pa.index:
            lines.append(f"지금: {ind_name} 잠정 정점 {cur} ({x[cur]:.1f}%) 이후 "
                         f"{(pa.index[-1] - cur).n}개월, {an} <b>{(pa.iloc[-1] / pa[cur] - 1) * 100:+.1f}%</b>.")
        if len(done) and len(fell) <= len(done) / 2:
            lines.append("<b>→ 지표가 튄다고 바로 무너지진 않았습니다. 크게 빠진 건 다른 재료가 겹친 때였습니다.</b>")
        with box.container():
            takeaway(lines)

# ---------------------------------------------------------------- CPI 서프라이즈
with t_surp:
    sp = safe(surprise_frame, "CPI 서프라이즈 기록")
    if sp is not None:

        st.markdown("CPI 발표 때마다 **발표 직전 예상치**(클리블랜드 연준 나우캐스트)와 **실제치**의 차이, "
                    "그리고 그날 시장 반응입니다. 시장은 숫자 자체보다 **예상과의 차이**에 반응합니다.")
        sbox = st.empty()     # 📌 한 줄 요약 자리
        last = sp.iloc[-1]
        m = st.columns(4)
        m[0].metric(f"다음 발표 {ev_cpi:%m/%d}" if ev_cpi else "다음 발표",
                    f"{nowcast:.2f}%" if nowcast is not None else "-",
                    f"{target:%Y.%m} CPI 예상 (나우캐스트)", delta_color="off", border=True)
        m[1].metric(f"직전 발표 {last.release:%Y-%m-%d} ({last.target})",
                    f"{last.surprise:+.2f}%p", f"예상 {last.nowcast:.2f}% → 실제 {last.actual:.2f}%",
                    delta_color="off", border=True)
        m[2].metric("평균 서프라이즈 크기", f"{sp.surprise.abs().mean():.2f}%p",
                    f"{len(sp)}회 발표 (2013.09~)", delta_color="off", border=True)
        m[3].metric("직전 발표일 SOX", f"{last['^SOX']:+.2f}%" if pd.notna(last["^SOX"]) else "-",
                    f"S&P500 {last['^GSPC']:+.2f}% · 10년물 {last['10년물']:+.0f}bp"
                    if pd.notna(last["^GSPC"]) else "", delta_color="off", border=True)

        per = st.segmented_control(
            "기간", ["전체 (2013~)", "고물가 시대 (2021~)"], default="전체 (2013~)",
            key="surp_per") or "전체 (2013~)"
        if per.startswith("고물가"):
            sp = sp[sp.release >= "2021-01-01"].reset_index(drop=True)

        band = 0.1
        sp["구분"] = pd.cut(sp.surprise, [-99, -band, band, 99],
                          labels=[f"예상보다 낮음 (≤ −{band}%p)", f"예상 근처 (±{band}%p)",
                                  f"예상보다 높음 (≥ +{band}%p)"])
        grp = sp.groupby("구분", observed=False).agg(
            횟수=("surprise", "size"), **{n: (t, "mean") for t, n in LONG_ASSETS.items()},
            **{"10년물 (bp)": ("10년물", "mean")}).reset_index()
        g3 = grp.set_index("구분")
        lo_r, hi_r = g3.iloc[0], g3.iloc[2]
        sl = [f"CPI가 예상보다 <b>0.1%p 이상 높게</b> 나온 날 → 반도체 평균 "
              f"<b>{hi_r['반도체(SOX)']:+.2f}%</b>, 10년물 {hi_r['10년물 (bp)']:+.1f}bp "
              f"({int(hi_r['횟수'])}번)",
              f"CPI가 예상보다 <b>0.1%p 이상 낮게</b> 나온 날 → 반도체 평균 "
              f"<b>{lo_r['반도체(SOX)']:+.2f}%</b>, 10년물 {lo_r['10년물 (bp)']:+.1f}bp "
              f"({int(lo_r['횟수'])}번)"]
        if nowcast is not None:
            sl.append(f"다음 발표{f' {ev_cpi:%m/%d}' if ev_cpi else ''}: 예상 <b>{nowcast:.2f}%</b> "
                      "→ 이보다 높으면 약세, 낮으면 강세 쪽이 과거 패턴입니다.")
        sl.append("<b>→ 시장은 숫자 자체가 아니라 '예상과의 차이'에 반응합니다.</b>"
                  + (" 고물가 시대(2021~)엔 이 반응이 더 커졌습니다." if not per.startswith("고물가") else ""))
        with sbox.container():
            takeaway(sl, title=f"📌 한 줄 요약 — {per}")

        st.markdown("**서프라이즈 방향별 발표 당일 평균 반응**")
        pct = st.column_config.NumberColumn(format="%+.2f%%")
        st.dataframe(grp, hide_index=True, width="stretch", column_config={
            **{n: pct for n in LONG_ASSETS.values()},
            "10년물 (bp)": st.column_config.NumberColumn(format="%+.1f")})

        pick = st.segmented_control("산점도 자산", list(LONG_ASSETS), default="^SOX",
                                    format_func=LONG_ASSETS.get, key="surp_asset") or "^SOX"
        d = sp.dropna(subset=[pick])
        cth = theme()
        fig = go.Figure(go.Scatter(
            x=d.surprise, y=d[pick], mode="markers",
            customdata=list(zip(d.release.dt.strftime("%Y-%m-%d"), d.target.astype(str))),
            hovertemplate="%{customdata[0]} (%{customdata[1]}분)<br>서프라이즈 %{x:+.2f}%p"
                          "<br>당일 %{y:+.2f}%<extra></extra>",
            marker=dict(size=9, color=cth["series"][0], opacity=0.8,
                        line=dict(width=1, color=cth["surface"]))))
        fig.add_hline(y=0, line=dict(color=cth["axis"], width=1))
        fig.add_vline(x=0, line=dict(color=cth["axis"], width=1))
        fig.update_layout(
            height=380, margin=dict(l=70, r=8, t=16, b=56),   # 축 제목 공간
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family='system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif',
                      color=cth["text2"], size=12),
            hoverlabel=dict(bgcolor=cth["surface"], font_color=cth["text"]),
            xaxis=dict(title="서프라이즈 (실제 − 예상, %p)", showgrid=False, zeroline=False,
                       linecolor=cth["axis"], tickfont=dict(color=cth["muted"]), automargin=True),
            yaxis=dict(title=f"{LONG_ASSETS[pick]} 발표 당일 (%)", gridcolor=cth["grid"],
                       zeroline=False, tickfont=dict(color=cth["muted"]), ticksuffix="%",
                       automargin=True))
        st.markdown(f"**서프라이즈 vs {LONG_ASSETS[pick]} 당일 수익률** (점 하나 = 발표 한 번)")
        show(fig)
        st.caption(f"상관계수 {d.surprise.corr(d[pick]):+.2f} · 오른쪽 아래(예상보다 높고 주가 하락)에 "
                   "몰려 있을수록 '물가 서프라이즈에 민감한' 시장 · 예상치는 시장 컨센서스(이코노미스트 "
                   "설문)가 아니라 나우캐스트라서 실제 시장 기대와는 차이가 있을 수 있음")

        with st.expander(f"발표별 기록 (최근 {min(36, len(sp))}회)"):
            show_t = sp.iloc[::-1].head(36).assign(
                발표일=lambda f: f.release.dt.strftime("%Y-%m-%d"),
                대상월=lambda f: f.target.astype(str))
            st.dataframe(show_t[["발표일", "대상월", "nowcast", "actual", "surprise",
                                 *LONG_ASSETS, "10년물"]].rename(columns={
                                     "nowcast": "예상", "actual": "실제", "surprise": "서프라이즈",
                                     **LONG_ASSETS, "10년물": "10년물 (bp)"}),
                         hide_index=True, width="stretch", column_config={
                             "예상": st.column_config.NumberColumn(format="%.2f%%"),
                             "실제": st.column_config.NumberColumn(format="%.2f%%"),
                             "서프라이즈": st.column_config.NumberColumn(format="%+.2f%%p"),
                             **{n: pct for n in LONG_ASSETS.values()},
                             "10년물 (bp)": st.column_config.NumberColumn(format="%+.1f")})

# ---------------------------------------------------------------- 국채 금리
with t_rate:
    @st.fragment
    def rate_intraday():
        intra = safe(yf_intraday, "장중 시세")
        if intra is None:
            return
        pick = st.radio("장중 차트 (최근 5거래일, 5분봉)", list(RT_YIELDS.values()),
                        index=2, horizontal=True, key="rt_pick")
        t = {v: k for k, v in RT_YIELDS.items()}[pick]
        s = intra[t].dropna()
        s.index = s.index.tz_localize(None)
        st.markdown(f"**미국채 {pick} 수익률 — 장중 (한국시간)**")
        show(line_chart([(pick, s)], yfmt=".3f", height=280, intraday=True))

    rate_intraday()

    if curve is not None:
        cv = curve.dropna(how="all")
        last_d = cv.index[-1]

        def row_at(days):
            return cv[cv.index <= last_d - pd.Timedelta(days=days)].iloc[-1]

        left, right = st.columns(2)
        labels = [_maturity_kr(m) for m in cv.columns]
        with left:
            st.markdown(f"**수익률 곡선 — 만기별** · 기준 {last_d:%Y-%m-%d} (재무부 공식)")
            pts = [("오늘", cv.iloc[-1]), ("1개월 전", row_at(30)), ("1년 전", row_at(365))]
            show(line_chart([(n, pd.Series(r.values, index=labels)) for n, r in pts],
                            xcat=True, markers=True, label_ends=False))
        with right:
            spread = (cv[120] - cv[24]).dropna()
            st.markdown(f"**장단기 금리차 (10년 − 2년)** · 현재 {spread.iloc[-1]:+.2f}%p")
            show(line_chart([("10년−2년", spread)], ysuffix="%p", zero_line=True))
            st.caption("0 아래 = 장단기 역전 (단기금리가 더 높음). 경기침체 선행 신호로 자주 인용됨")

        st.markdown("**만기별 추이**")
        c1, c2 = st.columns([3, 1])
        all_m = {_maturity_kr(m): m for m in cv.columns}
        pick = c1.multiselect("만기 선택 (최대 4개)", list(all_m),
                              default=[k for k in ("3개월", "2년", "10년", "30년") if k in all_m],
                              max_selections=4)
        rng = c2.selectbox("기간", ["3개월", "6개월", "1년", "2년"], index=2)
        days = {"3개월": 92, "6개월": 183, "1년": 365, "2년": 730}[rng]
        sub = cv[cv.index >= last_d - pd.Timedelta(days=days)]
        if pick:
            show(line_chart([(k, sub[all_m[k]].dropna()) for k in pick], height=360))

        with st.expander("전 만기 표 보기"):
            st.dataframe(pd.DataFrame({
                "만기": labels,
                "수익률(%)": cv.iloc[-1].values,
                "전일 대비": [bp(x) for x in (cv.iloc[-1] - cv.iloc[-2]).values],
                "1개월 대비": [bp(x) for x in (cv.iloc[-1] - row_at(30)).values],
                "1년 대비": [bp(x) for x in (cv.iloc[-1] - row_at(365)).values],
            }), hide_index=True, width="stretch",
                column_config={"수익률(%)": st.column_config.NumberColumn(format="%.2f")})

# ---------------------------------------------------------------- 물가·고용
with t_infl:
    real = safe(lambda: treasury("daily_treasury_real_yield_curve"), "TIPS 실질금리")
    c = st.columns(5)
    if mac is not None:
        for i, (name, s) in enumerate([("CPI", cpi_yoy), ("근원 CPI", core_yoy)]):
            c[i].metric(f"{name} 전년比 ({s.index[-1]:%Y.%m})", f"{s.iloc[-1]:.1f}%",
                        f"{s.iloc[-1] - s.iloc[-2]:+.1f}%p", delta_color="off", border=True)
        for i, (name, col) in enumerate([("CPI", "CPI_SA"), ("근원 CPI", "근원_SA")]):
            m = (mdf[col].pct_change(fill_method=None) * 100).dropna()
            c[2 + i].metric(f"{name} 전월比 ({m.index[-1]:%Y.%m})", f"{m.iloc[-1]:+.2f}%",
                            f"{m.iloc[-1] - m.iloc[-2]:+.2f}%p", delta_color="off", border=True,
                            help="계절조정 기준")
    if curve is not None and real is not None:
        be5 = (curve[60] - real[60]).dropna()
        be10 = (curve[120] - real[120]).dropna()
        c[4].metric("5년 / 10년 기대인플레", f"{be5.iloc[-1]:.2f}% / {be10.iloc[-1]:.2f}%",
                    border=True, help="명목 국채금리 − TIPS 실질금리 (BEI). 매 영업일 갱신")

    left, right = st.columns(2)
    if mac is not None:
        with left:
            st.markdown("**소비자물가 상승률 (전년 동월 대비)**")
            show(line_chart([("CPI", cpi_yoy), ("근원 CPI", core_yoy)], yfmt=".1f"))
            st.caption(f"출처: {msrc} · 근원 = 식품·에너지 제외 · 월 1회 발표 · "
                       "2025.10은 정부 셧다운으로 미발표")
        with right:
            st.markdown("**실업률**")
            show(line_chart([("실업률", unrate)], yfmt=".1f"))
            st.caption("저물가 시대엔 '왕' 지표, 고물가 시대엔 보조 지표 (해석이 반대)")
    if curve is not None and real is not None:
        st.markdown("**시장 기대인플레이션 (BEI)**")
        cut = be10.index[-1] - pd.Timedelta(days=730)
        show(line_chart([("5년", be5[be5.index >= cut]), ("10년", be10[be10.index >= cut])]))
        st.caption("출처: 미 재무부 · 명목금리 − TIPS 실질금리")

# ---------------------------------------------------------------- 반도체
with t_semi:
    sm = safe(lambda: semis(semi_list), "반도체 시세")
    if sm is not None:
        px = sm.ffill()
        last = px.iloc[-1]

        etfs = [t for t in [SOX] + SEMI_ETFS if t in px][:4]
        tc = st.columns(len(etfs))
        for col, t in zip(tc, etfs):
            s = sm[t].dropna()
            col.metric(NAMES.get(t, t), f"{s.iloc[-1]:,.2f}",
                       f"{(s.iloc[-1] / s.iloc[-2] - 1) * 100:+.2f}%", delta_color="off",
                       border=True)

        st.markdown("**수익률 비교** (기간 시작 = 100)")
        per = st.segmented_control("기간", ["1개월", "3개월", "6개월", "YTD", "1년"],
                                   default="3개월", key="semi_per")
        end = px.index[-1]
        start = (pd.Timestamp(end.year, 1, 1) if per == "YTD" else
                 end - pd.Timedelta(days={"1개월": 30, "3개월": 91, "6개월": 182,
                                          "1년": 365}.get(per, 91)))
        sub = px[px.index >= start]
        show(line_chart([(NAMES.get(t, t), sub[t] / sub[t].iloc[0] * 100) for t in etfs],
                        ysuffix="", yfmt=".1f", refs=[(100, "기준 100")]))

        def ret(days):
            ref = px[px.index <= end - pd.Timedelta(days=days)].iloc[-1]
            return (last / ref - 1) * 100

        ytd_ref = px[px.index < pd.Timestamp(end.year, 1, 1)].iloc[-1]
        hi52 = px[px.index > end - pd.Timedelta(days=365)].max()
        tbl = pd.DataFrame({
            "종목": [NAMES.get(t, t) for t in px.columns],
            "티커": list(px.columns),
            "현재가": last.values,
            "1일": ((last / px.iloc[-2] - 1) * 100).values,
            "1주": ret(7).values, "1개월": ret(30).values, "3개월": ret(91).values,
            "YTD": ((last / ytd_ref - 1) * 100).values,
            "52주 고점 대비": ((last / hi52 - 1) * 100).values,
        })
        order = {t: i for i, t in enumerate([SOX] + list(semi_list))}
        tbl = tbl.sort_values("티커", key=lambda s: s.map(order)).reset_index(drop=True)
        pct = st.column_config.NumberColumn(format="%+.1f%%")
        st.markdown(f"**종목별 수익률** · 기준 {end:%Y-%m-%d} 종가 (장중이면 현재가)")
        st.dataframe(tbl, hide_index=True, width="stretch", column_config={
            "현재가": st.column_config.NumberColumn(format="%,.2f"),
            **{k: pct for k in ["1일", "1주", "1개월", "3개월", "YTD", "52주 고점 대비"]}})

        # 반도체 vs 금리 (스펙 1-3: 반도체는 물가보다 10년물을 먼저 본다)
        st.markdown("**반도체 vs 10년물 금리 — 최근 1년**")
        st.caption("스펙 1-3: 금리가 꺾여야 반도체가 숨을 쉰다. 같은 기간을 위아래로 나란히 표시")
        yr = end - pd.Timedelta(days=365)
        show(line_chart([("SOX", sm[SOX][sm.index >= yr].dropna())], ysuffix="",
                        yfmt=",.0f", height=240))
        if len(y10s) > 5:
            show(line_chart([("10년물", y10s[y10s.index >= yr])], height=200,
                            refs=[(th["y10_green"], f"{th['y10_green']:.1f}%")]))
        if daily is not None:
            j = pd.concat([daily[SOX].pct_change(fill_method=None),
                           daily["^TNX"].diff()], axis=1).dropna().tail(60)
            corr = j.corr().iloc[0, 1]
            st.metric("최근 60거래일 상관계수 (SOX 일간 수익률 vs 10년물 일간 변화)",
                      f"{corr:+.2f}", border=True,
                      help="음수일수록 '금리 오르는 날 반도체 빠지는' 관계가 강함")

# ---------------------------------------------------------------- WTI
with t_oil:
    intra = safe(yf_intraday, "장중 시세")
    if intra is not None:
        s = intra[WTI].dropna()
        s.index = s.index.tz_localize(None)
        st.markdown("**WTI 원유 선물 — 장중 (최근 5거래일, 5분봉, 한국시간)**")
        show(line_chart([("WTI", s)], ysuffix="", yfmt=",.2f", height=280, intraday=True))
    if daily is not None:
        s = daily[WTI].dropna()
        st.markdown(f"**WTI — 최근 1년 일봉** · 1년 고점 \\${s.max():,.2f} / 저점 \\${s.min():,.2f}")
        show(line_chart([("WTI", s)], ysuffix="", yfmt=",.2f"))
        st.caption("근월물 선물(CL=F) 기준, 달러/배럴 · 만기 교체일엔 가격이 튈 수 있음")

# ---------------------------------------------------------------- 트라이팟
with t_tri:
    raw = safe(tripod_data, "NDX/VIX")
    if raw is not None:
        out = tp.build(raw)
        d = out.dropna(subset=["pos"])
        t = d.iloc[-1]
        st.markdown(f"**기준 종가일 {d.index[-1]:%Y-%m-%d}** · 판정은 종가 기준, 매매는 다음 거래일")
        c = st.columns(5)
        c[0].metric("시장 상태", tp.STATE_KR.get(t.state, "판정불가"), border=True)
        c[1].metric("규칙 포지션", tp.POSITIONS[t.pos][0], tp.POSITIONS[t.pos][1],
                    delta_color="off", border=True)
        c[2].metric("250일선 이격도", f"{t.gap * 100:+.2f}%", "기준 +1% / −5%",
                    delta_color="off", border=True)
        c[3].metric("VIX 10일 평균", f"{t.vix10:.2f}", "기준 18 / 28",
                    delta_color="off", border=True)
        c[4].metric("52주 고점 대비", f"{t.dd * 100:+.2f}%", "기준 −9%",
                    delta_color="off", border=True)
        st.info(f"해야 할 액션: **{t.action}**")

        tail = d[d.index >= d.index[-1] - pd.Timedelta(days=730)]
        st.markdown("**나스닥100 vs 250일 이동평균 (최근 2년)**")
        show(line_chart([("NDX", tail["ndx"]), ("250일선", tail["ma250"])],
                        ysuffix="", yfmt=",.0f"))

        ch = out[out["changed"]].tail(15).iloc[::-1]
        with st.expander(f"최근 신호 변경 {len(ch)}건"):
            st.dataframe(pd.DataFrame({
                "날짜": ch.index.strftime("%Y-%m-%d"),
                "상태": [tp.STATE_KR.get(s, "") for s in ch["state"]],
                "이격도": [f"{g * 100:+.2f}%" for g in ch["gap"]],
                "VIX10": ch["vix10"].round(2).values,
                "52주 낙폭": [f"{x * 100:+.2f}%" for x in ch["dd"]],
                "포지션": [tp.POSITIONS[p][0] for p in ch["pos"]],
            }), hide_index=True, width="stretch")
        with st.expander("판정 과정 자세히 / 규칙 전문"):
            st.code(tp.report_text(out, explain=True), language=None)
            st.code(tp.RULES_TEXT, language=None)

# ---------------------------------------------------------------- 도움말 (스펙 9장)
with t_help:
    st.markdown("""
### 프레임워크 요약
- **저물가 시대 → 실업률이 왕.** 실업률이 바닥 찍고 오르면 매도, 정점 찍고 내려오면 매수.
- **고물가 시대 → 인플레이션이 왕.** 물가가 오르면 매도, 정점 찍고 꺾이면 매수. 실업률 해석은 반대가 된다.
- **금리는 자산시장의 중력.** 물가와 금리를 반드시 같이 본다. 반도체는 물가보다 10년물을 먼저 본다.
- **버블 붕괴 조건:** 금리 신고가 돌파(Breaking to New Highs) + 물가 재상승으로 "금리가 내려올 길이 없다"는 체념(No Way Back)이 **동시에** 나올 때.

### 이 프레임워크의 한계
- "지금이 고물가 시대냐"는 사후에야 확실히 알 수 있다.
- "물가가 꺾였다"도 실시간으로는 헷갈린다. 2022년 물가 정점은 6월, 주가 바닥은 10월이었다.
- 버블 붕괴 3건, 황금기 4건 등 적은 사례에서 나온 규칙이다.
- 시장 전체(지수)에는 잘 맞아도 반도체처럼 금리에 민감한 업종은 금리가 더 중요할 수 있다.
- 지표는 방향을 알려주는 나침반이지, 정확한 시점을 알려주는 시계가 아니다.

### 계산 방식 메모
- **CPI 전년비**는 BLS 발표처럼 소수 1자리로 반올림한 값으로 "N개월 연속 상승/하락"을 판정한다 (같으면 연속이 끊김).
- **10년물**은 재무부 공식 종가 기준. 기본 '직전 고점' 5.34%는 2026-10-01 **장중** 고가이고, 종가 기준 최고치는 5.29%(09-30)다. 사이드바에서 '52주 최고치 자동'을 고르면 종가 기준으로 바뀐다.
- **2025년 10월 CPI**는 정부 셧다운으로 발표되지 않아 비어 있다.
- 숫자 기준(3.5%, 5.0%, 4.7%, 3.0% 등)은 전부 대화에서 정한 휴리스틱이며 사이드바에서 바꿀 수 있다.

### 데이터 출처
Yahoo Finance (시세, 약 10~15분 지연, SPY PER) · U.S. Treasury (수익률 곡선, TIPS) · BLS (CPI, 실업률) / 실패 시 FRED · 클리블랜드 연준 (인플레이션 나우캐스트) · 연준 (FOMC 일정) · 뉴욕 연준 (기준금리 목표 범위) · FRED API (CPI·고용보고서 발표일, 키 필요)
""")

st.divider()
st.caption(DISCLAIMER)
