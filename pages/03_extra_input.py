import streamlit as st
import pandas as pd
import gspread
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
st.title("📘 補足入力モード（試合後編集）")

# 1️⃣ 試合を特定する入力
st.header("1. 対象試合を特定")
colA, colB, colC = st.columns(3)
with colA:
    game_date = st.date_input("試合日")
with colB:
    top_team = st.text_input("先攻チーム名")
with colC:
    bottom_team = st.text_input("後攻チーム名")

if game_date and top_team and bottom_team:
    sheet_name = f"{game_date.strftime('%Y-%m-%d')}_{top_team.strip()}_vs_{bottom_team.strip()}"
    st.info(f"対象シート名：**{sheet_name}**")
else:
    st.warning("試合日・先攻・後攻をすべて入力してください。")
    st.stop()

# 2️⃣ スプレッドシートからデータ読み込み
try:
    df = load_game_sheet(sheet_name)
except Exception as e:
    st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

st.dataframe(df, use_container_width=True)

# 3️⃣ 編集対象を指定
st.header("2. 編集対象の指定")
col1, col2, col3, col4 = st.columns(4)
with col1:
    inning = st.number_input("イニング", min_value=1, step=1)
with col2:
    top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
with col3:
    order = st.number_input("打順", min_value=1, max_value=9, step=1)
with col4:
    pitch_number = st.number_input("何球目", min_value=1, step=1)

cond = (
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order)) &
    (df["pitch_number"].astype(str) == str(pitch_number))
)
target = df[cond]

if len(target) == 0:
    st.warning("一致する行が見つかりません。")
    st.stop()
else:
    target_row = target.iloc[0]
    st.success(f"{inning}回{top_bottom} {order}番 {pitch_number}球目 を編集中")

# 4️⃣ 補足情報の入力
st.header("3. 補足情報を入力")

batter = st.text_input("打者名", value=target_row.get("batter", ""))
pitcher = st.text_input("投手名", value=target_row.get("pitcher", ""))
pitch_result = st.selectbox("球の結果", ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "打席終了"], index=0)
atbat_result = st.text_input("打席結果（例: 左中2塁打）", value=target_row.get("atbat_result", ""))
batted_position = st.text_input("打球方向", value=target_row.get("batted_position", ""))
batted_outcome = st.text_input("打球結果", value=target_row.get("batted_outcome", ""))
strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ", "盗塁", "バスター"], index=0)
strategy_result = st.selectbox("作戦結果", ["", "成", "否"], index=0)

if st.button("この行を更新"):
    updates = {
        "batter": batter,
        "pitcher": pitcher,
        "pitch_result": pitch_result,
        "atbat_result": atbat_result,
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