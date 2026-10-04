# -*- coding: utf-8 -*-
"""
미국 금리 · 물가 · WTI 유가 모니터  +  나스닥 트라이팟 판정
================================================================
로컬 실행:   streamlit run app.py        (또는 대시보드_실행.bat 더블클릭)

데이터 출처 (모두 무료, API 키 불필요)
  - 장중 시세 (약 10~15분 지연): Yahoo Finance  ^IRX ^FVX ^TNX ^TYX CL=F ^NDX ^VIX
  - 국채 수익률 곡선 전 만기 / TIPS 실질금리: 미 재무부 (매 영업일 장 마감 후)
  - CPI: 미 노동통계국 BLS (월 1회 발표)  /  실패 시 FRED 로 대체
================================================================
"""

import io
import json
import re
import urllib.request
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

import nasdaq_tripod as tp

st.set_page_config(page_title="미국 금리·물가·유가 모니터", page_icon="📈", layout="wide")

UA = {"User-Agent": "Mozilla/5.0"}
KST = "Asia/Seoul"
NY = "America/New_York"

# 장중 시세 티커 (Yahoo). ^TNX 등은 수익률 그 자체(%)로 나옴
RT_YIELDS = {"^IRX": "3개월", "^FVX": "5년", "^TNX": "10년", "^TYX": "30년"}
WTI = "CL=F"


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
               zero_line=False, xcat=False, markers=False, intraday=False):
    """series: [(이름, pd.Series)]  — 순서대로 고정 색 배정.
    2개 이상이면 범례 + 선 끝 직접 라벨, 마우스 올리면 세로선 + 툴팁."""
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


def show(fig):
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


def bp(x):
    """%p 단위 차이 -> bp (0.04 -> +4bp)"""
    return f"{x * 100:+.0f}bp"


# ================================================================
# 데이터 수집
# ================================================================
def _get(url, timeout=30, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")


def _close(df):
    """yf.download 결과에서 Close만, 티커가 열이 되도록."""
    c = df["Close"]
    return c.to_frame() if isinstance(c, pd.Series) else c


@st.cache_data(ttl=60, show_spinner=False)
def yf_daily():
    """금리 4종 + WTI 일봉 1년. 오늘 봉은 장중엔 '현재가'로 채워져 들어옴."""
    df = _close(yf.download(list(RT_YIELDS) + [WTI], period="1y", interval="1d",
                            progress=False, auto_adjust=False))
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def yf_intraday():
    df = _close(yf.download(list(RT_YIELDS) + [WTI], period="5d", interval="5m",
                            progress=False, auto_adjust=False))
    idx = pd.to_datetime(df.index)
    df.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(KST)
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


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cpi():
    """CPI 지수 (월). 1순위 BLS, 실패하면 FRED."""
    ids = {"CUUR0000SA0": "CPI", "CUUR0000SA0L1E": "근원 CPI"}
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
        for sid, name in (("CPIAUCSL", "CPI"), ("CPILFESL", "근원 CPI")):
            d = pd.read_csv(io.StringIO(_get(
                f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=15)))
            d.iloc[:, 0] = pd.to_datetime(d.iloc[:, 0])
            out[name] = pd.to_numeric(d.set_index(d.columns[0]).iloc[:, 0], errors="coerce")
        df = pd.DataFrame(out)
        return df[df.index >= pd.Timestamp(this - 5, 1, 1)], "FRED"


@st.cache_data(ttl=3600, show_spinner=False)
def tripod_data():
    df = _close(yf.download(["^NDX", "^VIX"], period="10y", interval="1d",
                            progress=False, auto_adjust=False))
    df = df.rename(columns={"^NDX": "ndx", "^VIX": "vix"})[["ndx", "vix"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return tp.drop_unclosed_bar(df)


def safe(fn, what):
    try:
        return fn()
    except Exception as e:
        st.warning(f"{what} 데이터를 불러오지 못했습니다: {e}")
        return None


# ================================================================
# 화면
# ================================================================
st.title("미국 금리 · 물가 · WTI 유가 모니터")
st.caption("장중 시세는 1분마다 자동 갱신 (Yahoo Finance, 약 10~15분 지연) · "
           "전 만기 수익률 곡선은 미 재무부 장 마감 공식치 · 투자 권유가 아닙니다")


@st.fragment(run_every="60s")
def top_tiles():
    daily = safe(yf_daily, "장중 시세")
    curve = safe(treasury, "재무부 수익률")
    real = safe(lambda: treasury("daily_treasury_real_yield_curve"), "TIPS 실질금리")
    infl = safe(cpi, "CPI")
    intra = safe(yf_intraday, "장중 시세")

    cols = st.columns(7)
    if daily is not None:
        for i, (t, name) in enumerate(list(RT_YIELDS.items()) + [(WTI, "WTI")]):
            s = daily[t].dropna()
            if len(s) < 2:
                continue
            last, prev = s.iloc[-1], s.iloc[-2]
            if t == WTI:
                cols[6].metric("WTI 유가 ($/배럴)", f"${last:,.2f}",
                               f"{last - prev:+.2f} ({(last / prev - 1) * 100:+.2f}%)",
                               delta_color="off", border=True)
            else:
                cols[i].metric(f"미국채 {name}", f"{last:.3f}%", bp(last - prev),
                               delta_color="off", border=True)
    if curve is not None and real is not None:
        be = (curve[120] - real[120]).dropna()
        cols[4].metric("10년 기대인플레", f"{be.iloc[-1]:.2f}%",
                       bp(be.iloc[-1] - be.iloc[-2]), delta_color="off", border=True,
                       help="10년 명목금리 − 10년 TIPS 실질금리 (BEI). 시장이 예상하는 향후 10년 평균 물가상승률")
    if infl is not None:
        yoy = (infl[0]["CPI"].pct_change(12, fill_method=None) * 100).dropna()
        cols[5].metric(f"CPI 전년比 ({yoy.index[-1]:%Y.%m})", f"{yoy.iloc[-1]:.1f}%",
                       f"{yoy.iloc[-1] - yoy.iloc[-2]:+.2f}%p", delta_color="off", border=True,
                       help="소비자물가지수 전년 동월 대비. 월 1회 발표")
    if intra is not None and len(intra):
        ts = intra.dropna(how="all").index[-1]
        st.caption(f"마지막 시세 체결 {ts:%m/%d %H:%M} (한국시간) · 화면 갱신 "
                   f"{pd.Timestamp.now(tz=KST):%H:%M:%S}")


top_tiles()

tab_rate, tab_infl, tab_oil, tab_tri = st.tabs(["국채 금리", "물가", "WTI 유가", "나스닥 트라이팟"])

# ---------------------------------------------------------------- 금리
with tab_rate:
    @st.fragment(run_every="60s")
    def rate_intraday():
        intra = safe(yf_intraday, "장중 시세")
        if intra is None:
            return
        pick = st.radio("장중 차트 (최근 5거래일, 5분봉)", list(RT_YIELDS.values()),
                        index=2, horizontal=True, key="rt_pick")
        t = {v: k for k, v in RT_YIELDS.items()}[pick]
        s = intra[t].dropna()
        s.index = s.index.tz_localize(None)
        st.markdown(f"**미국채 {pick} 수익률 — 장중**")
        show(line_chart([(pick, s)], yfmt=".3f", height=280, intraday=True))

    rate_intraday()

    curve = safe(treasury, "재무부 수익률")
    if curve is not None:
        curve = curve.dropna(how="all")
        last_d = curve.index[-1]

        def row_at(days):
            return curve[curve.index <= last_d - pd.Timedelta(days=days)].iloc[-1]

        left, right = st.columns(2)
        with left:
            st.markdown(f"**수익률 곡선 — 만기별** · 기준 {last_d:%Y-%m-%d} (재무부 공식)")
            labels = [_maturity_kr(m) for m in curve.columns]
            pts = [("오늘", curve.iloc[-1]), ("1개월 전", row_at(30)), ("1년 전", row_at(365))]
            show(line_chart([(n, pd.Series(r.values, index=labels)) for n, r in pts],
                            xcat=True, markers=True, label_ends=False))
        with right:
            spread = (curve[120] - curve[24]).dropna()
            st.markdown(f"**장단기 금리차 (10년 − 2년)** · 현재 {spread.iloc[-1]:+.2f}%p")
            show(line_chart([("10년−2년", spread)], ysuffix="%p", zero_line=True))
            st.caption("0 아래 = 장단기 역전 (단기금리가 더 높음). 경기침체 선행 신호로 자주 인용됨")

        st.markdown("**만기별 추이**")
        c1, c2 = st.columns([3, 1])
        all_m = {_maturity_kr(m): m for m in curve.columns}
        pick = c1.multiselect("만기 선택 (최대 4개)", list(all_m),
                              default=[k for k in ("3개월", "2년", "10년", "30년") if k in all_m],
                              max_selections=4)
        rng = c2.selectbox("기간", ["3개월", "6개월", "1년", "2년"], index=2)
        days = {"3개월": 92, "6개월": 183, "1년": 365, "2년": 730}[rng]
        sub = curve[curve.index >= last_d - pd.Timedelta(days=days)]
        if pick:
            show(line_chart([(k, sub[all_m[k]].dropna()) for k in pick], height=360))

        with st.expander("전 만기 표 보기"):
            tbl = pd.DataFrame({
                "만기": labels,
                "수익률(%)": curve.iloc[-1].values,
                "전일 대비": [bp(x) for x in (curve.iloc[-1] - curve.iloc[-2]).values],
                "1개월 대비": [bp(x) for x in (curve.iloc[-1] - row_at(30)).values],
                "1년 대비": [bp(x) for x in (curve.iloc[-1] - row_at(365)).values],
            })
            st.dataframe(tbl, hide_index=True, width="stretch",
                         column_config={"수익률(%)": st.column_config.NumberColumn(format="%.2f")})

# ---------------------------------------------------------------- 물가
with tab_infl:
    infl = safe(cpi, "CPI")
    curve = safe(treasury, "재무부 수익률")
    real = safe(lambda: treasury("daily_treasury_real_yield_curve"), "TIPS 실질금리")

    c = st.columns(4)
    if infl is not None:
        idx, src = infl
        yoy = (idx.pct_change(12, fill_method=None) * 100).dropna(how="all")
        mom = (idx["CPI"].pct_change(fill_method=None) * 100).dropna()
        for i, name in enumerate(["CPI", "근원 CPI"]):
            s = yoy[name].dropna()
            c[i].metric(f"{name} 전년比 ({s.index[-1]:%Y.%m})", f"{s.iloc[-1]:.1f}%",
                        f"{s.iloc[-1] - s.iloc[-2]:+.2f}%p", delta_color="off", border=True)
        c[2].metric(f"CPI 전월比 ({mom.index[-1]:%Y.%m})", f"{mom.iloc[-1]:+.2f}%",
                    border=True, help="계절조정 전 지수 기준")
    if curve is not None and real is not None:
        be5 = (curve[60] - real[60]).dropna()
        be10 = (curve[120] - real[120]).dropna()
        c[3].metric("5년 / 10년 기대인플레", f"{be5.iloc[-1]:.2f}% / {be10.iloc[-1]:.2f}%",
                    border=True, help="명목 국채금리 − TIPS 실질금리 (BEI). 매 영업일 갱신")

    left, right = st.columns(2)
    if infl is not None:
        with left:
            st.markdown("**소비자물가 상승률 (전년 동월 대비)**")
            show(line_chart([(n, yoy[n].dropna()) for n in ["CPI", "근원 CPI"]], ysuffix="%",
                            yfmt=".1f"))
            st.caption(f"출처: {src} · 근원 = 식품·에너지 제외 · 월 1회 발표")
    if curve is not None and real is not None:
        with right:
            st.markdown("**시장 기대인플레이션 (BEI)**")
            cut = be10.index[-1] - pd.Timedelta(days=730)
            show(line_chart([("5년", be5[be5.index >= cut]), ("10년", be10[be10.index >= cut])]))
            st.caption("출처: 미 재무부 · 명목금리 − TIPS 실질금리")

# ---------------------------------------------------------------- WTI
with tab_oil:
    @st.fragment(run_every="60s")
    def oil_intraday():
        intra = safe(yf_intraday, "장중 시세")
        if intra is None:
            return
        s = intra[WTI].dropna()
        s.index = s.index.tz_localize(None)
        st.markdown("**WTI 원유 선물 — 장중 (최근 5거래일, 5분봉, 한국시간)**")
        show(line_chart([("WTI", s)], ysuffix="", yfmt=",.2f", height=280, intraday=True))

    oil_intraday()
    daily = safe(yf_daily, "WTI 일봉")
    if daily is not None:
        s = daily[WTI].dropna()
        st.markdown(f"**WTI — 최근 1년 일봉** · 1년 고점 ${s.max():,.2f} / 저점 ${s.min():,.2f}")
        show(line_chart([("WTI", s)], ysuffix="", yfmt=",.2f"))
        st.caption("근월물 선물(CL=F) 기준, 달러/배럴 · 만기 교체일엔 가격이 튈 수 있음")

# ---------------------------------------------------------------- 트라이팟
with tab_tri:
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

st.divider()
st.caption("데이터: Yahoo Finance(지연 시세), U.S. Treasury, BLS/FRED · "
           "공개된 자료를 기계적으로 계산해 보여줄 뿐이며 투자 권유가 아닙니다.")
