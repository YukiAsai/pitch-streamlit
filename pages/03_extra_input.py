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

def update_row_by_index(sheet_name: str, row_index: int, updates: dict):
    """行番号で直接更新（初回→順次入力用）"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values or row_index >= len(values):
        return False

    header = values[0]
    row_number = row_index + 2  # header行考慮
    for key, val in updates.items():
        if key in header:
            col_idx = header.index(key) + 1
            ws.update_cell(row_number, col_idx, val)
    return True

# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="補足入力モード（試合後編集）", layout="wide")
st.title("📘 補足入力モード（試合後編集）")

# ===== 進行状況バー（スクロール固定版） =====

# セッション初期化
if "current_inning" not in st.session_state:
    st.session_state.current_inning = 1
if "current_top_bottom" not in st.session_state:
    st.session_state.current_top_bottom = "表"
if "current_order" not in st.session_state:
    st.session_state.current_order = 1
if "current_game_date" not in st.session_state:
    st.session_state.current_game_date = ""
if "current_top_team" not in st.session_state:
    st.session_state.current_top_team = ""
if "current_bottom_team" not in st.session_state:
    st.session_state.current_bottom_team = ""

# 試合名
if st.session_state.current_game_date and st.session_state.current_top_team and st.session_state.current_bottom_team:
    match_label = f"{st.session_state.current_game_date}　{st.session_state.current_top_team} vs {st.session_state.current_bottom_team}"
else:
    match_label = "試合情報未設定"

# 固定バーCSS
st.markdown("""
    <style>
    .fixed-header {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        z-index: 1000;
        background-color: #f0f2f6;
        border-bottom: 1px solid #ddd;
        padding: 10px 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 18px;
        font-weight: 600;
        height: 50px;
    }
    .main > div:first-child { margin-top: 60px; }
    </style>
""", unsafe_allow_html=True)

# 固定ヘッダー表示
st.markdown(
    f"""
    <div class="fixed-header">
        <div>
            🧾 <span style="color:#1f77b4;">{st.session_state.current_inning}回{st.session_state.current_top_bottom}</span>　
            👤 <span style="color:#2ca02c;">{st.session_state.current_order}番打者</span>
        </div>
        <div style="color:#555;font-size:16px;">{match_label}</div>
    </div>
    """,
    unsafe_allow_html=True
)

# ===== 試合選択 =====
st.header("1. 対象試合を選択")

client = _gs_client()
spreadsheet = client.open(SPREADSHEET_NAME)
all_sheets = [ws.title for ws in spreadsheet.worksheets()]
valid_sheets = [s for s in all_sheets if s[:4].isdigit()]  # yyyyから始まるもののみ

if not valid_sheets:
    st.error("有効な試合データシートが見つかりません。")
    st.stop()

sheet_name = st.selectbox("試合を選択", sorted(valid_sheets))
st.session_state.current_game_date = sheet_name.split("_")[0] if "_" in sheet_name else ""
if "_" in sheet_name:
    parts = sheet_name.split("_")
    if len(parts) >= 3:
        st.session_state.current_top_team = parts[1]
        st.session_state.current_bottom_team = parts[3] if len(parts) > 3 else parts[2]

df = load_game_sheet(sheet_name)
if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

st.dataframe(df, use_container_width=True)

# ===== 編集対象（順次進行） =====
st.header("2. 編集対象を順次入力")

# セッションに現在の行番号を保持
if "current_row_index" not in st.session_state:
    st.session_state.current_row_index = 0

if st.session_state.current_row_index >= len(df):
    st.success("✅ 試合終了：すべての球の入力が完了しました。")
    st.stop()

target_row = df.iloc[st.session_state.current_row_index]
inning = target_row.get("inning", "?")
top_bottom = target_row.get("top_bottom", "?")
order = target_row.get("order", "?")

st.info(f"{inning}回{top_bottom}　{order}番打者　（{st.session_state.current_row_index+1}球目）を編集中")

# ===== 入力フォーム =====
st.header("3. 補足情報を入力")
# --- 打席情報 ---
st.subheader("⚾ 打席情報")

# 1行目：打者〜投手情報（4カラム）
colA, colB, colC, colD = st.columns(4)
with colA:
    batter = st.text_input("打者名", value=target_row.get("batter", ""))
with colB:
    batter_side = st.selectbox(
        "打者の利き腕",
        ["右", "左", "両"],
        index=["右", "左", "両"].index(target_row.get("batter_side", "右"))
        if target_row.get("batter_side") in ["右", "左", "両"]
        else 0,
    )
with colC:
    pitcher = st.text_input("投手名", value=target_row.get("pitcher", ""))
with colD:
    pitcher_side = st.selectbox(
        "投手の利き腕",
        ["右", "左"],
        index=["右", "左"].index(target_row.get("pitcher_side", "右"))
        if target_row.get("pitcher_side") in ["右", "左"]
        else 0,
    )

# 2行目：走者情報（3カラム）
colE, colF, colG = st.columns(3)
with colE:
    runner_1b = st.text_input("一塁走者", value=target_row.get("runner_1b", ""))
with colF:
    runner_2b = st.text_input("二塁走者", value=target_row.get("runner_2b", ""))
with colG:
    runner_3b = st.text_input("三塁走者", value=target_row.get("runner_3b", ""))

# --- 1球情報 ---
st.subheader("⚾ 1球情報")

pitch_result = st.selectbox(
    "球の結果",
    ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "牽制", "打席終了"],
    index=0
)

if pitch_result == "打席終了":
    atbat_result = st.selectbox("打席結果", ["", "三振(見)", "三振(空)", "四球", "死球", "インプレー", "その他"], index=0)
else:
    atbat_result = ""

batted_type = ""
batted_position = ""
batted_outcome = ""
if atbat_result == "インプレー":
    st.markdown("**【インプレー詳細入力】**")
    batted_type = st.selectbox("打球の種類", ["", "フライ", "ゴロ", "ライナー"], index=0)
    batted_position = st.selectbox("打球方向", ["", "投手", "一塁", "二塁", "三塁", "遊撃", "左翼", "中堅", "右翼", "左中", "右中"], index=0)
    batted_outcome = st.selectbox("結果", ["", "ヒット", "2塁打", "3塁打", "ホームラン", "アウト", "エラー", "併殺", "犠打", "犠飛"], index=0)

if st.button("この球を更新して次へ"):
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
    }

    ok = update_row_by_index(sheet_name, st.session_state.current_row_index, updates)
    if ok:
        st.success("この球を更新しました！")
        st.session_state.current_row_index += 1  # 次の球へ進む
        st.rerun()
    else:
        st.error("更新に失敗しました。")