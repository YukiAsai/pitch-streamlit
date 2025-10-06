import streamlit as st
import pandas as pd
import gspread
import re
from google.oauth2.service_account import Credentials

# ========= Google Sheets 接続 =========
SPREADSHEET_NAME = "Pitch_Data_2025"

# --- 認証（共通化） ---
@st.cache_resource
def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    return gspread.authorize(creds)

# --- シート一覧取得（キャッシュ付き） ---
@st.cache_data(ttl=120)
def list_game_sheets_cached():
    ss = _gs_client().open(SPREADSHEET_NAME)
    sheet_titles = [ws.title for ws in ss.worksheets()]
    # 日付形式のみ抽出
    return sorted([s for s in sheet_titles if re.match(r"^\d{4}-\d{2}-\d{2}_", s)])

# --- シート読み込み（キャッシュ付き） ---
@st.cache_data(ttl=60)
def load_game_sheet_cached(sheet_name: str):
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)

# --- 1行更新（軽量化版） ---
@st.cache_resource
def get_header(sheet_name: str):
    """ヘッダーを一度だけ取得しキャッシュ"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    return values[0] if values else []

def update_row_by_index(sheet_name: str, row_index: int, updates: dict):
    """DataFrame上の行番号に対応するスプレッドシート行を更新"""
    header = get_header(sheet_name)
    if not header:
        return False
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    row_number = row_index + 2  # header行を考慮
    for key, val in updates.items():
        if key in header:
            col_idx = header.index(key) + 1
            ws.update_cell(row_number, col_idx, val)
    return True

# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="補足入力（試合後編集）", layout="wide")
st.title("📘 補足入力モード（1球ごとの追記・修正）")

# 1️⃣ 試合シートの選択
st.header("1. 試合選択")
try:
    game_sheets = list_game_sheets_cached()
except Exception as e:
    st.error(f"スプレッドシートの取得に失敗しました: {e}")
    st.stop()

if not game_sheets:
    st.warning("日付形式（YYYY-MM-DD_）のシートが見つかりません。")
    st.stop()

sheet_name = st.selectbox("試合シートを選択", game_sheets)
if not sheet_name:
    st.stop()

# 読み込みボタンで明示的にロード（API節約）
if st.button("📥 データを読み込む / 更新"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

try:
    df = load_game_sheet_cached(sheet_name)
except Exception as e:
    st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

st.dataframe(df, use_container_width=True)

# 2️⃣ 編集対象を指定
st.header("2. 編集対象（イニング・打順で絞り込み）")
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
subset = df[cond]

if len(subset) == 0:
    st.warning("指定条件に一致する球が見つかりません。")
    st.stop()

# ⚾ 並び順を固定
subset = subset.reset_index()
if "current_pitch_index" not in st.session_state:
    st.session_state.current_pitch_index = 0
if "atbat_info" not in st.session_state:
    st.session_state.atbat_info = {}

# 現在球
if st.session_state.current_pitch_index >= len(subset):
    st.session_state.current_pitch_index = len(subset) - 1

current_pitch = subset.iloc[st.session_state.current_pitch_index]
row_index = current_pitch["index"]
target_row = df.loc[row_index]

st.success(
    f"{inning}回{top_bottom} {order}番 の {st.session_state.current_pitch_index+1}球目 "
    f"(zone={current_pitch.get('zone','')}, pitch_type={current_pitch.get('pitch_type','')}) を編集中"
)

# 3️⃣ 補足情報入力
st.header("3. 補足情報入力（打席＋投球）")

# --- 打席情報（保持型） ---
st.subheader("⚾ 打席情報")

# 現在の打席（イニング＋表裏＋打順）を識別
current_atbat_key = f"{inning}-{top_bottom}-{order}"

# もし別の打席に切り替わったらリセット
if "last_atbat_key" not in st.session_state or st.session_state.last_atbat_key != current_atbat_key:
    st.session_state.last_atbat_key = current_atbat_key
    st.session_state.atbat_info = {
        "batter": target_row.get("batter", ""),
        "batter_side": target_row.get("batter_side", "右"),
        "pitcher": target_row.get("pitcher", ""),
        "pitcher_side": target_row.get("pitcher_side", "右"),
        "runner_1b": target_row.get("runner_1b", False),
        "runner_2b": target_row.get("runner_2b", False),
        "runner_3b": target_row.get("runner_3b", False),
    }

colA, colB, colC, colD = st.columns(4)
with colA:
    batter = st.text_input("打者名", value=st.session_state.atbat_info["batter"], key="batter_input")
with colB:
    try:
        batter_side_index = ["右", "左", "両"].index(st.session_state.atbat_info["batter_side"])
    except ValueError:
        batter_side_index = 0
    batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"], index=batter_side_index, key="batter_side_input")
with colC:
    pitcher = st.text_input("投手名", value=st.session_state.atbat_info["pitcher"], key="pitcher_input")
with colD:
    try:
        pitcher_side_index = ["右", "左"].index(st.session_state.atbat_info["pitcher_side"])
    except ValueError:
        pitcher_side_index = 0
    pitcher_side = st.selectbox("投手の利き腕", ["右", "左"], index=pitcher_side_index, key="pitcher_side_input")

# --- ランナー情報（有無チェック） ---
st.subheader("🏃‍♂️ ランナー情報")
colE, colF, colG = st.columns(3)
with colE:
    runner_1b = st.checkbox(
        "一塁走者あり",
        value=bool(st.session_state.atbat_info.get("runner_1b", False)),
        key="runner_1b_input"
    )
with colF:
    runner_2b = st.checkbox(
        "二塁走者あり",
        value=bool(st.session_state.atbat_info.get("runner_2b", False)),
        key="runner_2b_input"
    )
with colG:
    runner_3b = st.checkbox(
        "三塁走者あり",
        value=bool(st.session_state.atbat_info.get("runner_3b", False)),
        key="runner_3b_input"
    )

# 🔹 変更があったらセッションに反映（同打席中は維持される）
st.session_state.atbat_info.update({
    "batter": batter,
    "batter_side": batter_side,
    "pitcher": pitcher,
    "pitcher_side": pitcher_side,
    "runner_1b": runner_1b,
    "runner_2b": runner_2b,
    "runner_3b": runner_3b,
})
# --- 投球情報 ---
st.subheader("🎯 投球情報")
pitch_result = st.selectbox(
    "球の結果",
    ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "牽制", "打席終了"],
    index=0
)

if pitch_result == "打席終了":
    atbat_result = st.selectbox(
        "打席結果",
        ["", "三振(見)", "三振(空)", "四球", "死球", "インプレー", "その他"],
        index=0
    )
else:
    atbat_result = ""

if atbat_result == "インプレー":
    st.markdown("**【インプレー詳細入力】**")
    batted_type = st.selectbox("打球の種類", ["フライ", "ゴロ", "ライナー"], index=0)
    batted_position = st.selectbox("打球方向", ["投手", "一塁", "二塁", "三塁", "遊撃", "左翼", "中堅", "右翼", "左中", "右中"], index=0)
    batted_outcome = st.selectbox("打球結果", ["ヒット", "2塁打", "3塁打", "ホームラン", "アウト", "エラー", "併殺", "犠打", "犠飛"], index=0)
else:
    batted_type, batted_position, batted_outcome = "", "", ""

# --- 保存＆次へ ---
col_save, col_next = st.columns([2, 1])
with col_save:
    if st.button("💾 この球を更新（次へ）"):
        updates = {
            "batter": batter,
            "batter_side": batter_side,
            "pitcher": pitcher,
            "pitcher_side": pitcher_side,
            "runner_1b": "有" if runner_1b else "無",
            "runner_2b": "有" if runner_2b else "無",
            "runner_3b": "有" if runner_3b else "無",
            "pitch_result": pitch_result,
            "atbat_result": atbat_result,
            "batted_type": batted_type,
            "batted_position": batted_position,
            "batted_outcome": batted_outcome,
        }

        ok = update_row_by_index(sheet_name, row_index, updates)
        if ok:
            st.session_state.atbat_info = {
                "batter": batter,
                "batter_side": batter_side,
                "pitcher": pitcher,
                "pitcher_side": pitcher_side,
            }
            st.success(f"{inning}回{top_bottom} {order}番 の {st.session_state.current_pitch_index+1}球目 を更新しました！")

            # 次の球 or 次の打者へ
            if st.session_state.current_pitch_index < len(subset) - 1:
                st.session_state.current_pitch_index += 1
                st.rerun()
            else:
                next_order = 1 if order == 9 else order + 1
                df_next = df[
                    (df["inning"].astype(str) == str(inning)) &
                    (df["top_bottom"] == top_bottom) &
                    (df["order"].astype(str) == str(next_order))
                ]
                if not df_next.empty:
                    st.session_state.current_pitch_index = 0
                    st.session_state["order"] = next_order
                    st.success(f"→ 次打者（{next_order}番）へ移動します。")
                    st.rerun()
                else:
                    if top_bottom == "表":
                        next_tb, next_inning = "裏", inning
                    else:
                        next_tb, next_inning = "表", inning + 1
                    df_next_inning = df[
                        (df["inning"].astype(str) == str(next_inning)) &
                        (df["top_bottom"] == next_tb) &
                        (df["order"].astype(str) == "1")
                    ]
                    if not df_next_inning.empty:
                        st.session_state.current_pitch_index = 0
                        st.session_state["inning"] = next_inning
                        st.session_state["top_bottom"] = next_tb
                        st.session_state["order"] = 1
                        st.success(f"→ {next_inning}回{next_tb} 1番打者へ移動します。")
                        st.rerun()
                    else:
                        st.info("試合終了です 🏁")
        else:
            st.error("更新に失敗しました。対象行が見つからない可能性があります。")