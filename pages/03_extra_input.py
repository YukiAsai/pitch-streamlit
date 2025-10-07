# pages/03_extra_input.py
import streamlit as st
import pandas as pd
import re
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# =========================
# Google Sheets 接続まわり
# =========================
SPREADSHEET_NAME = "Pitch_Data_2025"

def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

@st.cache_data(show_spinner=False, ttl=60)
def list_game_sheets():
    """YYYY-MM-DD_ で始まるタイトルだけを抽出して返す（昇順）。"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    titles = [ws.title for ws in ss.worksheets()]
    return sorted([t for t in titles if re.match(r"^\d{4}-\d{2}-\d{2}_", t)])

@st.cache_data(show_spinner=False, ttl=60)
def load_sheet(sheet_name: str) -> pd.DataFrame:
    """対象シートを DataFrame で取得。空なら空DF。"""
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df

def batch_update_rows(sheet_name: str, row_indices_0based: list[int], updates_list: list[dict]) -> None:
    """
    DataFrame 中の 0-based 行インデックス配列と同数の更新 dict を受け取り、
    gspread の batch_update でまとめて書き込む（高効率・クォータ対策）。
    - 各 updates は {列名: 値, ...}
    """
    if not row_indices_0based:
        return
    ss = _gs_client().open(SPREADSHEET_NAME)
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values:
        return
    header = values[0]
    # 1セルずつ update_cell せず、値レンジでまとめて置換する
    # 行単位で range 指定を作る
    requests = []
    for df_idx, up in zip(row_indices_0based, updates_list):
        sheet_row = df_idx + 2  # 1-based + header 行を考慮
        row_vals = [values[sheet_row-1][i] if i < len(values[sheet_row-1]) else "" for i in range(len(header))]
        # 既存行の必要カラムを新値で上書き
        for k, v in up.items():
            if k in header:
                col = header.index(k)
                if col >= len(row_vals):
                    # 足りない分は空文字で拡張
                    row_vals += [""] * (col - len(row_vals) + 1)
                row_vals[col] = v
        # レンジ文字列（A1:Z1 的な）を生成
        end_col_letter = gspread.utils.rowcol_to_a1(1, len(row_vals)).rstrip("1")
        rng = f"A{sheet_row}:{end_col_letter}{sheet_row}"
        requests.append({"range": rng, "values": [row_vals]})

    # batch_update
    ws.batch_update(requests, value_input_option="RAW")


# =========================
# 便利関数（ローカル計算）
# =========================
def ensure_columns(df: pd.DataFrame, cols: list[str]):
    """df に指定列が無ければ追加（空）。保存前にヘッダ欠損で落ちないようにする保険。"""
    for c in cols:
        if c not in df.columns:
            df[c] = ""

def atbat_subset(df: pd.DataFrame, inning: int, tb: str, order: int) -> pd.DataFrame:
    cond = (
        (df["inning"].astype(str) == str(inning)) &
        (df["top_bottom"] == tb) &
        (df["order"].astype(str) == str(order))
    )
    return df[cond].copy()

def compute_counts_for_pitch(results_before: list[str]) -> tuple[int, int]:
    """その球の直前までの pitch_result 配列から (strike_count<=2, ball_count<=3) を返す。"""
    s = 0
    b = 0
    for r in results_before:
        if r.startswith("ストライク") or r.startswith("ファウル"):
            if s < 2:
                s += 1
        elif r.startswith("ボール"):
            if b < 3:
                b += 1
        # 牽制/その他はカウント変化なし
    return s, b

def next_atbat_pointer(df: pd.DataFrame, inning: int, tb: str, order: int) -> tuple[int|None, str|None, int|None]:
    """
    “次の打席”のポインタを返す。
    優先順:
      1) 同イニング・同表裏で order+1（9→1） が存在
      2) 表→裏、もしくは 裏→次イニング表 の 1番が存在
      見つからなければ (None, None, None)
    """
    # orderカラムを一度すべて整数化（float, str 混在対応）
    df = df.copy()
    df["order_int"] = pd.to_numeric(df["order"], errors="coerce").fillna(0).astype(int)
    inning = int(inning)
    order = int(order)

    next_order = 1 if order == 9 else order + 1

    # 1) 同じイニング・同じ表裏で次打者を探す
    same_tb = df[(df["inning"].astype(int) == inning) &
                 (df["top_bottom"] == tb) &
                 (df["order_int"] == next_order)]
    if not same_tb.empty:
        return (inning, tb, next_order)

    # 2) 表裏を進める
    if tb == "表":
        ntb, ninn = "裏", inning
    else:
        ntb, ninn = "表", inning + 1

    next_tb_first = df[(df["inning"].astype(int) == ninn) &
                       (df["top_bottom"] == ntb) &
                       (df["order_int"] == 1)]
    if not next_tb_first.empty:
        return (ninn, ntb, 1)

    # 3) どちらも存在しない場合は試合終了
    return (None, None, None)


# =========================
# セッション初期化
# =========================
st.set_page_config(page_title="補足入力（打席単位・一括保存・軽量）", layout="wide")
st.title("📘 補足入力（打席単位 / 一括保存）")

if "sheet_name" not in st.session_state:
    st.session_state.sheet_name = None

# 現在の打席ポインタ（固定表示・rerunなし遷移）
defaults = {"inning": 1, "top_bottom": "表", "order": 1}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# 打席内の“何球目”選択
st.session_state.setdefault("pitch_idx", 0)  # 0-based

# 打席情報のメモ（打者/投手/走者/アウト）を“打順に紐付けて再利用”
# batter_memory[(sheet, tb, order)] = {"batter":..., "batter_side":...}
st.session_state.setdefault("batter_memory", {})
# pitcher_memory[(sheet, tb)] = {"pitcher":..., "pitcher_side":...}
st.session_state.setdefault("pitcher_memory", {})

# 現打席の入力バッファ（保存までローカル保持）
st.session_state.setdefault("atbat_buffer", {})  # 打席情報
st.session_state.setdefault("pitch_edits", {})   # {df_idx: {"pitch_result":..., "atbat_result":..., ...}}


# =========================
# 1) 試合シートの選択
# =========================
st.header("1. 試合シートを選択")
try:
    sheets = list_game_sheets()
except Exception as e:
    st.error(f"シート一覧の取得に失敗：{e}")
    st.stop()

if not sheets:
    st.warning("`YYYY-MM-DD_` 形式のシートが見つかりません。")
    st.stop()

sheet_name = st.selectbox("試合シート", sheets, index=(sheets.index(st.session_state.sheet_name) if st.session_state.sheet_name in sheets else 0))
if sheet_name != st.session_state.sheet_name:
    st.session_state.sheet_name = sheet_name
    # シート変更時はローカル状態を初期化
    st.session_state.pitch_idx = 0
    st.session_state.atbat_buffer = {}
    st.session_state.pitch_edits = {}

df = load_sheet(st.session_state.sheet_name)
if df.empty:
    st.warning("この試合シートはまだ空です。")
    st.stop()

# 必要カラムの確保（不足していても落ちないように）
ensure_columns(df, [
    "batter","batter_side","pitcher","pitcher_side",
    "runner_1b","runner_2b","runner_3b","outs",
    "pitch_result","atbat_result","batted_type","batted_position","batted_outcome",
    "strike_count","ball_count"
])

st.caption(f"総行数: {len(df)}")

# =========================
# 2) 対象“打席”を指定
# =========================
st.header("2. 対象打席を指定（イニング / 表裏 / 打順）")
c1, c2, c3 = st.columns([1,1,1])
with c1:
    inning = st.number_input("イニング", min_value=1, step=1, value=int(st.session_state.inning), key="inning_input")
with c2:
    top_bottom = st.radio("表裏", ["表","裏"], horizontal=True, index=(0 if st.session_state.top_bottom=="表" else 1), key="tb_input")
with c3:
    order = st.number_input("打順", min_value=1, max_value=9, step=1, value=int(st.session_state.order), key="order_input")

# セッションに確定
st.session_state.inning = int(st.session_state.inning_input)
st.session_state.top_bottom = st.session_state.tb_input
st.session_state.order = int(st.session_state.order_input)

# 打席の行群
subset = atbat_subset(df, st.session_state.inning, st.session_state.top_bottom, st.session_state.order)
subset = subset.reset_index()  # 元の df 行番号を保持（col 'index'）
if subset.empty:
    st.warning("この打席（イニング/表裏/打順）の行が見つかりません。")
    st.stop()

# pitch_idx の上限を保険
if st.session_state.pitch_idx >= len(subset):
    st.session_state.pitch_idx = max(0, len(subset)-1)

# =========================
# 3) 打席情報（入力＆保持）
# =========================
st.header("3. 打席情報 / 走者・アウト（打席では原則固定）")

# 既知の打者・投手をメモから自動補完
bm_key = (st.session_state.sheet_name, st.session_state.top_bottom, st.session_state.order)
pm_key = (st.session_state.sheet_name, st.session_state.top_bottom)

# 初回だけ atbat_buffer に初期値反映（メモ or DF の最初行）
if not st.session_state.atbat_buffer:
    first = subset.iloc[0]  # その打席の最初の行
    # batter/pitcher はメモを優先。無ければ DF 値、さらに無ければ空
    batter = st.session_state.batter_memory.get(bm_key, {}).get("batter", first.get("batter",""))
    batter_side = st.session_state.batter_memory.get(bm_key, {}).get("batter_side", first.get("batter_side","右") or "右")

    pitcher = st.session_state.pitcher_memory.get(pm_key, {}).get("pitcher", first.get("pitcher",""))
    pitcher_side = st.session_state.pitcher_memory.get(pm_key, {}).get("pitcher_side", first.get("pitcher_side","右") or "右")

    # 走者・アウトは DF を初期値に（なければ False/0）
    r1 = bool(first.get("runner_1b")) if str(first.get("runner_1b")).lower() not in ("", "0", "false", "none") else False
    r2 = bool(first.get("runner_2b")) if str(first.get("runner_2b")).lower() not in ("", "0", "false", "none") else False
    r3 = bool(first.get("runner_3b")) if str(first.get("runner_3b")).lower() not in ("", "0", "false", "none") else False
    outs = int(first.get("outs") or 0)

    st.session_state.atbat_buffer = {
        "batter": batter,
        "batter_side": batter_side,
        "pitcher": pitcher,
        "pitcher_side": pitcher_side,
        "runner_1b": r1,
        "runner_2b": r2,
        "runner_3b": r3,
        "outs": outs
    }

# ---- 入力UI（横並び） ----
b1, b2, p1, p2 = st.columns(4)
with b1:
    st.session_state.atbat_buffer["batter"] = st.text_input("打者名", value=st.session_state.atbat_buffer["batter"])
with b2:
    st.session_state.atbat_buffer["batter_side"] = st.selectbox("打者の利き腕", ["右","左","両"],
                                                                index=["右","左","両"].index(st.session_state.atbat_buffer["batter_side"]) if st.session_state.atbat_buffer["batter_side"] in ["右","左","両"] else 0)
with p1:
    st.session_state.atbat_buffer["pitcher"] = st.text_input("投手名", value=st.session_state.atbat_buffer["pitcher"])
with p2:
    st.session_state.atbat_buffer["pitcher_side"] = st.selectbox("投手の利き腕", ["右","左"],
                                                                 index=["右","左"].index(st.session_state.atbat_buffer["pitcher_side"]) if st.session_state.atbat_buffer["pitcher_side"] in ["右","左"] else 0)

r1c, r2c, r3c, oc = st.columns([1,1,1,1])
with r1c:
    st.session_state.atbat_buffer["runner_1b"] = st.checkbox("一塁走者あり", value=st.session_state.atbat_buffer["runner_1b"])
with r2c:
    st.session_state.atbat_buffer["runner_2b"] = st.checkbox("二塁走者あり", value=st.session_state.atbat_buffer["runner_2b"])
with r3c:
    st.session_state.atbat_buffer["runner_3b"] = st.checkbox("三塁走者あり", value=st.session_state.atbat_buffer["runner_3b"])
with oc:
    st.session_state.atbat_buffer["outs"] = st.number_input("アウトカウント", min_value=0, max_value=2, step=1, value=int(st.session_state.atbat_buffer["outs"]))

st.caption("※ この打席が続く限りこの値が初期値として使われます。次打席へ進むと更新できます。")

# =========================
# 4) 何球目を編集するか
# =========================
st.header("4. 何球目を編集 → 投球情報を入力")
info_cols = st.columns([3, 2, 2])
with info_cols[0]:
    st.write(f"この打席の球数: **{len(subset)}**")
with info_cols[1]:
    if st.button("◀ 前の球", use_container_width=True, disabled=(st.session_state.pitch_idx == 0)):
        st.session_state.pitch_idx -= 1
with info_cols[2]:
    if st.button("次の球 ▶", use_container_width=True, disabled=(st.session_state.pitch_idx >= len(subset)-1)):
        st.session_state.pitch_idx += 1

cur = subset.iloc[st.session_state.pitch_idx]
df_row = int(cur["index"])  # 元DFの行番号

# 直前までの pitch_result（ローカル編集を優先）
results_before = []
for i in range(st.session_state.pitch_idx):
    prev_row = subset.iloc[i]
    prev_df_idx = int(prev_row["index"])
    pr = st.session_state.pitch_edits.get(prev_df_idx, {}).get("pitch_result")
    if pr is None or pr == "":
        pr = str(df.loc[prev_df_idx].get("pitch_result") or "")
    results_before.append(pr)

strike_count, ball_count = compute_counts_for_pitch(results_before)

st.info(f"{st.session_state.inning}回{st.session_state.top_bottom} {st.session_state.order}番｜{st.session_state.pitch_idx+1}球目を編集中（直前のカウント: S{strike_count} B{ball_count}）")

# 投球情報UI（この球）
colL, colR = st.columns([2, 2])
with colL:
    pitch_result = st.selectbox("球の結果",
                                ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "牽制", "打席終了"],
                                index=0,
                                key=f"pr_{df_row}")
with colR:
    if pitch_result == "打席終了":
        atbat_result = st.selectbox("打席結果",
                                    ["", "三振(見)", "三振(空)","四球","死球","インプレー","その他"],
                                    index=0,
                                    key=f"ar_{df_row}")
    else:
        atbat_result = ""

if atbat_result == "インプレー":
    cA, cB, cC = st.columns(3)
    with cA:
        batted_type = st.selectbox("打球の種類", ["フライ","ゴロ","ライナー"], index=0, key=f"bt_{df_row}")
    with cB:
        batted_position = st.selectbox("打球方向", ["投手","一塁","二塁","三塁","遊撃","左翼","中堅","右翼","左中","右中"], index=0, key=f"bp_{df_row}")
    with cC:
        batted_outcome = st.selectbox("打球結果", ["ヒット","2塁打","3塁打","ホームラン","アウト","エラー","併殺","犠打","犠飛"], index=0, key=f"bo_{df_row}")
else:
    batted_type = ""
    batted_position = ""
    batted_outcome = ""

# ローカル編集に反映（保存ボタンを押すまでは Sheets 書き込みしない）
local_update = {
    "pitch_result": pitch_result,
    "atbat_result": atbat_result,
    "batted_type": batted_type,
    "batted_position": batted_position,
    "batted_outcome": batted_outcome,
    "strike_count": strike_count,
    "ball_count": ball_count,
    # 打席情報も各球に反映（保存時にまとめて書き込む）
    "batter": st.session_state.atbat_buffer["batter"],
    "batter_side": st.session_state.atbat_buffer["batter_side"],
    "pitcher": st.session_state.atbat_buffer["pitcher"],
    "pitcher_side": st.session_state.atbat_buffer["pitcher_side"],
    "runner_1b": st.session_state.atbat_buffer["runner_1b"],
    "runner_2b": st.session_state.atbat_buffer["runner_2b"],
    "runner_3b": st.session_state.atbat_buffer["runner_3b"],
    "outs": st.session_state.atbat_buffer["outs"],
}
st.session_state.pitch_edits[df_row] = local_update  # 逐次上書き（軽量）

st.caption("※ ここまでの編集はローカルに保持。下の “この打席を保存” ボタンで一括保存します。")

# =========================
# 5) この打席を一括保存
# =========================
st.header("5. この打席をスプレッドシートに保存")

col_save, col_next = st.columns([2, 2])

with col_save:
    if st.button("💾 この打席を保存（一括）", type="primary", use_container_width=True):
        # この打席に属する行だけ取り出して保存
        target_df_idxs = [int(r["index"]) for _, r in subset.iterrows()]
        updates_list = [st.session_state.pitch_edits.get(idx, {}) for idx in target_df_idxs]

        # 空 dict は現状の DF から “最低限の列” を拾って補う（未編集でも strike/ball などは保存）
        minimal_cols = ["pitch_result","atbat_result","batted_type","batted_position","batted_outcome",
                        "strike_count","ball_count",
                        "batter","batter_side","pitcher","pitcher_side","runner_1b","runner_2b","runner_3b","outs"]
        for i, up in enumerate(updates_list):
            if not up:
                base_row = df.loc[target_df_idxs[i]]
                up2 = {c: base_row.get(c, "") for c in minimal_cols}
                updates_list[i] = up2
            else:
                # 最低限のキーが無ければ埋める
                for c in minimal_cols:
                    updates_list[i].setdefault(c, df.loc[target_df_idxs[i]].get(c, ""))

        try:
            batch_update_rows(st.session_state.sheet_name, target_df_idxs, updates_list)
            st.success("この打席の内容を保存しました。")

            # バッター情報を “打順に紐付け” で記憶（次回同打順の初期値に）
            st.session_state.batter_memory[bm_key] = {
                "batter": st.session_state.atbat_buffer["batter"],
                "batter_side": st.session_state.atbat_buffer["batter_side"],
            }
            # ピッチャーは“表裏に紐付け”で記憶（同じ半イニングなら継続）
            st.session_state.pitcher_memory[pm_key] = {
                "pitcher": st.session_state.atbat_buffer["pitcher"],
                "pitcher_side": st.session_state.atbat_buffer["pitcher_side"],
            }

        except Exception as e:
            st.error(f"保存に失敗しました：{e}")

with col_next:
    last_df_idx = int(subset.iloc[-1]["index"])
    last_result = st.session_state.pitch_edits.get(last_df_idx, {}).get("pitch_result")
    if not last_result:
        last_result = str(df.loc[last_df_idx].get("pitch_result") or "")

    can_go_next = last_result == "打席終了"

    if st.button(f"➡ 次の打席へ進む{'（打席終了の球が必要）' if not can_go_next else ''}",
                 use_container_width=True, disabled=not can_go_next):
        ninn, ntb, nord = next_atbat_pointer(df, st.session_state.inning,
                                             st.session_state.top_bottom, st.session_state.order)
        if ninn is None:
            st.info("試合終了です 🏁")
        else:
            carry_pitcher = (ntb == st.session_state.top_bottom)

            st.session_state.inning = ninn
            st.session_state.top_bottom = ntb
            st.session_state.order = nord
            st.session_state.pitch_idx = 0

            # 新しい打席を特定
            next_subset = atbat_subset(df, ninn, ntb, nord)
            if next_subset.empty:
                st.info("次の打席データがスプレッドシートに存在しません。")
            else:
                next_first = next_subset.reset_index().iloc[0]
                new_batter = st.session_state.batter_memory.get(
                    (st.session_state.sheet_name, ntb, nord), {}
                ).get("batter", next_first.get("batter", ""))
                new_batter_side = st.session_state.batter_memory.get(
                    (st.session_state.sheet_name, ntb, nord), {}
                ).get("batter_side", next_first.get("batter_side", "右") or "右")

                if carry_pitcher:
                    new_pitcher = st.session_state.atbat_buffer["pitcher"]
                    new_pitcher_side = st.session_state.atbat_buffer["pitcher_side"]
                else:
                    new_pitcher = st.session_state.pitcher_memory.get(
                        (st.session_state.sheet_name, ntb), {}
                    ).get("pitcher", next_first.get("pitcher", ""))
                    new_pitcher_side = st.session_state.pitcher_memory.get(
                        (st.session_state.sheet_name, ntb), {}
                    ).get("pitcher_side", next_first.get("pitcher_side", "右") or "右")

                st.session_state.atbat_buffer = {
                    "batter": new_batter,
                    "batter_side": new_batter_side,
                    "pitcher": new_pitcher,
                    "pitcher_side": new_pitcher_side,
                    "runner_1b": bool(next_first.get("runner_1b")),
                    "runner_2b": bool(next_first.get("runner_2b")),
                    "runner_3b": bool(next_first.get("runner_3b")),
                    "outs": int(next_first.get("outs") or 0)
                }

                st.session_state.pitch_edits = {}
                st.success(f"{ninn}回{ntb} {nord}番打者へ移動しました。")

# =========================
# 6) 参考：この打席の全球（プレビュー）
# =========================
st.header("6. この打席の全球（プレビュー）")
preview_cols = ["inning","top_bottom","order","zone","pitch_type","pitch_result","strike_count","ball_count","atbat_result"]
for c in preview_cols:
    if c not in subset.columns:
        subset[c] = ""
# ローカル編集を上書きしてプレビュー
subset2 = subset.copy()
for i, r in subset2.iterrows():
    dfi = int(r["index"])
    if dfi in st.session_state.pitch_edits:
        for k, v in st.session_state.pitch_edits[dfi].items():
            subset2.at[i, k] = v
st.dataframe(subset2[preview_cols], use_container_width=True)