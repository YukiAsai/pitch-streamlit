import streamlit as st
import pandas as pd
import gspread
import re
from google.oauth2.service_account import Credentials

# ========= Google Sheets 接続 =========
SPREADSHEET_NAME = "Pitch_Data_2025"

def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

def list_game_sheets():
    """シート一覧のうち、日付(YYYY-MM-DD_)で始まるものだけ返す"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    sheet_titles = [ws.title for ws in ss.worksheets()]
    return sorted([s for s in sheet_titles if re.match(r"^\d{4}-\d{2}-\d{2}_", s)])

def load_game_sheet(sheet_name: str):
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)

def update_row_by_pitch(sheet_name: str, inning: int, top_bottom: str, order: int, pitch_number: int, updates: dict):
    """イニング＋表裏＋打順＋pitch_numberで一致する行を更新"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values:
        return False
    
    header = values[0]
    df = pd.DataFrame(values[1:], columns=header)

    cond = (
        (df["inning"].astype(str) == str(inning)) &
        (df["top_bottom"] == top_bottom) &
        (df["order"].astype(str) == str(order)) &
        (df["pitch_number"].astype(str) == str(pitch_number))
    )
    match_idx = df[cond].index

    if match_idx.empty:
        return False
    
    row_number = match_idx[0] + 2  # header行考慮
    for key, val in updates.items():
        if key in header:
            col_idx = header.index(key) + 1
            ws.update_cell(row_number, col_idx, val)
    return True


# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="補足入力（試合後編集）", layout="wide")
st.title("📘 補足入力モード（1球ごとの追加編集）")

# 1️⃣ 対象試合の選択
st.header("1. 試合選択")

try:
    game_sheets = list_game_sheets()
except Exception as e:
    st.error(f"スプレッドシートの取得に失敗しました: {e}")
    st.stop()

if not game_sheets:
    st.warning("日付形式（YYYY-MM-DD_）のシートが見つかりません。")
    st.stop()

sheet_name = st.selectbox("試合シートを選択", game_sheets)
if not sheet_name:
    st.stop()

# データ読み込み
try:
    df = load_game_sheet(sheet_name)
except Exception as e:
    st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

st.dataframe(df, use_container_width=True)


# 2️⃣ 編集対象を指定
st.header("2. 編集対象（イニング・打順・球数）")

col1, col2, col3, col4 = st.columns(4)
with col1:
    inning = st.number_input("イニング", min_value=1, step=1)
with col2:
    top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
with col3:
    order = st.number_input("打順", min_value=1, max_value=9, step=1)
with col4:
    pitch_number = st.number_input("何球目", min_value=1, step=1)

# 条件で対象行を取得
cond = (
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order)) &
    (df["pitch_number"].astype(str) == str(pitch_number))
)
target = df[cond]

if len(target) == 0:
    st.warning("一致する1球が見つかりません。")
    st.stop()
else:
    target_row = target.iloc[0]
    st.success(f"{inning}回{top_bottom} {order}番 {pitch_number}球目 を編集中")


# 3️⃣ 打席・投球情報の補足入力
st.header("3. 補足情報入力（打席＋投球）")

# --- 打席情報 ---
st.subheader("⚾ 打席情報")
colA, colB, colC, colD = st.columns(4)
with colA:
    batter = st.text_input("打者名", value=target_row.get("batter", ""))
with colB:
    batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"], index=0 if target_row.get("batter_side","右")=="右" else 1)
with colC:
    pitcher = st.text_input("投手名", value=target_row.get("pitcher", ""))
with colD:
    pitcher_side = st.selectbox("投手の利き腕", ["右", "左"], index=0 if target_row.get("pitcher_side","右")=="右" else 1)

colE, colF, colG = st.columns(3)
with colE:
    runner_1b = st.text_input("一塁走者", value=target_row.get("runner_1b", ""))
with colF:
    runner_2b = st.text_input("二塁走者", value=target_row.get("runner_2b", ""))
with colG:
    runner_3b = st.text_input("三塁走者", value=target_row.get("runner_3b", ""))

# --- 投球情報 ---
st.subheader("🎯 投球情報")
pitch_result = st.selectbox(
    "球の結果",
    ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "打席終了"],
    index=0
)
atbat_result = st.text_input("打席結果（例: 左中2塁打）", value=target_row.get("atbat_result", ""))
batted_type = st.selectbox("打球種別", ["", "フライ", "ゴロ", "ライナー"], index=0)
batted_position = st.selectbox("打球方向", ["", "投手", "一塁", "二塁", "三塁", "遊撃", "左翼", "中堅", "右翼", "左中", "右中"], index=0)
batted_outcome = st.selectbox("打球結果", ["", "ヒット","2塁打","3塁打","ホームラン", "アウト", "エラー", "併殺", "犠打", "犠飛"], index=0)
strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ","盗塁","バスター"], index=0)
strategy_result = st.selectbox("作戦結果", ["", "成", "否"], index=0)

# --- 保存 ---
if st.button("この1球の情報を更新"):
    updates = {
        "batter": batter,
        "batter_side": batter_side,
        "pitcher": pitcher,
        "pitcher_side": pitcher_side,
        "runner_1b": runner_1b,
        "runner_2b": runner_2b,
        "runner_3b": runner_3b,
        "pitch_result": pitch_result,
        "atbat_result": atbat_result,
        "batted_type": batted_type,
        "batted_position": batted_position,
        "batted_outcome": batted_outcome,
        "strategy": strategy,
        "strategy_result": strategy_result,
    }

    ok = update_row_by_pitch(sheet_name, inning, top_bottom, order, pitch_number, updates)
    if ok:
        st.success(f"{inning}回{top_bottom} {order}番 {pitch_number}球目 を更新しました！")
    else:
        st.error("更新に失敗しました。対象行が見つからない可能性があります。")