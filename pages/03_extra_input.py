# pages/04_batch_counts.py
import streamlit as st
import pandas as pd
import gspread
import re
import time
from google.oauth2.service_account import Credentials

# ==============================
# Google Sheets 接続・共通関数
# ==============================
SPREADSHEET_NAME = "Pitch_Data_2025"

@st.cache_resource(show_spinner=False)
def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    return gspread.authorize(creds)

def _open_ss():
    return _gs_client().open(SPREADSHEET_NAME)

@st.cache_data(show_spinner=False, ttl=60)
def list_game_sheets():
    """YYYY-MM-DD_ で始まるシートのみ。読み込みはキャッシュ（60秒）。"""
    ss = _open_ss()
    titles = [ws.title for ws in ss.worksheets()]
    return sorted([t for t in titles if re.match(r"^\d{4}-\d{2}-\d{2}_", t)])

@st.cache_data(show_spinner=False, ttl=60)
def load_game_sheet(sheet_name: str):
    ws = _open_ss().worksheet(sheet_name)
    values = ws.get_all_values()  # 1回で全取得（回数を抑える）
    if not values:
        return pd.DataFrame(), []
    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)
    return df, header

def ensure_columns(ws, header: list, need_cols: list) -> list:
    """必要列が無ければヘッダーに追加して1行目を更新。戻り値は更新後ヘッダー。"""
    missing = [c for c in need_cols if c not in header]
    if not missing:
        return header
    new_header = header + missing
    ws.update('1:1', [new_header])   # 1行目を置換
    return new_header

def col_letter(idx_1based: int) -> str:
    """1→A, 2→B ..."""
    s = ""
    n = idx_1based
    while n:
        n, r = divmod(n-1, 26)
        s = chr(65+r) + s
    return s

def batch_update_rows(ws, header: list, row_updates: list[dict]):
    """
    row_updates = [
      {"row": 12, "values": {"strike_count": 1, "ball_count": 2, "pitch_in_atbat": 3}},
      ...
    ]
    を、batch_updateでまとめて反映。
    """
    # まとめてレンジを作る
    requests = []
    for item in row_updates:
        row_no = int(item["row"])          # 1-based（ヘッダー行含む）
        vals  = item["values"]             # dict
        # 対象キーだけ抜き出し（列順不問）
        keys = list(vals.keys())
        # 左端・右端の列番号を求める（離散更新を避けるため横並びの最小〜最大にまとめる）
        col_indices = [header.index(k)+1 for k in keys if k in header]
        if not col_indices:
            continue
        left = min(col_indices)
        right = max(col_indices)
        # その範囲分の配列を組む（欠け列には既存値を触らずに置換しないよう、本当は列ごと指定が望ましい）
        # ここでは「キーがある列にだけ値を入れ、その他はセルをそのまま」にしたいので、
        # 列ごとの個別レンジ更新に切り替える（安全優先）
        for k in keys:
            c = header.index(k) + 1
            rng = f"{col_letter(c)}{row_no}:{col_letter(c)}{row_no}"
            requests.append({
                "range": rng,
                "values": [[str(vals[k])]],
            })

    # 100 件ごとに分割して送信（429対策）
    for i in range(0, len(requests), 100):
        chunk = requests[i:i+100]
        ws.batch_update(chunk)
        # 速連打を少し緩和
        time.sleep(0.2)

# ==============================
# カウント計算（打席単位）
# ==============================
def count_before_pitch(pitch_result: str, strikes: int, balls: int):
    """
    “その球以前”のカウント値として (strikes, balls) を返す。
    戻り値は現在の値（記録用）。その後で内部で次の値に進める。
    """
    return strikes, balls

def advance_count_after_pitch(pitch_result: str, strikes: int, balls: int):
    """この球の結果を反映してカウントを進める（上限 S:2, B:3 ルール込み）"""
    pr = (pitch_result or "").strip()
    if pr in ("ストライク（見逃し）", "ストライク（空振り）"):
        strikes = min(2, strikes + 1)
    elif pr == "ファウル":
        if strikes < 2:
            strikes += 1
    elif pr == "ボール":
        balls = min(3, balls + 1)
    # 牽制・その他はカウント変化なし
    return strikes, balls

def compute_counts_for_inning(df: pd.DataFrame, inning: int, top_bottom: str):
    """
    指定イニングの全打席（orderごと）について、
    「その球以前」の strike_count / ball_count と 何球目 pitch_in_atbat を計算して
    {row_index_in_df: {col: val, ...}} を返す。
    """
    # 前処理：数値列を安全にキャスト
    dff = df.copy()
    # 入力のバラツキに備えて文字列→数値を吸収
    for col in ("inning", "order"):
        if col in dff.columns:
            dff[col] = pd.to_numeric(dff[col], errors="coerce")

    # このイニングの該当データ
    cond = (
        (dff.get("inning").astype("Int64") == int(inning)) &
        (dff.get("top_bottom") == top_bottom)
    )
    sub = dff[cond].copy()
    # 同打席内の並び順は“シート上の登場順”＝元の行順のまま
    sub = sub.reset_index()   # index列に “元のdf行番号” が入る

    result_map = {}  # df_row -> {col: val}

    # 打順ごと（=打席ごと）にグループ化
    if "order" not in sub.columns:
        return result_map  # 欠損時は何もしない

    for order_val, g in sub.groupby("order", dropna=True):
        # カウント初期化
        s_count, b_count = 0, 0
        # 打席内で上から順に（=古い順）
        for i, row in g.iterrows():
            df_row = int(row["index"])  # 元dfの行番号
            pr = row.get("pitch_result", "")

            # 記録するのは “この球の直前” のカウント
            rec_s, rec_b = count_before_pitch(pr, s_count, b_count)

            # 何球目（1始まり）
            pitch_idx = i - g.index.min() + 1  # i は sub.reset_index()後の連番なので基準をグループ先頭に

            result_map[df_row] = {
                "strike_count": str(rec_s),
                "ball_count": str(rec_b),
                "pitch_in_atbat": str(pitch_idx),
            }

            # 次の球に向けてカウントを進める
            s_count, b_count = advance_count_after_pitch(pr, s_count, b_count)

    return result_map

# ==============================
# Streamlit UI
# ==============================
st.set_page_config(page_title="補足入力：自動カウント付与（イニング保存）", layout="wide")
st.title("📘 補足入力：自動カウント付与（イニング単位で一括保存）")

# 1) 試合シート選択
with st.container():
    st.subheader("1. 試合シートを選択")
    try:
        sheets = list_game_sheets()
    except Exception as e:
        st.error(f"シート一覧の取得に失敗しました: {e}")
        st.stop()

    if not sheets:
        st.warning("YYYY-MM-DD_ で始まるシートが見つかりません。")
        st.stop()

    sheet_name = st.selectbox("試合シート", sheets)

# 2) イニング指定 & データ読込（キャッシュ）
with st.container():
    st.subheader("2. 対象イニングを指定")
    col1, col2 = st.columns(2)
    with col1:
        inning = st.number_input("イニング", min_value=1, step=1, value=1)
    with col2:
        top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True, index=0)

    # 読み込み
    try:
        df, header = load_game_sheet(sheet_name)
    except Exception as e:
        st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
        st.stop()

    if df.empty:
        st.warning("この試合シートにはまだデータがありません。")
        st.stop()

    st.caption(f"読み込んだ行数: {len(df)}")

# 3) 計算プレビュー & 保存
with st.container():
    st.subheader("3. 自動カウントを計算 → 保存")
    st.markdown("- Strike/ball は **その球の直前** のカウントを記録します。")
    st.markdown("- 保存は **このイニング（指定の表/裏）に属する全打席** に対して行われます。")

    # 計算
    result_map = compute_counts_for_inning(df, inning=inning, top_bottom=top_bottom)

    # プレビュー（先頭10行だけ）
    if result_map:
        preview_rows = []
        for df_row, vals in result_map.items():
            r = df.loc[df_row]
            preview_rows.append({
                "df_row": df_row+2,  # 表示用（シート上の行番号を意識）
                "inning": r.get("inning", ""),
                "top_bottom": r.get("top_bottom", ""),
                "order": r.get("order", ""),
                "pitch_result": r.get("pitch_result", ""),
                **vals
            })
        st.dataframe(pd.DataFrame(preview_rows).head(10), use_container_width=True)
    else:
        st.info("このイニング・表裏に該当するデータが見つかりませんでした。")

    # 保存ボタン（rerunに頼らず、その場で一括保存）
    if st.button("💾 このイニングのカウントを一括保存", use_container_width=True, type="primary", help="429対策としてバッチ更新で書き込みます"):
        try:
            ss = _open_ss()
            ws = ss.worksheet(sheet_name)

            # 必要列を確保（無ければヘッダーに追加）
            header = ensure_columns(ws, header, ["strike_count", "ball_count", "pitch_in_atbat"])

            # 反映対象を組み立て
            updates = []
            for df_row, vals in result_map.items():
                # df_row は 0-based。シートはヘッダ行があるので +2
                row_no = int(df_row) + 2
                updates.append({"row": row_no, "values": vals})

            if not updates:
                st.info("更新対象がありませんでした。")
            else:
                batch_update_rows(ws, header, updates)
                st.success(f"{inning}回{top_bottom} の {len(updates)} 行にカウントを保存しました ✅")

                # 読み込みキャッシュを明示的にクリア（画面はrerunしない）
                load_game_sheet.clear()   # type: ignore[attr-defined]
        except gspread.exceptions.APIError as e:
            st.error(f"APIError: {e}")
        except Exception as e:
            st.error(f"保存時にエラーが発生しました: {e}")

# 4) 任意：対象イニングの抽出を表示（確認用・負荷軽め）
with st.expander("対象イニングの生データ（確認用）", expanded=False):
    try:
        dff = df.copy()
        dff["inning"] = pd.to_numeric(dff.get("inning"), errors="coerce")
        show = dff[(dff["inning"] == int(inning)) & (dff["top_bottom"] == top_bottom)]
        st.dataframe(show, use_container_width=True, height=300)
    except Exception:
        st.dataframe(df.head(50), use_container_width=True, height=300)