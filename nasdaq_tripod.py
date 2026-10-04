#!/usr/bin/env python3.13
# -*- coding: utf-8 -*-
# ↑ 더블클릭 시 Windows py 런처가 이 줄을 보고 3.13으로 실행합니다.
#   (기본값인 3.14에는 yfinance/pandas가 설치돼 있지 않아 바로 꺼졌음)
"""
나스닥 트라이팟 판정기 v2  (조회 + 엑셀 자동 기록)
================================================================
영상에서 공개된 규칙(250일선 / VIX 10일평균 / 52주 낙폭)을
매일 계산해서 현재 상태와 규칙상 포지션을 출력하고,
버튼 한 번으로 엑셀 일지에 누적 기록합니다.

  판정 로직 (원문 규칙 그대로)
  ---------------------------------------------------------------
  [1단계] 시장 상태
      NDX 종가가 250일선 대비
        +1% 초과      -> 상승(UP)
        -5% 미만      -> 하락(DOWN)
        그 사이       -> 직전 상태 그대로 유지   <-- 이월 규칙

  [2단계] 포지션
      상승 + VIX10 < 28 + 낙폭 9% 이내   -> TQQQ 100%     (3배)
      상승 + (VIX10 >= 28 or 낙폭 > 9%)  -> QQQ50 + QLD50 (1.5배)
      하락 + VIX10 < 18                  -> QQQ50 + QLD50 (1.5배)
      하락 + VIX10 >= 18                 -> 전량 현금     (0배)

  [3단계] 어제와 같으면 아무것도 안 함

  * 판정은 종가 기준, 매매는 '다음 거래일'
================================================================
사용법
    python nasdaq_tripod.py                # GUI 실행 (버튼 3개)
    python nasdaq_tripod.py --cli          # 콘솔로 조회만
    python nasdaq_tripod.py --cli --excel  # 콘솔 조회 + 엑셀 기록
    python nasdaq_tripod.py --history 20   # 신호 변경 이력
    python nasdaq_tripod.py --rules        # 규칙 전문 보기
    python nasdaq_tripod.py --cli --brief  # 항목 설명 없이 짧게
    python nasdaq_tripod.py --demo         # 인터넷 없이 로직 테스트

※ 공개된 규칙을 기계적으로 계산해 보여줄 뿐이며 투자 권유가 아닙니다.
   판단과 책임은 사용자 본인에게 있습니다.
"""

import argparse
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

# ----------------------------------------------------------------
# 규칙 파라미터 (영상에서 공개된 값)
# 화자 본인이 "VIX 28은 최적값이 아니라 내가 감당하기로 정한 위험의 크기"
# 라고 밝혔습니다. 바꾸면 다른 전략이 되므로 의식하고 바꾸세요.
# ----------------------------------------------------------------
MA_PERIOD     = 250
UPPER_BAND    = 0.01
LOWER_BAND    = -0.05
VIX_MA_PERIOD = 10
VIX_HIGH      = 28.0
VIX_LOW       = 18.0
DD_LIMIT      = -0.09
HIGH_52W      = 252

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(BASE, "tripod_data.csv")
META_FILE  = os.path.join(BASE, "tripod_meta.json")
EXCEL_FILE = os.path.join(BASE, "트라이팟_일지.xlsx")

POSITIONS = {
    "TQQQ": ("TQQQ 100%",         "3배"),
    "HALF": ("QQQ 50% + QLD 50%", "1.5배"),
    "CASH": ("전량 현금",          "0배"),
}
STATE_KR = {"UP": "상승", "DOWN": "하락", None: "판정불가"}


# ================================================================
# 1. 데이터 수집 (증분 업데이트)
# ================================================================
def fetch_data(force_full=False, log=print):
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance가 필요합니다: pip install yfinance pandas numpy openpyxl")

    cached = None
    if os.path.exists(DATA_FILE) and not force_full:
        cached = pd.read_csv(DATA_FILE, parse_dates=["date"]).set_index("date")

    if cached is None or cached.empty:
        period, start = "10y", None
        log("캐시 없음 -> 10년치 전체 수신 중...")
    else:
        start = (cached.index.max() - pd.Timedelta(days=10)).date()
        period = None
        missing = (pd.Timestamp.today().normalize() - cached.index.max()).days
        log(f"마지막 기록 {cached.index.max().date()} ({missing}일 전) -> 이후 구간 수신 중...")

    frames = {}
    for label, ticker in (("ndx", "^NDX"), ("vix", "^VIX")):
        kw = dict(progress=False, auto_adjust=False)
        df = yf.download(ticker, period=period, **kw) if period else \
             yf.download(ticker, start=start, **kw)
        if df.empty:
            raise RuntimeError(f"{ticker} 수신 실패. 네트워크를 확인하세요.")
        s = df["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        frames[label] = s

    new = pd.DataFrame(frames).dropna()
    new.index = pd.to_datetime(new.index).tz_localize(None).normalize()
    new.index.name = "date"
    new = drop_unclosed_bar(new)

    if cached is not None and not cached.empty:
        merged = pd.concat([cached, new])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = new.sort_index()

    merged.to_csv(DATA_FILE)
    return merged


def drop_unclosed_bar(df):
    """미국 장중(한국 밤)에 받으면 오늘 봉은 종가가 아니라 '현재가'.
    그대로 두면 장중 가격으로 판정하고 엑셀에도 그 값이 영구 기록되므로 제외."""
    ny = pd.Timestamp.now(tz="America/New_York")
    if len(df) and df.index[-1].date() == ny.date() and (ny.hour, ny.minute) < (16, 15):
        return df.iloc[:-1]
    return df


def make_demo_data(n=1400, seed=7):
    """인터넷 없이 로직만 검증할 때 쓰는 가짜 데이터."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    ndx = 7000 * np.exp(np.cumsum(rng.normal(0.0005, 0.014, n)))
    vix = 12 + 25 * np.abs(rng.normal(0, 0.5, n)) ** 1.5
    vix = pd.Series(vix, index=idx).rolling(4, min_periods=1).mean()
    return pd.DataFrame({"ndx": ndx, "vix": vix.values}, index=idx)


# ================================================================
# 2. 지표 계산 + 상태 판정
# ================================================================
def build(df):
    out = df.copy()
    out["ma250"]  = out["ndx"].rolling(MA_PERIOD).mean()
    out["gap"]    = out["ndx"] / out["ma250"] - 1
    out["high52"] = out["ndx"].rolling(HIGH_52W, min_periods=HIGH_52W).max()
    out["dd"]     = out["ndx"] / out["high52"] - 1
    out["vix10"]  = out["vix"].rolling(VIX_MA_PERIOD).mean()

    # --- 1단계: 상태 이월이 있으므로 반드시 순차 계산 ---
    state, states = None, []
    for g in out["gap"]:
        if pd.isna(g):
            states.append(None)
            continue
        if g > UPPER_BAND:
            state = "UP"
        elif g < LOWER_BAND:
            state = "DOWN"
        # 그 사이면 state를 건드리지 않음 = 직전 상태 유지
        states.append(state)
    out["state"] = states

    # --- 2단계: 포지션 ---
    def pos(r):
        if r["state"] is None or pd.isna(r["vix10"]) or pd.isna(r["dd"]):
            return None
        if r["state"] == "UP":
            if r["vix10"] < VIX_HIGH and r["dd"] >= DD_LIMIT:
                return "TQQQ"
            return "HALF"
        return "HALF" if r["vix10"] < VIX_LOW else "CASH"

    out["pos"] = out.apply(pos, axis=1)

    # --- 3단계: 어제와 비교해서 액션 문구 생성 ---
    prev = out["pos"].shift()
    out["changed"] = out["pos"].ne(prev) & out["pos"].notna() & prev.notna()

    # pandas의 apply는 None을 NaN으로 바꾸므로 문자열만 남기고 정규화
    positions = [p if isinstance(p, str) else None for p in out["pos"]]
    out["pos"] = positions

    def action(i):
        p = positions[i]
        q = positions[i - 1] if i > 0 else None
        if p is None:
            return ""
        if q is None:
            return f"최초 판정: {POSITIONS[p][0]}"
        if p == q:
            return "유지 (매매 없음)"
        return f"변경: {POSITIONS[q][0]} → {POSITIONS[p][0]}  [다음 거래일 실행]"

    out["action"] = [action(i) for i in range(len(out))]
    return out


# ================================================================
# 3. 엑셀 일지 누적 기록
# ================================================================
HEADERS = ["날짜", "NDX 종가", "MA250", "이격도", "VIX", "VIX 10일평균",
           "52주 고점", "52주 낙폭", "시장상태", "규칙 포지션", "배수",
           "해야 할 액션", "기록시각"]


def _style_new_sheet(ws):
    from openpyxl.styles import Font, PatternFill, Alignment
    ws.append(HEADERS)
    fill = PatternFill("solid", fgColor="1F3864")
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    widths = [12, 12, 12, 10, 8, 12, 12, 10, 10, 20, 8, 44, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w


def write_excel(out, path=EXCEL_FILE, log=print):
    """미기록 거래일을 전부 찾아 일지에 누적. 며칠 걸러도 자동 백필."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill

    d = out.dropna(subset=["pos"])
    if d.empty:
        raise RuntimeError("기록할 데이터가 없습니다. 250거래일 이상 필요합니다.")

    if os.path.exists(path):
        try:
            wb = load_workbook(path)
        except PermissionError:
            raise RuntimeError("엑셀 파일이 열려 있습니다. 닫고 다시 시도하세요.")
        ws = wb["일지"] if "일지" in wb.sheetnames else wb.create_sheet("일지")
        if ws.max_row == 0 or ws["A1"].value is None:
            _style_new_sheet(ws)
        done = {str(ws.cell(row=r, column=1).value)[:10]
                for r in range(2, ws.max_row + 1)}
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "일지"
        _style_new_sheet(ws)
        done = set()

    fills = {"TQQQ": PatternFill("solid", fgColor="FCE4E4"),   # 3배   연빨강
             "HALF": PatternFill("solid", fgColor="FFF6DA"),   # 1.5배 연노랑
             "CASH": PatternFill("solid", fgColor="E8E8E8")}   # 현금  회색
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    added = 0
    for idx, r in d.iterrows():
        key = str(idx.date())
        if key in done:
            continue
        ws.append([key, round(float(r.ndx), 2), round(float(r.ma250), 2),
                   float(r.gap), round(float(r.vix), 2), round(float(r.vix10), 2),
                   round(float(r.high52), 2), float(r.dd),
                   STATE_KR.get(r.state, "판정불가"), POSITIONS[r.pos][0], POSITIONS[r.pos][1],
                   r.action, now])
        row = ws.max_row
        for c in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = Font(name="Arial", size=10)
            cell.fill = fills[r.pos]
        ws.cell(row=row, column=4).number_format = "0.00%"
        ws.cell(row=row, column=8).number_format = "0.00%"
        for c in (2, 3, 7):
            ws.cell(row=row, column=c).number_format = "#,##0.00"
        if bool(r.changed):                 # 신호 변경일은 굵게 + 빨강
            for c in range(1, len(HEADERS) + 1):
                ws.cell(row=row, column=c).font = Font(
                    name="Arial", size=10, bold=True, color="C00000")
        added += 1

    try:
        wb.save(path)
    except PermissionError:
        raise RuntimeError("엑셀 파일이 열려 있어 저장할 수 없습니다. 닫고 다시 시도하세요.")

    log(f"엑셀 기록 완료: {added}행 추가 (총 {ws.max_row - 1}행) -> {path}")
    return added


# ================================================================
# 4. 결과 텍스트
# ================================================================
def _disp_w(s):
    """한글은 화면에서 2칸을 차지하므로 정렬용 폭 계산."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def report_text(out, last_seen=None, explain=True):
    """explain=True 면 각 항목마다 '이게 무엇이고 왜 보는지' 설명을 함께 출력."""
    d = out.dropna(subset=["pos"])
    if d.empty:
        return "데이터가 부족합니다. 250거래일 이상 필요합니다."

    t = d.iloc[-1]
    date = d.index[-1].date()
    L = []
    note = (lambda s: L.append(s)) if explain else (lambda s: None)

    L.append("=" * 64)
    L.append(f"  나스닥 트라이팟 판정   |   기준 종가일 {date}")
    L.append("=" * 64)
    note("  판정은 '종가' 기준이고, 실제 매매는 '다음 거래일'에 합니다.")
    note("  세 지표는 각각 추세 방향 / 시장의 공포 / 가격 피로도를 담당하며,")
    note("  하나라도 빼면 다른 전략이 됩니다 (낙폭 조건 제거 시 MDD -80%).")

    # ---------------- [1] 250일선 ----------------
    gap_ok = "상승권" if t.gap > UPPER_BAND else \
             ("하락권" if t.gap < LOWER_BAND else "중립구간(직전 유지)")
    L.append(f"\n[1] 250일 이동평균선  —  추세 방향")
    L.append(f"    NDX {t.ndx:>11,.2f}    MA250 {t.ma250:>11,.2f}")
    L.append(f"    이격도 {t.gap*100:+6.2f}%   (기준 +1% 초과=상승 / -5% 미만=하락)"
             f"  ->  {gap_ok}")
    note("    ※ 지금 시장이 오르는 국면인지 내리는 국면인지만 봅니다.")
    note("       위(+1%)와 아래(-5%)를 다르게 둔 것은 선 하나로 자르면 그 근처에서")
    note("       매매가 반복되기 때문입니다. \"나갈 때는 신중하게, 들어올 때는 빠르게\".")

    # ---------------- [2] VIX 10일평균 ----------------
    v_ok = "낮음" if t.vix10 < VIX_LOW else ("보통" if t.vix10 < VIX_HIGH else "높음")
    L.append(f"\n[2] VIX 10일 평균  —  시장의 공포")
    L.append(f"    VIX10 {t.vix10:>6.2f}   (당일 VIX {t.vix:.2f})")
    L.append(f"    기준 18 / 28  ->  {v_ok}")
    note("    ※ 당일 VIX가 아니라 10일 평균입니다. 하루짜리 급등에 반응하지 않기 위함.")
    note("       28 이상이면 상승장이어도 배수를 줄이고, 하락장에서 18 이상이면 현금입니다.")
    note("       28은 최적값이 아니라 화자가 '감당하기로 정한 위험의 크기'입니다.")

    # ---------------- [3] 52주 낙폭 ----------------
    d_ok = "9% 이내" if t.dd >= DD_LIMIT else "9% 초과"
    L.append(f"\n[3] 52주 고점 대비 낙폭  —  가격 피로도")
    L.append(f"    낙폭 {t.dd*100:+6.2f}%   (52주 고점 {t.high52:,.2f})")
    L.append(f"    기준 -9%  ->  {d_ok}")
    note("    ※ 최대낙폭(MDD)이 아니라 '최근 52주 고점에서 얼마나 내려왔나'입니다.")
    note("       고점 대비 많이 빠진 상태에서 3배를 드는 것을 막는 안전장치입니다.")

    # ---------------- 판정 과정 ----------------
    L.append("\n" + "-" * 64)
    L.append(f"  [1단계] 시장 상태   : {STATE_KR.get(t.state, '판정불가')}")
    if t.gap > UPPER_BAND:
        note(f"          이격도 {t.gap*100:+.2f}% 가 +1% 를 넘음  ->  상승 확정")
    elif t.gap < LOWER_BAND:
        note(f"          이격도 {t.gap*100:+.2f}% 가 -5% 를 밑돎  ->  하락 확정")
    else:
        note(f"          이격도 {t.gap*100:+.2f}% 는 -5% ~ +1% 중립 구간")
        note(f"          ->  새로 판정하지 않고 직전 상태"
             f"({STATE_KR.get(t.state, '판정불가')})를 그대로 이월")

    L.append(f"  [2단계] 규칙 포지션 : {POSITIONS[t.pos][0]}   ({POSITIONS[t.pos][1]})")
    if explain:
        if t.state == "UP":
            m1 = "OK" if t.vix10 < VIX_HIGH else "탈락"
            m2 = "OK" if t.dd >= DD_LIMIT else "탈락"
            c1 = f"VIX10 {t.vix10:.2f} {'<' if t.vix10 < VIX_HIGH else '>='} 28"
            c2 = f"낙폭 {t.dd*100:+.2f}% {'>=' if t.dd >= DD_LIMIT else '<'} -9%"
            L.append(f"          {c1}{' ' * max(1, 26 - _disp_w(c1))}[{m1}]")
            L.append(f"          {c2}{' ' * max(1, 26 - _disp_w(c2))}[{m2}]")
            if t.pos == "TQQQ":
                L.append("          둘 다 충족  ->  3배 (TQQQ 100%)")
            else:
                L.append("          하나라도 어긋나면 1.5배로 축소 (AND 조건)")
        elif t.state == "DOWN":
            if t.pos == "HALF":
                L.append(f"          하락이지만 VIX10 {t.vix10:.2f} < 18")
                L.append("          ->  현금이 아니라 1.5배. '하락 = 현금'이 아닙니다.")
            else:
                L.append(f"          하락 + VIX10 {t.vix10:.2f} >= 18  ->  전량 현금")

    L.append(f"  [3단계] 해야 할 액션: {t.action}")
    note("          어제와 판정이 같으면 아무것도 하지 않습니다.")
    note("          연평균 8.1회, 최장 2년 7개월 무매매도 있는 저빈도 전략입니다.")
    L.append("-" * 64)

    if last_seen:
        gap_df = d[d.index > pd.Timestamp(last_seen)]
        if not gap_df.empty:
            L.append(f"\n  [ 마지막 조회({last_seen}) 이후 {len(gap_df)}거래일 ]")
            ch = gap_df[gap_df["changed"]]
            if ch.empty:
                L.append("    그 사이 신호 변경 없었습니다.")
            else:
                L.append("    !! 그 사이 신호가 바뀐 날이 있습니다:")
                for i, r in ch.iterrows():
                    L.append(f"      {i.date()}  ->  {POSITIONS[r.pos][0]}")
                L.append("    (지나간 신호입니다. 현재 유효한 것은 위 '규칙 포지션')")

    L.append("\n" + "=" * 64)
    L.append("  공개된 규칙을 기계적으로 계산한 결과이며 투자 권유가 아닙니다.")
    if explain:
        L.append("  전체 규칙표를 보려면:  python nasdaq_tripod.py --rules")
        L.append("  설명 없이 짧게 보려면: python nasdaq_tripod.py --cli --brief")
    L.append("=" * 64)
    return "\n".join(L)


def history_text(out, n):
    ch = out[out["changed"]].tail(n)
    L = [f"\n최근 신호 변경 {len(ch)}건", "-" * 60]
    for i, r in ch.iterrows():
        L.append(f"{i.date()}   {STATE_KR.get(r.state, '판정불가'):<4}  "
                 f"이격 {r.gap*100:+6.2f}%  VIX10 {r.vix10:5.2f}  "
                 f"낙폭 {r.dd*100:+6.2f}%   -> {POSITIONS[r.pos][0]}")
    L.append("-" * 60)
    return "\n".join(L)


RULES_TEXT = f"""
================================================================
  트라이팟 규칙 전문 (이 프로그램이 계산하는 내용 전부)
================================================================

■ 언제 보나
  매 거래일 '장 마감 후', 일봉 종가 기준. 분봉은 쓰지 않습니다.
  판정은 종가로 하고, 실제 매매는 다음 거래일에 합니다.
  (종가에 사는 것은 불가능하므로 판정일과 매매일이 다릅니다)

■ 보는 지표 3개
  1) 나스닥100(NDX)의 {MA_PERIOD}일 이동평균선   -> 추세 방향
  2) VIX의 {VIX_MA_PERIOD}일 평균 (당일값 아님)          -> 시장의 공포
  3) 52주 고점 대비 낙폭                  -> 가격 피로도
  * 3번은 최대낙폭(MDD)이 아닙니다. 최근 52주 고점에서의 낙폭입니다.
  * 하나라도 빼면 구조가 무너집니다 (낙폭 조건 제거 시 MDD -80%).

■ 1단계 — 시장 상태
  250일선 대비 +{UPPER_BAND*100:.0f}% 초과   -> 상승(UP)
  250일선 대비 {LOWER_BAND*100:.0f}% 미만    -> 하락(DOWN)
  그 사이 (중립 구간)      -> 직전 상태 그대로 유지
  * 위아래 폭이 다른 이유: 선 하나로 자르면 그 근처에서 매매가 반복됨.
    "나갈 때는 신중하게, 들어올 때는 빠르게"

■ 2단계 — 포지션
  상승 + VIX10 < {VIX_HIGH:.0f} + 낙폭 {abs(DD_LIMIT)*100:.0f}% 이내   -> TQQQ 100%        (3배)
  상승 + (VIX10 >= {VIX_HIGH:.0f} 또는 낙폭 {abs(DD_LIMIT)*100:.0f}% 초과) -> QQQ 50%+QLD 50% (1.5배)
  하락 + VIX10 < {VIX_LOW:.0f}                  -> QQQ 50%+QLD 50% (1.5배)
  하락 + VIX10 >= {VIX_LOW:.0f}                 -> 전량 현금        (0배)
  * 3배로 가려면 두 조건을 '둘 다' 충족해야 합니다 (AND).
    하나라도 어긋나면 1.5배로 축소됩니다 (OR).
  * 하락이어도 VIX가 낮으면 현금이 아니라 1.5배입니다.

■ 3단계 — 어제와 같으면 아무것도 하지 않는다
  연평균 8.1회, 무매매 연도 5년, 최장 2년 7개월 무매매인 저빈도 전략.

■ 규칙을 지키는 대가
  35년간 기어 조정 55회 중 40회는 사후적으로 TQQQ 보유가 나았습니다(승률 27%).
  화자는 이것을 손실이 아니라 극단 구간의 생존을 사는 '보험료'로 규정합니다.

■ 파라미터를 바꾸려면 (파일 상단 상수)
  VIX 문턱을 올리면 수익률은 올라가지만 낙폭도 커집니다.
  28은 최적값이 아니라 화자 개인이 감당하기로 정한 위험의 크기입니다.
  바꾸면 화자가 제시한 32.9% / -62% 와는 다른 전략이 됩니다.

■ 규칙에 명시되지 않아 본인이 정해야 하는 것
  - 다음 거래일 '시가'냐 '종가'냐 (화자는 "다음 거래일"이라고만 함)
  - 시드 규모. 3배 포지션을 전 자산에 적용하라는 뜻이 아닙니다.
    별도 계좌로 운용하고 신뢰도에 따라 금액을 정하라는 것이 화자의 권고입니다.
  - 자금 투입(적립) 방법은 공개 범위 밖입니다.

■ 실무 이슈 (이 프로그램은 계산하지 않음)
  세금(해외주식 양도차익 250만원 공제 후 22%), 환율, 계좌 종류별 레버리지 ETF 가능 여부.

================================================================
  공개된 규칙을 기계적으로 계산할 뿐이며 투자 권유가 아닙니다.
================================================================
"""


def save_meta(out):
    d = out.dropna(subset=["pos"])
    json.dump({"last_seen": str(d.index[-1].date()),
               "last_run": datetime.now().isoformat(timespec="seconds"),
               "last_pos": d.iloc[-1]["pos"]},
              open(META_FILE, "w"), indent=2)


def load_meta():
    if os.path.exists(META_FILE):
        try:
            return json.load(open(META_FILE)).get("last_seen")
        except Exception:
            return None
    return None


# ================================================================
# 5. GUI  (버튼: 조회 / 엑셀 기록 / 이력)
# ================================================================
def run_gui(demo=False):
    import tkinter as tk
    from tkinter import scrolledtext, messagebox

    root = tk.Tk()
    root.title("나스닥 트라이팟 판정기")
    root.geometry("760x640")

    state = {"out": None}
    explain_var = tk.BooleanVar(value=True)

    bar = tk.Frame(root, pady=8)
    bar.pack(fill="x")
    status = tk.Label(root, text="'조회' 를 눌러 시작하세요.", anchor="w",
                      fg="#555", padx=10)
    box = scrolledtext.ScrolledText(root, font=("Consolas", 10), wrap="none")

    def show(msg):
        box.delete("1.0", "end")
        box.insert("1.0", msg)

    def do_fetch():
        status.config(text="데이터 수신 중...")
        root.update()
        try:
            raw = make_demo_data() if demo else \
                  fetch_data(log=lambda m: (status.config(text=m), root.update()))
            out = build(raw)
            state["out"] = out
            show(report_text(out, load_meta(), explain=explain_var.get()))
            if not demo:
                save_meta(out)
            status.config(text="조회 완료. 엑셀에 남기려면 '엑셀 기록'을 누르세요.")
        except Exception as e:
            status.config(text="오류")
            messagebox.showerror("오류", str(e))

    def do_excel():
        if state["out"] is None:
            messagebox.showinfo("안내", "먼저 '조회'를 눌러주세요.")
            return
        try:
            n = write_excel(state["out"], log=lambda m: status.config(text=m))
            messagebox.showinfo("완료", f"{n}행을 기록했습니다.\n\n{EXCEL_FILE}")
        except Exception as e:
            messagebox.showerror("오류", str(e))

    def do_hist():
        if state["out"] is None:
            messagebox.showinfo("안내", "먼저 '조회'를 눌러주세요.")
            return
        show(history_text(state["out"], 30))

    tk.Button(bar, text="조회 (갱신)", command=do_fetch, width=14, height=2,
              bg="#1F3864", fg="white").pack(side="left", padx=8)
    tk.Button(bar, text="엑셀 기록", command=do_excel, width=14, height=2,
              bg="#2E7D32", fg="white").pack(side="left", padx=4)
    def do_rules():
        show(RULES_TEXT)

    def do_toggle():
        if state["out"] is not None:
            show(report_text(state["out"], load_meta(),
                             explain=explain_var.get()))

    tk.Button(bar, text="신호 변경 이력", command=do_hist, width=14,
              height=2).pack(side="left", padx=4)
    tk.Button(bar, text="규칙 전문", command=do_rules, width=12,
              height=2).pack(side="left", padx=4)
    tk.Checkbutton(bar, text="항목 설명 보기", variable=explain_var,
                   command=do_toggle).pack(side="left", padx=10)

    status.pack(fill="x")
    box.pack(fill="both", expand=True, padx=10, pady=8)
    root.mainloop()


# ================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="GUI 없이 콘솔 실행")
    ap.add_argument("--excel", action="store_true", help="CLI에서 엑셀까지 기록")
    ap.add_argument("--demo", action="store_true", help="가짜 데이터로 로직 테스트")
    ap.add_argument("--full", action="store_true", help="캐시 무시하고 전체 재수신")
    ap.add_argument("--history", type=int, metavar="N", help="최근 신호 변경 N건")
    ap.add_argument("--brief", action="store_true", help="항목 설명 없이 짧게 출력")
    ap.add_argument("--rules", action="store_true", help="규칙 전문만 출력하고 종료")
    args = ap.parse_args()

    if args.rules:
        print(RULES_TEXT)
        return

    if not args.cli and args.history is None:
        try:
            import tkinter  # noqa: F401
            return run_gui(demo=args.demo)
        except ImportError:
            print("(tkinter 없음 -> 콘솔 모드로 실행합니다)\n")

    raw = make_demo_data() if args.demo else fetch_data(force_full=args.full)
    out = build(raw)

    if args.history:
        print(history_text(out, args.history))
        return

    print(report_text(out, load_meta(), explain=not args.brief))
    if args.excel:
        write_excel(out)
    if not args.demo:
        save_meta(out)


if __name__ == "__main__":
    main()
