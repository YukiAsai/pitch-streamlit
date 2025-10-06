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

if "current_pitch_index" not in st.session_state:
    st.session_state.current_pitch_index = 0

# --- 打席情報を保持するセッション ---
if "atbat_info" not in st.session_state:
    st.session_state.atbat_info = {}

# 現在のインデックスの球を取得
if st.session_state.current_pitch_index >= len(subset):
    st.session_state.current_pitch_index = len(subset) - 1  # 保険

current_pitch = subset.iloc[st.session_state.current_pitch_index]
row_index = current_pitch["index"]
target_row = df.loc[row_index]

current_label = f"{st.session_state.current_pitch_index + 1}球目: zone={current_pitch.get('zone','')} | pitch_type={current_pitch.get('pitch_type','')}"
st.success(f"{inning}回{top_bottom} {order}番 の {current_label} を編集中")

# 3️⃣ 補足情報の入力
st.header("3. 補足情報入力（打席＋投球）")

# --- 打席情報 ---
st.subheader("⚾ 打席情報")
colA, colB, colC, colD = st.columns(4)
with colA:
    batter = st.text_input("打者名", value=target_row.get("batter", ""))
with colB:
    batter_side = st.selectbox(
        "打者の利き腕", ["右", "左", "両"],
        index=["右","左","両"].index(target_row.get("batter_side", "右"))
        if target_row.get("batter_side") in ["右","左","両"] else 0
    )
with colC:
    pitcher = st.text_input("投手名", value=target_row.get("pitcher", ""))
with colD:
    pitcher_side = st.selectbox(
        "投手の利き腕", ["右", "左"],
        index=["右","左"].index(target_row.get("pitcher_side", "右"))
        if target_row.get("pitcher_side") in ["右","左"] else 0
    )

# --- ランナー情報（有無チェック） ---
st.subheader("🏃‍♂️ ランナー情報")
colE, colF, colG = st.columns(3)
with colE:
    runner_1b = st.checkbox("一塁走者あり", value=(target_row.get("runner_1b") in ["有", True, "True"]))
with colF:
    runner_2b = st.checkbox("二塁走者あり", value=(target_row.get("runner_2b") in ["有", True, "True"]))
with colG:
    runner_3b = st.checkbox("三塁走者あり", value=(target_row.get("runner_3b") in ["有", True, "True"]))

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
    if st.button("💾 この球を更新（次へ）"):
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
            # 🔹打席情報をセッションに保持（次打席での初期値として使う）
            st.session_state.atbat_info = {
                "batter": batter,
                "batter_side": batter_side,
                "pitcher": pitcher,
                "pitcher_side": pitcher_side,
                "runner_1b": runner_1b,
                "runner_2b": runner_2b,
                "runner_3b": runner_3b,
            }

            st.success(f"{inning}回{top_bottom} {order}番 の {st.session_state.current_pitch_index+1}球目 を更新しました！")

            # ========== 遷移ロジック ==========
            if st.session_state.current_pitch_index < len(subset) - 1:
                # 同じ打席内にまだ球がある
                st.session_state.current_pitch_index += 1
                st.rerun()
            else:
                # 打席の最後の球
                current_inning = inning
                current_tb = top_bottom
                current_order = order

                # 次打者の番号（9→1へ）
                next_order = 1 if current_order == 9 else current_order + 1

                # 同じイニング・表裏・次打者を検索
                df_next = df[
                    (df["inning"].astype(str) == str(current_inning)) &
                    (df["top_bottom"] == current_tb) &
                    (df["order"].astype(str) == str(next_order))
                ]

                if not df_next.empty:
                    # ✅ 次打者が同イニング・同表裏に存在
                    st.session_state.current_pitch_index = 0
                    st.session_state["inning"] = current_inning
                    st.session_state["top_bottom"] = current_tb
                    st.session_state["order"] = next_order
                    st.success(f"{current_inning}回{current_tb} {current_order}番の最後の球 → 次打者（{next_order}番）へ移動します。")
                    st.rerun()
                else:
                    # ✅ 次の打者がいない → イニング切り替え
                    if current_tb == "表":
                        next_tb = "裏"
                        next_inning = current_inning
                    else:
                        next_tb = "表"
                        next_inning = current_inning + 1

                    # 次イニング・1番打者を検索
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
                        st.success(f"{current_inning}回{current_tb} の最後の打者でした → {next_inning}回{next_tb} 1番打者へ移動します。")
                        st.rerun()
                    else:
                        # ✅ 試合終了
                        st.info("試合終了です 🏁")
        else:
            st.error("更新に失敗しました。対象行が見つからない可能性があります。")