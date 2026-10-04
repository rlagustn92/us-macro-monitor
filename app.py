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

import macro_rules as mr
import nasdaq_tripod as tp

st.set_page_config(page_title="물가·금리 신호 모니터", page_icon="📈", layout="wide")

UA = {"User-Agent": "Mozilla/5.0"}
KST = "Asia/Seoul"

# Yahoo 티커. ^TNX 등은 수익률 그 자체(%)로 나옴 (예전처럼 ÷10 할 필요 없음)
RT_YIELDS = {"^IRX": "3개월", "^FVX": "5년", "^TNX": "10년", "^TYX": "30년"}
WTI, SOX = "CL=F", "^SOX"
LEVERAGE = ["SOXL", "TQQQ"]

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
                   type="category" if xcat else None,
                   showspikes=True, spikemode="across", spikecolor=c["axis"],
                   spikethickness=1, spikedash="solid"),
        yaxis=dict(gridcolor=c["grid"], gridwidth=1, zeroline=False,
                   tickfont=dict(color=c["muted"]), ticksuffix=ysuffix),
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


def show(fig):
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


def bp(x):
    """%p 단위 차이 -> bp (0.04 -> +4bp)"""
    return f"{x * 100:+.0f}bp"


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
    df = _close(yf.download(list(RT_YIELDS) + [WTI], period="5d", interval="5m",
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


@st.cache_data(ttl=3600, show_spinner=False)
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

fed_pause = sb.checkbox("연준 금리 인상 중단 시그널", value=False,
                        help="🟢🟢 판정에 필요. 2026-09 인상 후 추가 인상 시사 중이라 기본 해제")
nowcast = sb.number_input("클리블랜드 연준 나우캐스트: 다음 CPI 전년비 (%)", value=None,
                          step=0.01, placeholder="예: 3.57")
fwd_per = sb.number_input("S&P500 선행 PER", value=None, step=0.1, placeholder="예: 22.5",
                          help="이익수익률(1/PER)과 10년물 비교에 사용")

with sb.expander("레버리지 진입가 (손절·익절 점검)"):
    entries = {t: st.number_input(f"{t} 진입가 ($)", value=None, step=0.01, key=f"entry_{t}")
               for t in LEVERAGE}

with sb.expander("다음 이벤트 일정"):
    today = date.today()
    first_fri = lambda y, m: date(y, m, 1) + timedelta(days=(4 - date(y, m, 1).weekday()) % 7)
    jobs = first_fri(today.year, today.month)
    if jobs < today:
        nm = date(today.year + today.month // 12, today.month % 12 + 1, 1)
        jobs = first_fri(nm.year, nm.month)
    ev_cpi = st.date_input("다음 CPI 발표", value=NEXT_CPI if NEXT_CPI >= today else None)
    ev_jobs = st.date_input("다음 고용보고서 (첫째 금요일 추정)", value=jobs)
    ev_fomc = st.date_input("다음 FOMC 결정", value=next((d for d in FOMC if d >= today), None))

with sb.expander("반도체 종목"):
    semi_txt = st.text_input("ETF + 종목 (쉼표로 구분)", ", ".join(SEMI_ETFS + SEMI_STOCKS))
semi_list = tuple(dict.fromkeys(t.strip().upper() for t in semi_txt.split(",") if t.strip()))


# ================================================================
# 지표 조립 + 신호 판정
# ================================================================
mac = safe(macro, "CPI·실업률")
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

h1, h2, h3 = st.columns([1.2, 1.2, 2])
with h1:
    st.markdown(
        f'<div style="padding:.55rem 1rem;border-radius:6px;background:rgba(127,127,127,.08)">'
        f'<div style="font-size:.85rem;opacity:.7">현재 레짐</div>'
        f'<div style="font-size:2rem;font-weight:700;line-height:1.25">'
        f'{"🔥 고물가 시대" if regime == "HIGH" else "🧊 저물가 시대"}</div></div>',
        unsafe_allow_html=True)
with h2:
    stage_badge(stage)
with h3:
    cpi_m = f"{cpi_yoy.index[-1]:%Y.%m}" if hasattr(cpi_yoy.index[-1], "strftime") else "수동"
    st.markdown(
        f"**데이터 기준** · CPI {cpi_m} ({msrc}) · 10년물 {y10:.2f}% ({y10_src})  \n"
        f"{regime_why}  \n"
        f"<span style='opacity:.7'>시세 받은 시각 {fetched_at():%m/%d %H:%M} · 1시간마다 갱신 · "
        f"투자 권유 아님</span>", unsafe_allow_html=True)

# 시세 타일
cols = st.columns(4) + st.columns(4)   # 4개씩 두 줄 (8개 한 줄은 숫자가 잘림)
if daily is not None:
    for i, (t, name) in enumerate(RT_YIELDS.items()):
        s = daily[t].dropna()
        cols[i].metric(f"미국채 {name}", f"{s.iloc[-1]:.3f}%", bp(s.iloc[-1] - s.iloc[-2]),
                       delta_color="off", border=True)
    for col, t, lab in ((cols[6], WTI, "WTI ($)"), (cols[7], SOX, "반도체지수 SOX")):
        s = daily[t].dropna()
        col.metric(lab, f"{s.iloc[-1]:,.2f}", f"{(s.iloc[-1] / s.iloc[-2] - 1) * 100:+.2f}%",
                   delta_color="off", border=True)
cols[4].metric(f"CPI 전년比 ({cpi_m})", f"{cpi_yoy.iloc[-1]:.1f}%",
               f"{cpi_yoy.iloc[-1] - cpi_yoy.iloc[-2]:+.1f}%p", delta_color="off", border=True)
um = f" ({unrate.index[-1]:%Y.%m})" if hasattr(unrate.index[-1], "strftime") else ""
cols[5].metric(f"실업률{um}", f"{unrate.iloc[-1]:.1f}%",
               f"{unrate.iloc[-1] - unrate.iloc[-2]:+.1f}%p", delta_color="off", border=True)

tabs = st.tabs(["🧭 신호 판정", "국채 금리", "물가·고용", "반도체", "WTI 유가",
                "나스닥 트라이팟", "도움말"])

# ---------------------------------------------------------------- 신호 판정
with tabs[0]:
    # ② 현재 액션 카드 (스펙 6-3)
    st.subheader(f"지금 할 일 — {mr.STAGES[stage]['icon']} {mr.STAGES[stage]['label']}")
    act = mr.ACTIONS[stage]
    for col, lab, txt in zip(st.columns(4), ["몸통 적립", "커버드콜", "레버리지 (꼬리 10%)",
                                             "대기 현금"], act):
        with col.container(border=True):
            st.caption(lab)
            st.markdown(f"**{txt}**")
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
            st.caption("사이드바에 S&P500 선행 PER을 넣으면 계산")
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

# ---------------------------------------------------------------- 국채 금리
with tabs[1]:
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
with tabs[2]:
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
with tabs[3]:
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
with tabs[4]:
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
with tabs[5]:
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
with tabs[6]:
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
Yahoo Finance (시세, 약 10~15분 지연) · U.S. Treasury (수익률 곡선, TIPS) · BLS (CPI, 실업률) / 실패 시 FRED
""")

st.divider()
st.caption(DISCLAIMER)
