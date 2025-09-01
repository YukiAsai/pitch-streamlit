import streamlit as st
from datetime import datetime
import uuid
import gspread
from google.oauth2.service_account import Credentials

# 追加で必要
from PIL import Image, ImageDraw

# 線画の見た目設定
TARGET_WIDTH = 300      # 表示幅
GRID_TOTAL = 5          # 5x5（外周がボール、内側3x3がストライク）
PAD_RATIO = 0.1         # 画像の外余白

def make_strike_zone_base() -> tuple[Image.Image, dict]:
    """
    5x5 グリッド＋内側3x3を薄く塗った線画を生成して、(画像, 境界情報) を返す
    """
    W = TARGET_WIDTH
    H = int(W * 1.1)
    PAD = int(W * PAD_RATIO)
    STROKE_OUT = 2
    STROKE_GRID = 1

    BG = (255, 255, 255)
    LINE = (0, 0, 0)
    CORE_FILL = (235, 245, 255)   # 内側3x3の淡い塗り

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # 全体5x5の枠
    x_left  = PAD
    x_right = W - PAD
    y_top   = PAD
    y_bot   = H - PAD

    # セルサイズ
    cell_w = (x_right - x_left) / GRID_TOTAL
    cell_h = (y_bot - y_top) / GRID_TOTAL

    # 内側3x3の枠（外周1マスぶん内側）
    core_left   = x_left  + cell_w
    core_right  = x_right - cell_w
    core_top    = y_top   + cell_h
    core_bottom = y_bot   - cell_h

    # 内側3x3を淡色で塗る
    draw.rectangle([core_left, core_top, core_right, core_bottom], fill=CORE_FILL)

    # 外枠
    draw.rectangle([x_left, y_top, x_right, y_bot], outline=LINE, width=STROKE_OUT)

    # グリッド線
    for i in range(1, GRID_TOTAL):
        x = x_left + cell_w * i
        draw.line([(x, y_top), (x, y_bot)], fill=LINE, width=STROKE_GRID)
    for j in range(1, GRID_TOTAL):
        y = y_top + cell_h * j
        draw.line([(x_left, y), (x_right, y)], fill=LINE, width=STROKE_GRID)

    bounds = dict(
        x_left=x_left, x_right=x_right,
        y_top=y_top, y_bottom=y_bot,
        cell_w=cell_w, cell_h=cell_h,
        W=W, H=H
    )
    return img, bounds

def center_of_cell(col: int, row: int, bounds: dict) -> dict:
    """5x5 のセル(1..5,1..5)の中心ピクセル座標を返す"""
    cx = int(round(bounds["x_left"] + (col - 0.5) * bounds["cell_w"]))
    cy = int(round(bounds["y_top"]  + (row - 0.5) * bounds["cell_h"]))
    return {"x": cx, "y": cy}

# ========= 既定値 =========
TARGET_WIDTH = 300  # 画像は使わず、グリッドでコースを記録
GRID_TOTAL = 5      # 5x5（外周=ボール、内側3x3=ストライク）
SPREADSHEET_NAME = "Pitch_Data_2025"

# ========= GSpread helpers =========
def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

def _open_spreadsheet():
    return _gs_client().open(SPREADSHEET_NAME)

def _get_worksheet(spreadsheet, sheet_name: str):
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=30)
        # 最初の保存時にヘッダーを自動生成するため、ここでは列は作らない
        return ws

def _safe_sheet_name(raw: str) -> str:
    # 禁止文字除去 + 長さ制限
    import re
    name = re.sub(r'[:/\\\?\*\[\]\r\n]', ' ', raw).strip()
    return name[:100] if len(name) > 100 else name

def save_minimal_record(latest: dict) -> str:
    """
    同じ試合（date + top_team + bottom_team）で同一シートに追記。
    ヘッダーは latest.keys() を基準に自動拡張。
    """
    ss = _open_spreadsheet()

    date = latest.get("date", "unknown")
    top_team = (latest.get("top_team") or "TopTeam").strip()
    bottom_team = (latest.get("bottom_team") or "BottomTeam").strip()

    raw_sheet_name = f"{date}_{top_team}_vs_{bottom_team}"
    sheet_name = _safe_sheet_name(raw_sheet_name)

    ws = _get_worksheet(ss, sheet_name)
    values = ws.get_all_values()
    has_header = len(values) > 0
    header = values[0] if has_header else []

    desired_header = list(latest.keys())
    # 列不足なら右に拡張
    if not has_header:
        ws.append_row(desired_header, value_input_option="RAW")
        header = desired_header
    else:
        missing = [c for c in desired_header if c not in header]
        if missing:
            header = header + missing
            ws.update('1:1', [header])

    row = [latest.get(c, "") for c in header]
    ws.append_row(row, value_input_option="RAW")
    return sheet_name

def delete_row_by_id(sheet_name: str, row_id: str) -> bool:
    ss = _open_spreadsheet()
    try:
        ws = ss.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return False
    vals = ws.get_all_values()
    if not vals:
        return False
    header = vals[0]
    try:
        idx0 = header.index("row_id")
    except ValueError:
        return False
    for i in range(1, len(vals)):
        if len(vals[i]) > idx0 and vals[i][idx0] == row_id:
            ws.delete_rows(i + 1)  # 1-based
            return True
    return False

# ========= ページ設定 =========
st.set_page_config(page_title="簡易入力（試合/イニング/打順/コース/球種）", layout="wide")
st.title("⚾ 簡易入力モード")

# ========= セッション =========
if "game_info" not in st.session_state:
    st.session_state.game_info = {}
if "inning_info" not in st.session_state:
    st.session_state.inning_info = {}
if "pitches" not in st.session_state:
    st.session_state.pitches = []
if "save_log" not in st.session_state:
    st.session_state.save_log = []

# ========= サイドバー：全消去 & 取り消し =========
st.sidebar.header("操作")
if st.sidebar.button("🔄 入力をリセット"):
    st.session_state.clear()
    st.rerun()

# ========= 1. 試合情報 =========
st.header("1. 試合情報")
with st.form("game_form"):
    game_date = st.date_input("試合日", value=datetime.today())
    colA, colB = st.columns(2)
    with colA:
        top_team = st.text_input("先攻チーム名")
    with colB:
        bottom_team = st.text_input("後攻チーム名")
    if st.form_submit_button("試合情報を保存"):
        st.session_state.game_info = {
            "date": game_date.strftime("%Y-%m-%d"),
            "top_team": top_team.strip(),
            "bottom_team": bottom_team.strip(),
        }
        st.success("試合情報を保存しました。")

if st.session_state.game_info:
    gi = st.session_state.game_info
    st.info(f"試合日: {gi.get('date','')}｜先攻: {gi.get('top_team','')}｜後攻: {gi.get('bottom_team','')}")

# ========= 2. イニング・打順 =========
st.header("2. イニング・打順")
with st.form("inning_form"):
    col1, col2, col3, col4 = st.columns([1, 1, 1, 1])  # ★ 4分割に変更
    with col1:
        inning = st.number_input("イニング", min_value=1, step=1, value=1)
    with col2:
        top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
    with col3:
        order_num = st.number_input("打順（1〜9）", min_value=1, max_value=9, step=1, value=1)
    with col4:
        batter_cycle = st.checkbox("打者一巡", value=False)  # ★ チェックボックス追加
        st.caption("※ 同じ打順にその回２度目の打席が回ったらチェックしてください")

    if st.form_submit_button("イニング・打順を保存"):
        st.session_state.inning_info = {
            "inning": int(inning),
            "top_bottom": top_bottom,
            "order": int(order_num),
            "batter_cycle": batter_cycle,   # ★ 保存する
        }
        st.success("イニング・打順を保存しました。")

if st.session_state.inning_info:
    ii = st.session_state.inning_info
    st.info(f"{ii.get('inning','?')}回{ii.get('top_bottom','?')}｜打順 {ii.get('order','?')}")

# ========= 3. コース（5×5） =========
st.header("3. コース（5×5）")

# まず線画を作る
base_img, zone_bounds = make_strike_zone_base()

# スライダーでセルを選択
c1, c2 = st.columns(2)
with c1:
    col = st.select_slider("横(1=内角〜5=外角)(右打者ver、左は逆)", options=[1,2,3,4,5], value=3, key="grid5_col")
with c2:
    row = st.select_slider("縦（1=低め〜5=高め）", options=[1,2,3,4,5], value=3, key="grid5_row")

# セル中心にスナップして赤丸を描画
pt = center_of_cell(col, row, zone_bounds)
canvas = base_img.copy()
draw = ImageDraw.Draw(canvas)
r = 5
draw.ellipse((pt["x"]-r, pt["y"]-r, pt["x"]+r, pt["y"]+r), fill=(255,0,0))

# 画像として表示（超軽量）
st.image(canvas, width=TARGET_WIDTH)

# ストライク/ボールのラベル
in_strike = (2 <= col <= 4) and (2 <= row <= 4)
zone_label = "ストライク" if in_strike else "ボール"
st.caption(f"選択セル: ({col},{row}) → {zone_label}")

# 記録時に使うため（あなたの保存ロジックが 'zone' を使う前提）
# ここで変数 zone_label, col, row が定義されていればOK（保存処理側は変更不要）

# ========= 4. 球種 =========
st.header("4. 球種")
pitch_type = st.selectbox("球種を選択", ["ストレート", "カーブ", "スライダー", "チェンジアップ", "フォーク", "ツーシーム","シュート","シンカー"])

# ========= 5. 球種 =========
st.header("5. 作戦")
strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ","盗塁","バスター"])
# ========= 6. 記録 =========
st.header("6. 記録")
if st.button("この一球を記録"):
    if not st.session_state.get("game_info") or not st.session_state.get("inning_info"):
        st.error("先に試合情報とイニング・打順を保存してください。")
    else:
        gi = st.session_state.game_info
        ii = st.session_state.inning_info
        row_id = str(uuid.uuid4())

        record = {
            "row_id": row_id,
            # 試合情報
            "date": gi.get("date", ""),
            "top_team": gi.get("top_team", ""),
            "bottom_team": gi.get("bottom_team", ""),
            # イニング・打順
            "inning": ii.get("inning", ""),
            "top_bottom": ii.get("top_bottom", ""),
            "order": ii.get("order", ""),
            # コース（5×5）
            "grid_col": col,
            "grid_row": row,
            "zone": zone_label,        # Strike/Ball のラベル
            # 球種
            "pitch_type": pitch_type,
            # 作戦
            "strategy": strategy,
        }

        # ローカル保存
        st.session_state.pitches.append(record)
        # シートへ保存（試合ごとに同じシート）
        sheet_name = save_minimal_record(record)
        # Undo用ログ
        st.session_state.save_log.append({"sheet": sheet_name, "row_id": row_id})
        if len(st.session_state.save_log) > 100:
            st.session_state.save_log = st.session_state.save_log[-100:]

        st.success("保存しました ✅")
# ========= 7. 取消 =========
st.header("7. 取消")
with st.sidebar.expander("⏪ 入力取り消し", expanded=False):
    n_to_undo = st.number_input("取り消す件数", min_value=1, max_value=10, value=1, step=1)
    if st.button("選択件数を取り消す"):
        n = int(min(n_to_undo, len(st.session_state.pitches), len(st.session_state.save_log)))
        if n <= 0:
            st.warning("取り消せる履歴がありません。")
        else:
            ok = 0
            for _ in range(n):
                log = st.session_state.save_log.pop()
                sheet_name = log["sheet"]
                row_id = log["row_id"]

                # シートから該当レコード削除
                if delete_row_by_id(sheet_name, row_id):
                    ok += 1

                # ローカル履歴からも削除
                for j in range(len(st.session_state.pitches) - 1, -1, -1):
                    if st.session_state.pitches[j].get("row_id") == row_id:
                        st.session_state.pitches.pop(j)
                        break
            st.success(f"{n}件取り消しました（シート側 {ok}/{n} 行削除）")
            st.rerun()


# ========= 最近の記録 =========
if st.session_state.pitches:
    st.subheader("📊 最近の記録（直近10件）")
    cols = ["inning","top_bottom","order","grid_col","grid_row","zone","pitch_type","strategy"]
    import pandas as pd
    df = pd.DataFrame(st.session_state.pitches)[cols]
    st.dataframe(df.tail(10), use_container_width=True)