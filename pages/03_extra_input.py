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

def update_row_by_index(sheet_name: str, row_index: int, updates: dict):
    """DataFrame上の行番号に対応するスプレッドシート行を更新"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values:
        return False

    header = values[0]
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

# 条件で絞り込み
cond = (
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order))
)
subset = df[cond]

if len(subset) == 0:
    st.warning("指定条件に一致する球が見つかりません。")
    st.stop()

# ⚾ 並び順を「古い順（上から順）」に固定
subset = subset.reset_index()  # 元の行番号を保持
subset_display = [
    f"{i+1}球目: zone={row.get('zone','')} | pitch_type={row.get('pitch_type','')}"
    for i, (_, row) in enumerate(subset.iterrows())
]

if "current_pitch_index" not in st.session_state:
    st.session_state.current_pitch_index = 0

choice = st.selectbox(
    "補足したい球を選択",
    subset_display,
    index=st.session_state.current_pitch_index
)

row_index = subset.loc[subset_display.index(choice), "index"]
target_row = df.loc[row_index]
st.success(f"{inning}回{top_bottom} {order}番 の {choice} を編集中")

# 3️⃣ 補足情報の入力
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
    ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "牽制", "打席終了"],
    index=0
)

# 打席終了時のみ表示
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
    batted_type = ""
    batted_position = ""
    batted_outcome = ""

# --- 保存＆次へ ---
col_save, col_next = st.columns([2, 1])
with col_save:
    if st.button("💾 この球を更新"):
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

        ok = update_row_by_index(sheet_name, row_index, updates)
        if ok:
            st.success(f"{inning}回{top_bottom} {order}番 の {choice} を更新しました！")
        else:
            st.error("更新に失敗しました。対象行が見つからない可能性があります。")

with col_next:
    if st.button("➡ 次の球へ"):
        # まず同じ打席内で次の球があるか
        if st.session_state.current_pitch_index < len(subset_display) - 1:
            st.session_state.current_pitch_index += 1
            st.rerun()
        else:
            # 現在の打順と表裏・イニングを取得
            current_order = order
            current_tb = top_bottom
            current_inning = inning

            # 次の打順を計算（9の次は1）
            next_order = 1 if current_order == 9 else current_order + 1

            # 同じイニング・表裏で次打者を探す
            df_next = df[
                (df["inning"].astype(str) == str(current_inning)) &
                (df["top_bottom"] == current_tb) &
                (df["order"].astype(str) == str(next_order))
            ]

            if not df_next.empty:
                st.session_state.current_pitch_index = 0
                st.session_state["next_inning"] = current_inning
                st.session_state["next_top_bottom"] = current_tb
                st.session_state["next_order"] = next_order

                st.success(f"{current_inning}回{current_tb} {current_order}番の最後の球です → 次打者（{next_order}番）へ移動します。")
                st.rerun()
            else:
                # 同じイニングで次打者がいなければ、表裏を進める
                if current_tb == "表":
                    next_tb = "裏"
                    next_inning = current_inning
                else:
                    next_tb = "表"
                    next_inning = current_inning + 1

                df_next_tb = df[
                    (df["inning"].astype(str) == str(next_inning)) &
                    (df["top_bottom"] == next_tb) &
                    (df["order"].astype(str) == "1")
                ]

                if not df_next_tb.empty:
                    st.session_state.current_pitch_index = 0
                    st.session_state["next_inning"] = next_inning
                    st.session_state["next_top_bottom"] = next_tb
                    st.session_state["next_order"] = 1

                    st.success(f"{current_inning}回{current_tb} の最後の打者でした → {next_inning}回{next_tb} 1番打者へ移動します。")
                    st.rerun()
                else:
                    st.info("試合終了です 🏁")