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
    """日付(YYYY-MM-DD_)で始まるシートのみ取得"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    sheet_titles = [ws.title for ws in ss.worksheets()]
    return sorted([s for s in sheet_titles if re.match(r"^\d{4}-\d{2}-\d{2}_", s)])

def load_game_sheet(sheet_name: str):
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)

def update_rows(sheet_name: str, df: pd.DataFrame):
    """対象試合シート全体を上書き保存（該当打席分のみ反映）"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    ws.update([df.columns.values.tolist()] + df.values.tolist())


# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="補足入力（1打席単位）", layout="wide")
st.title("📘 補足入力モード（1打席単位での入力）")

# ========== 1️⃣ 試合シートの選択 ==========
st.header("1. 試合シートを選択")
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

try:
    df = load_game_sheet(sheet_name)
except Exception as e:
    st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

# ========== 2️⃣ 対象イニング・打順を指定 ==========
st.header("2. 対象打席を指定")
col1, col2, col3 = st.columns(3)
with col1:
    inning = st.number_input("イニング", min_value=1, step=1)
with col2:
    top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
with col3:
    order = st.number_input("打順", min_value=1, max_value=9, step=1)

cond = (
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order))
)
subset = df[cond].reset_index()

if len(subset) == 0:
    st.warning("指定した打席データが見つかりません。")
    st.stop()

# ========== 3️⃣ 打席情報の入力（保持 + 自動補完） ==========
st.header("3. 打席情報入力")
if "batter_memory" not in st.session_state:
    st.session_state["batter_memory"] = {}

memory_key = f"{top_bottom}_{order}"

# 自動補完 or 前回情報保持
prev_info = st.session_state["batter_memory"].get(memory_key, {})

colA, colB, colC, colD = st.columns(4)
with colA:
    batter = st.text_input("打者名", value=prev_info.get("batter", subset.iloc[0].get("batter", "")))
with colB:
    batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"], index=["右", "左", "両"].index(prev_info.get("batter_side", "右")))
with colC:
    pitcher = st.text_input("投手名", value=prev_info.get("pitcher", subset.iloc[0].get("pitcher", "")))
with colD:
    pitcher_side = st.selectbox("投手の利き腕", ["右", "左"], index=["右", "左"].index(prev_info.get("pitcher_side", "右")))

colE, colF, colG, colH = st.columns(4)
with colE:
    runner_1b = st.checkbox("一塁走者あり", value=prev_info.get("runner_1b", False))
with colF:
    runner_2b = st.checkbox("二塁走者あり", value=prev_info.get("runner_2b", False))
with colG:
    runner_3b = st.checkbox("三塁走者あり", value=prev_info.get("runner_3b", False))
with colH:
    out_count = st.number_input("アウトカウント", min_value=0, max_value=2, step=1, value=int(prev_info.get("out_count", 0)))

# ========== 4️⃣ 投球情報入力 ==========
st.header("4. 投球情報入力")
st.info("⚾ この打席に属する全投球に対して入力します（Strike / Ball カウント自動計算）")

pitch_rows = []
strike_count, ball_count = 0, 0

for i, row in subset.iterrows():
    st.subheader(f"{i+1}球目 (zone={row.get('zone','')} / pitch_type={row.get('pitch_type','')})")
    pitch_result = st.selectbox(
        f"{i+1}球目の結果",
        ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "牽制", "打席終了"],
        key=f"pitch_{i}_result",
        index=0
    )

    # Strike / Ball カウント計算
    if pitch_result in ["ストライク（見逃し）", "ストライク（空振り）", "ファウル"]:
        strike_count = min(2, strike_count + 1)
    elif pitch_result == "ボール":
        ball_count += 1

    pitch_rows.append({
        "index": row["index"],
        "pitch_result": pitch_result,
        "strike_count": strike_count,
        "ball_count": ball_count
    })

# ========== 5️⃣ 保存処理 ==========
if st.button("💾 この打席を保存"):
    try:
        for pr in pitch_rows:
            df.loc[pr["index"], ["pitch_result", "strike_count", "ball_count"]] = [
                pr["pitch_result"], pr["strike_count"], pr["ball_count"]
            ]
        # 打席情報を更新（全行に反映）
        df.loc[cond, ["batter", "batter_side", "pitcher", "pitcher_side",
                      "runner_1b", "runner_2b", "runner_3b", "out_count"]] = [
            batter, batter_side, pitcher, pitcher_side,
            runner_1b, runner_2b, runner_3b, out_count
        ]

        update_rows(sheet_name, df)
        st.session_state["batter_memory"][memory_key] = {
            "batter": batter,
            "batter_side": batter_side,
            "pitcher": pitcher,
            "pitcher_side": pitcher_side,
            "runner_1b": runner_1b,
            "runner_2b": runner_2b,
            "runner_3b": runner_3b,
            "out_count": out_count
        }
        st.success(f"{inning}回{top_bottom} {order}番 の打席を保存しました！")

    except Exception as e:
        st.error(f"スプレッドシートの更新に失敗しました: {e}")