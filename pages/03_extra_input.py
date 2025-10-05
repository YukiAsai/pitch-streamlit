# ==============================
# 📘 03_extra_input.py
# ==============================
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

def list_game_sheets():
    """スプレッドシート内の全シート名を返す"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    return [ws.title for ws in ss.worksheets()]

def load_game_sheet(sheet_name: str):
    """試合シートを DataFrame として取得"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)

def update_row(sheet_name: str, inning: int, top_bottom: str, order: int, updates: dict):
    """イニング・表裏・打順に一致する全行を更新"""
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
        (df["order"].astype(str) == str(order))
    )
    match_idx = df[cond].index

    if match_idx.empty:
        return False
    
    for i in match_idx:
        row_number = i + 2  # header分補正
        for key, val in updates.items():
            if key in header:
                col_idx = header.index(key) + 1
                ws.update_cell(row_number, col_idx, val)
    return True

# ========= Streamlit ページ設定 =========
st.set_page_config(page_title="補足入力（試合後編集）", layout="wide")
st.title("📘 補足入力モード（試合後編集）")

# 1️⃣ 試合を選択
st.header("1. 試合を選択")

try:
    sheets = list_game_sheets()
except Exception as e:
    st.error(f"シート一覧の取得に失敗しました: {e}")
    st.stop()

if not sheets:
    st.warning("まだ記録された試合がありません。")
    st.stop()

sheet_name = st.selectbox("対象試合を選択", sheets)
df = load_game_sheet(sheet_name)
if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

# 2️⃣ 打席選択（イニング×表裏×打順）
st.header("2. 編集する打席を選択")

bat_candidates = df[["inning", "top_bottom", "order"]].drop_duplicates()
bat_candidates["label"] = bat_candidates.apply(lambda r: f"{r['inning']}回{r['top_bottom']} {r['order']}番", axis=1)
sel_label = st.selectbox("補足対象の打席", bat_candidates["label"])

sel_row = bat_candidates[bat_candidates["label"] == sel_label].iloc[0]
inning = sel_row["inning"]
top_bottom = sel_row["top_bottom"]
order = sel_row["order"]

target_rows = df[
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order))
]

st.info(f"対象：{inning}回{top_bottom} {order}番（{len(target_rows)}球）")

# 3️⃣ Simple Input データのプレビュー
st.subheader("既存データプレビュー")
st.dataframe(target_rows, use_container_width=True)

# 4️⃣ フォーム（既存値を初期値に）
st.header("3. 不足情報を補足入力")

# 打席情報
st.markdown("### 打席情報")
batter = st.text_input("打者名", value=target_rows["batter"].dropna().iloc[0] if "batter" in target_rows and target_rows["batter"].any() else "")
batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"], index=0)
pitcher = st.text_input("投手名", value=target_rows["pitcher"].dropna().iloc[0] if "pitcher" in target_rows and target_rows["pitcher"].any() else "")
pitcher_side = st.selectbox("投手の利き腕", ["右", "左"], index=0)
runner_1b = st.text_input("一塁走者", value=target_rows["runner_1b"].dropna().iloc[0] if "runner_1b" in target_rows and target_rows["runner_1b"].any() else "")
runner_2b = st.text_input("二塁走者", value=target_rows["runner_2b"].dropna().iloc[0] if "runner_2b" in target_rows and target_rows["runner_2b"].any() else "")
runner_3b = st.text_input("三塁走者", value=target_rows["runner_3b"].dropna().iloc[0] if "runner_3b" in target_rows and target_rows["runner_3b"].any() else "")

# 打席結果
st.markdown("### 打席結果")
atbat_result = st.text_input("打席結果（例: 左中2塁打）", value=target_rows["atbat_result"].dropna().iloc[0] if "atbat_result" in target_rows and target_rows["atbat_result"].any() else "")
batted_position = st.text_input("打球方向", value=target_rows["batted_position"].dropna().iloc[0] if "batted_position" in target_rows and target_rows["batted_position"].any() else "")
batted_outcome = st.text_input("打球結果", value=target_rows["batted_outcome"].dropna().iloc[0] if "batted_outcome" in target_rows and target_rows["batted_outcome"].any() else "")
strategy_result = st.selectbox("作戦結果", ["", "成", "否"], index=0)

# 5️⃣ 更新
if st.button("この打席を更新"):
    updates = {
        "batter": batter,
        "batter_side": batter_side,
        "pitcher": pitcher,
        "pitcher_side": pitcher_side,
        "runner_1b": runner_1b,
        "runner_2b": runner_2b,
        "runner_3b": runner_3b,
        "atbat_result": atbat_result,
        "batted_position": batted_position,
        "batted_outcome": batted_outcome,
        "strategy_result": strategy_result,
    }

    ok = update_row(sheet_name, inning, top_bottom, order, updates)
    if ok:
        st.success(f"{inning}回{top_bottom} {order}番 の補足情報を更新しました！")
        st.dataframe(load_game_sheet(sheet_name), use_container_width=True)
    else:
        st.error("更新に失敗しました。対象の行が見つかりません。")