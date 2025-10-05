import streamlit as st
from datetime import datetime
from PIL import Image, ImageDraw, ImageOps, ImageFont
from streamlit_image_coordinates import streamlit_image_coordinates
import os
import uuid

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from io import BytesIO

TARGET_WIDTH = 300  # 表示幅は固定に
GRID_TOTAL = 5          # 5x5：外周1マスがボールゾーン
GRID_CORE = 3           # 内側3x3がストライクゾーン
PAD_RATIO = 0.1         # 画像の余白割合

# 線画ベースを生成＆キャッシュ
@st.cache_resource(show_spinner=False)
def make_strike_zone_base(hand: str = "右", show_labels: bool = True, _ver: int = 1):
    W = TARGET_WIDTH
    H = int(W * 1.1)  # 好みで縦横比
    PAD = int(W * PAD_RATIO)
    STROKE_OUT = 2
    STROKE_GRID = 1

    BG = (255, 255, 255)
    LINE = (0, 0, 0)
    CORE_FILL = (235, 245, 255)   # 内側3x3の淡い塗り（任意）
    CORE_BORDER = (30, 90, 200)   # 内側3x3の枠色（任意）

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # 全体5x5の枠（ゾーン領域）
    x_left  = PAD
    x_right = W - PAD
    y_top   = PAD
    y_bot   = H - PAD

    # 内側3x3の枠を算出（外周1マスぶん内側）
    cell_w = (x_right - x_left) / GRID_TOTAL
    cell_h = (y_bot - y_top) / GRID_TOTAL
    core_left   = x_left + cell_w
    core_right  = x_right - cell_w
    core_top    = y_top + cell_h
    core_bottom = y_bot - cell_h

    # まず内側3x3を淡色で塗る
    draw.rectangle([core_left, core_top, core_right, core_bottom], fill=CORE_FILL, outline=None)

    # 外枠（5x5全体）
    draw.rectangle([x_left, y_top, x_right, y_bot], outline=LINE, width=STROKE_OUT)

    # グリッド線（縦 横）
    for i in range(1, GRID_TOTAL):
        x = x_left + cell_w * i
        draw.line([(x, y_top), (x, y_bot)], fill=LINE, width=STROKE_GRID)
    for j in range(1, GRID_TOTAL):
        y = y_top + cell_h * j
        draw.line([(x_left, y), (x_right, y)], fill=LINE, width=STROKE_GRID)

    # 内側3x3の枠を強調
    draw.rectangle([core_left, core_top, core_right, core_bottom], outline=CORE_BORDER, width=2)

    # ラベル描画（英語固定: IN/OUT）
    if show_labels:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        if hand == "左":
            label_left, label_right = "OUT", "IN"   # 左打者なら左右逆
        else:
            label_left, label_right = "IN", "OUT"   # 右打者なら通常

        margin = int(min(cell_w, cell_h) * 0.15)
        y_text = y_bot - margin - 10  # 下端から少し上に配置

        # 左下
        draw.text((x_left + margin, y_text), label_left, fill=(0, 0, 0), font=font)
        # 右下
        tw = draw.textlength(label_right, font=font) if hasattr(draw, "textlength") else len(label_right) * 6
        draw.text((x_right - margin - tw, y_text), label_right, fill=(0, 0, 0), font=font)


    # 後で座標計算に使う境界を保存
    bounds = dict(
        x_left=x_left, x_right=x_right,
        y_top=y_top, y_bottom=y_bot,
        W=W, H=H,
        cell_w=cell_w, cell_h=cell_h
    )
    return img, bounds

# 赤点を描いてPNGで返す（線画はPNGが軽くて綺麗）
def compose_marked_image_png(base: Image.Image, coords: dict | None) -> bytes:
    canvas = base.copy()
    if coords:
        draw = ImageDraw.Draw(canvas)
        x, y = coords["x"], coords["y"]
        r = 3
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 0, 0))
    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

def point_to_5x5_cell(x: int, y: int, bounds: dict):
    """
    画像ピクセル (x,y) を 5x5 のセル番号 (col,row) に変換（1..5）。
    ついでにストライクゾーン判定（内側3x3なら True）も返す。
    """
    x_left, x_right = bounds["x_left"], bounds["x_right"]
    y_top, y_bottom = bounds["y_top"], bounds["y_bottom"]
    cell_w, cell_h  = bounds["cell_w"], bounds["cell_h"]

    # 範囲外は端に丸める
    if x < x_left:  x = x_left
    if x > x_right: x = x_right
    if y < y_top:   y = y_top
    if y > y_bottom:y = y_bottom

    col = int((x - x_left) // cell_w) + 1  # 1..5
    row = int((y - y_top)  // cell_h) + 1  # 1..5
    col = max(1, min(5, col))
    row = max(1, min(5, row))

    in_strike = (2 <= col <= 4) and (2 <= row <= 4)  # 内側3x3がストライク
    return col, row, in_strike

def center_of_cell(col: int, row: int, bounds: dict):
    """5x5の任意セルの中心ピクセル座標を返す（描画を中心にスナップしたい場合用）"""
    x_left, y_top = bounds["x_left"], bounds["y_top"]
    cell_w, cell_h = bounds["cell_w"], bounds["cell_h"]
    cx = int(round(x_left + (col - 0.5) * cell_w))
    cy = int(round(y_top  + (row - 0.5) * cell_h))
    return {"x": cx, "y": cy}

#スプレッドシートへの保存
def save_to_google_sheets(data):
    import re
    import gspread
    import pandas as pd
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open("Pitch_Data_2025")

    latest = data[-1]
    date = latest.get("date", "unknown")
    top_team = latest.get("top_team", "TopTeam").strip()
    bottom_team = latest.get("bottom_team", "BottomTeam").strip()

    # 同一試合で固定のシート名（表裏・イニングに依存しない）
    raw_sheet_name = f"{date}_{top_team}_vs_{bottom_team}"
    # 禁止文字除去 + 長さ制限
    sheet_name = re.sub(r'[:/\\\?\*\[\]]', ' ', raw_sheet_name).strip()
    sheet_name = sheet_name[:100]

    # シート取得 or 作成
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        existing_data = worksheet.get_all_values()
        has_header = len(existing_data) > 0
        existing_header = existing_data[0] if has_header else []
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=30)
        has_header = False
        existing_header = []

    desired_header = list(latest.keys())

    # 列数が足りなければ増やす（ヘッダー更新や append 前に）
    needed_cols = max(len(desired_header), len(existing_header))
    if worksheet.col_count < needed_cols:
        worksheet.add_cols(needed_cols - worksheet.col_count)

    if not has_header:
        worksheet.append_row(desired_header, value_input_option="RAW")
        existing_header = desired_header
    else:
        missing = [col for col in desired_header if col not in existing_header]
        if missing:
            new_header = existing_header + missing
            # 1行目を新ヘッダーで上書き
            worksheet.update('1:1', [new_header])
            existing_header = new_header

    row_to_append = [latest.get(col, "") for col in existing_header]
    worksheet.append_row(row_to_append, value_input_option="RAW")

    return sheet_name

def delete_row_by_id(sheet_name: str, row_id: str) -> bool:
    """指定シートの 'row_id' 列で一致する行だけを削除。成功なら True。"""
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open("Pitch_Data_2025")

    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return False

    values = ws.get_all_values()
    if not values:
        return False

    header = values[0]
    try:
        col_idx_0 = header.index("row_id")  # 0-based
    except ValueError:
        return False

    # 2行目以降で一致行を探す
    for i in range(1, len(values)):
        if len(values[i]) > col_idx_0 and values[i][col_idx_0] == row_id:
            ws.delete_rows(i + 1)  # gspread は 1-based
            return True
    return False

# ========= Sheets クライアント =========
def _gs_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return gspread.authorize(creds)

def load_game_sheet(sheet_name: str):
    ss = _gs_client().open("Pitch_Data_2025")
    ws = ss.worksheet(sheet_name)
    rows = ws.get_all_records()
    return pd.DataFrame(rows)

def update_row_by_inning(sheet_name: str, inning: int, top_bottom: str, order: int, updates: dict):
    ss = _gs_client().open("Pitch_Data_2025")
    ws = ss.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values:
        return False
    
    header = values[0]
    df = pd.DataFrame(values[1:], columns=header)

    # 条件に一致する行を検索
    cond = (
        (df["inning"].astype(str) == str(inning)) &
        (df["top_bottom"] == top_bottom) &
        (df["order"].astype(str) == str(order))
    )
    match_idx = df[cond].index

    if match_idx.empty:
        return False  # 見つからない
    
    # Google Sheets 上の行番号（ヘッダー行を考慮して +2）
    row_number = match_idx[0] + 2  

    # 更新処理
    for key, val in updates.items():
        if key in header:
            col_idx = header.index(key) + 1
            ws.update_cell(row_number, col_idx, val)

    return True

st.set_page_config(page_title="一球データ入力アプリ", layout="wide")

# ■■ セッション情報初期化 ■■
if "game_info" not in st.session_state:
    st.session_state.game_info = {}
if "inning_info" not in st.session_state:
    st.session_state.inning_info = {}
if "atbat_info" not in st.session_state:
    st.session_state.atbat_info = {}
if "pitches" not in st.session_state:
    st.session_state.pitches = []
if "last_coords" not in st.session_state:
    st.session_state.last_coords = None
if "save_log" not in st.session_state:          
    st.session_state.save_log = []

# □ 試合リセットボタン
st.sidebar.header("試合リセット")
if st.sidebar.button("🔄 試合を変更"):
    st.session_state.clear()
    st.rerun()

# □ 軽量モード
use_light_mode = st.sidebar.toggle("⚡ 軽量モード（画像クリックを使わない）", value=False)


# □ 取り消しUI
with st.sidebar.expander("⏪ 入力取り消し（最大10件）", expanded=False):
    n_to_undo = st.number_input("取り消す件数", min_value=1, max_value=10, value=1, step=1)
    if st.button("選択件数を取り消す"):
        n = int(min(n_to_undo, len(st.session_state.pitches), len(st.session_state.save_log)))
        if n <= 0:
            st.warning("取り消せる履歴がありません。")
        else:
            ok_count = 0
            for _ in range(n):
                log_entry = st.session_state.save_log.pop()
                sheet_name = log_entry["sheet"]
                row_id = log_entry["row_id"]

                # シート側：該当行のみ削除
                deleted = delete_row_by_id(sheet_name, row_id)

                # ローカル履歴側：該当 row_id の1件を削除
                for j in range(len(st.session_state.pitches) - 1, -1, -1):
                    if st.session_state.pitches[j].get("row_id") == row_id:
                        st.session_state.pitches.pop(j)
                        break

                if deleted:
                    ok_count += 1

            st.success(f"{n}件取り消しました（スプレッドシート側は {ok_count}/{n} 行削除）")
            st.rerun()

# □ 1. 試合情報入力

col1, col2 = st.columns(2)

with col1:
    with st.expander("試合情報", expanded=False): 
        with st.form("game_form"):
            game_date = st.date_input("試合日", value=datetime.today())
            top_team = st.text_input("先攻チーム名")
            bottom_team = st.text_input("後攻チーム名")
            submitted = st.form_submit_button("試合情報を確定")
            if submitted:
                st.session_state.game_info = {
                    "date": game_date.strftime("%Y-%m-%d"),
                    "top_team": top_team,
                    "bottom_team": bottom_team
                }
                st.success("試合情報を保存しました")
    if st.session_state.game_info:
        game = st.session_state.game_info
        st.info(f"試合日: {game['date']} | 先攻: {game['top_team']} | 後攻: {game['bottom_team']}")

# □ 2. イニング情報
with col2:
    with st.expander("イニング情報", expanded=False): 
        with st.form("inning_form"):
            inning = st.number_input("現在のイニング", min_value=1, step=1)
            top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
            submitted = st.form_submit_button("イニング情報を保存")
            if submitted:
                st.session_state.inning_info = {
                    "inning": inning,
                    "top_bottom": top_bottom
                }
                st.success("イニング情報を保存しました")

    if st.session_state.inning_info:
        inn = st.session_state.inning_info
        st.info(f"現在: {inn['inning']} 回{inn['top_bottom']}")

# □ 3. 打席情報
st.header("3. 打席情報 (打者・投手・ランナー)")
with st.form("at_bat_form"):
    batter = st.text_input("打者名")
    batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"])
    pitcher = st.text_input("投手名")
    pitcher_side = st.selectbox("投手の利き腕", ["右", "左"])
    runner_1b = st.text_input("一塁ランナー")
    runner_2b = st.text_input("二塁ランナー")
    runner_3b = st.text_input("三塁ランナー")
    submitted = st.form_submit_button("打席情報を保存")
    if submitted:
        st.session_state.atbat_info = {
            "batter": batter,
            "batter_side": batter_side,
            "pitcher": pitcher,
            "pitcher_side": pitcher_side,
            "runner_1b": runner_1b,
            "runner_2b": runner_2b,
            "runner_3b": runner_3b
        }
        st.success("打席情報を保存しました")

if st.session_state.atbat_info:
    info = st.session_state.atbat_info
    st.info(f"打者: {info['batter']}({info['batter_side']}) vs 投手: {info['pitcher']}({info['pitcher_side']})")

# □ 4. 一球情報入力
st.header("4. 一球情報入力")
#コース選択
if use_light_mode:
    st.markdown("### グリッドでコースを選択（5×5）")
    batter_side = st.session_state.atbat_info.get("batter_side", "右") if st.session_state.atbat_info else "右"
    #レイアウト変更路にコード中の_verを順に増やしていく
    base_img, zone_bounds = make_strike_zone_base(batter_side, _ver=2)

    c1, c2 = st.columns(2)
    with c1:
        col = st.select_slider("横（1〜5）", options=[1,2,3,4,5], value=3, key="grid5_col")
    with c2:
        row = st.select_slider("縦（1〜5）", options=[1,2,3,4,5], value=3, key="grid5_row")

    snap = center_of_cell(col, row, zone_bounds)
    st.session_state.last_coords = snap
    st.session_state.marked_img_bytes = compose_marked_image_png(base_img, snap)

    in_strike = (2 <= col <= 4) and (2 <= row <= 4)
    zone_label = "ストライク" if in_strike else "ボール"
    st.image(Image.open(BytesIO(st.session_state.marked_img_bytes)), width=TARGET_WIDTH)
    pitch_course = f"({col},{row}) {zone_label} / X:{snap['x']}, Y:{snap['y']}"
    

else:
    # 打者の利き腕
    batter_side = st.session_state.atbat_info.get("batter_side", "右") if st.session_state.atbat_info else "右"

    # 線画のベース画像＋境界（キャッシュ）
    base_img, zone_bounds = make_strike_zone_base(batter_side, _ver=2)
    img_w, img_h = base_img.size
    display_w = TARGET_WIDTH
    display_h = int(img_h * display_w / img_w)

    # 初期化
    if "marked_img_bytes" not in st.session_state:
        st.session_state.marked_img_bytes = compose_marked_image_png(base_img, None)
    if "last_coords" not in st.session_state:
        st.session_state.last_coords = None

    st.markdown("### ストライクゾーンをクリック👇")
    coords_disp = streamlit_image_coordinates(
        Image.open(BytesIO(st.session_state.marked_img_bytes)),
        key="strike_zone_coords",
        width=display_w
    )

    # 表示 → 実画像の補正（幅は固定なので誤差ほぼないが一応）
    def to_image_coords(c):
        if not c:
            return None
        sx = img_w / float(display_w)
        sy = img_h / float(display_h)
        return {"x": int(round(c["x"] * sx)), "y": int(round(c["y"] * sy))}

    img_coords = to_image_coords(coords_disp) if coords_disp else None

    # クリック→セル判定→中心にスナップして表示（任意）
    # ...
    if img_coords:
        col, row, in_strike = point_to_5x5_cell(img_coords["x"], img_coords["y"], zone_bounds)

        # ★ 生のクリック座標でそのまま描画・保持
        if img_coords != st.session_state.last_coords:
            st.session_state.last_coords = img_coords
            st.session_state.marked_img_bytes = compose_marked_image_png(base_img, img_coords)

        zone_label = "ストライク" if in_strike else "ボール"
        pitch_course = f"({col},{row}) {zone_label} / X:{img_coords['x']}, Y:{img_coords['y']}"
    else:
        pitch_course = (
            f"({point_to_5x5_cell(st.session_state.last_coords['x'], st.session_state.last_coords['y'], zone_bounds)[0]},"
            f"{point_to_5x5_cell(st.session_state.last_coords['x'], st.session_state.last_coords['y'], zone_bounds)[1]})"
            f" / X:{st.session_state.last_coords['x']}, Y:{st.session_state.last_coords['y']}"
        ) if st.session_state.last_coords else "未選択"
   

# 一球の共通入力（フォーム外。pitch_resultはここで選ぶ）

strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ","盗塁"])
if strategy != "なし":
    strategy_result = st.selectbox(" 作戦結果",["成", "否"] ,key="stategy_result_select")
else:
    atbat_result = ""
pitch_type = st.selectbox("球種", ["ストレート", "カーブ", "スライダー", "チェンジアップ", "フォーク", "シュート","ツーシーム","その他"])
pitch_result = st.selectbox("結果", ["ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル",  "牽制", "打席終了"], key="pitch_result_selectbox")


# ↓打席終了のときフォーム外で詳細を即時入力
if pitch_result == "打席終了":
    st.markdown("**【打席結果入力】**")
    atbat_result = st.selectbox("打席結果",["三振(見)", "三振(空)","四球","死球","インプレー","その他"] ,key="batted_type_select")
else:
    atbat_result = ""


# ↓インプレーのときだけフォーム外で詳細を即時入力
if atbat_result == "インプレー":
    st.markdown("**【インプレー詳細入力】**")
    batted_type = st.selectbox("打球の種類", ["フライ", "ゴロ", "ライナー"], key="inplay_result_select")
    batted_position = st.selectbox("打球方向", ["投手", "一塁", "二塁", "三塁", "遊撃", "左翼", "中堅", "右翼","左中","右中"], key="batted_pos_select")
    batted_outcome = st.selectbox("結果", ["ヒット","2塁打","3塁打","ホームラン", "アウト", "エラー", "併殺", "犠打", "犠飛"], key="batted_out_select")

else:
    batted_type = ""
    batted_position = ""
    batted_outcome = ""


# □ 記録ボタン（すべての情報を記録）
if st.button("この一球を記録"):
    game_info = st.session_state.game_info
    inning_info = st.session_state.inning_info
    atbat_info = st.session_state.atbat_info

    # ★ 一意IDを付与
    row_id = str(uuid.uuid4())

    pitch_record = {
        # ★ 主キー
        "row_id": row_id,

        # 試合情報
        "date": game_info.get("date", ""),
        "top_team": game_info.get("top_team", ""),
        "bottom_team": game_info.get("bottom_team", ""),

        # イニング情報
        "inning": inning_info.get("inning", ""),
        "top_bottom": inning_info.get("top_bottom", ""),

        # 打席情報
        "batter": atbat_info.get("batter", ""),
        "batter_side": atbat_info.get("batter_side", ""),
        "pitcher": atbat_info.get("pitcher", ""),
        "pitcher_side": atbat_info.get("pitcher_side", ""),
        "runner_1b": atbat_info.get("runner_1b", ""),
        "runner_2b": atbat_info.get("runner_2b", ""),
        "runner_3b": atbat_info.get("runner_3b", ""),

        # 一球情報
        "pitch_type": pitch_type,
        "pitch_result": pitch_result,
        "pitch_course": pitch_course,
        "strategy": strategy,
        "batted_type": batted_type,
        "batted_position": batted_position,
        "batted_outcome": batted_outcome,
    }

    st.session_state.pitches.append(pitch_record)
    sheet_name = save_to_google_sheets(st.session_state.pitches)

    # ★ どのシートのどの行かを記録
    st.session_state.save_log.append({"sheet": sheet_name, "row_id": row_id})
    if len(st.session_state.save_log) > 100:
        st.session_state.save_log = st.session_state.save_log[-100:]

    st.success("一球の情報を保存しました")

# □ 最新の入力履歴表示
if st.session_state.pitches:
    st.subheader("📊 最近の投球記録（直近15件）")
    st.dataframe(st.session_state.pitches[-15:])




# === スポナビ風の途中経過ビュー =========================================
st.markdown("## 📰 試合経過")

# ---- 0) B/S/Oの暫定推定（簡易） ----
def summarize_state(pitches: list[dict]):
    balls = strikes = outs = 0
    last_5 = []
    for rec in pitches:
        pr = rec.get("pitch_result", "") or ""
        ar = rec.get("atbat_result", "") or ""
        bo = rec.get("batted_outcome", "") or ""
        inn = rec.get("inning", "?")
        tb  = rec.get("top_bottom", "?")
        batter  = rec.get("batter", "")
        pitcher = rec.get("pitcher", "")

        desc = f"{inn}回{tb}｜{batter} vs {pitcher}｜{pr}"
        if ar:
            desc += f" → {ar}"
        if ar == "インプレー" and bo:
            desc += f"（{bo}）"
        last_5.append(desc)

        # 簡易カウント
        if pr.startswith("ボール"):
            balls = min(3, balls + 1)
        elif pr.startswith("ストライク"):
            if strikes < 2:
                strikes += 1
        elif pr.startswith("ファウル"):
            if strikes < 2:
                strikes += 1

        # 打席終了時のアウト推定＆カウントリセット
        if pr == "打席終了":
            if ar.startswith("三振"):
                outs = min(3, outs + 1)
            if ar == "インプレー" and bo in ("アウト", "併殺", "犠打", "犠飛"):
                outs = min(3, outs + (2 if bo == "併殺" else 1))
            balls = 0
            strikes = 0

    return {"balls": balls, "strikes": strikes, "outs": outs, "last_5": last_5[-5:]}

state = summarize_state(st.session_state.pitches)

# ---- 1) ヘッダー帯 ----
game = st.session_state.game_info if st.session_state.game_info else {}
inn  = st.session_state.inning_info if st.session_state.inning_info else {}
t_top = game.get("top_team", "-")
t_bot = game.get("bottom_team", "-")
inning_lab = f"{inn.get('inning','-')}回{inn.get('top_bottom','-')}" if inn else "-"

hdr1, hdr2, hdr3 = st.columns([3, 2, 3])
with hdr1:
    st.markdown(f"### {t_top}")
with hdr2:
    st.markdown(f"#### {inning_lab}")
with hdr3:
    st.markdown(f"### {t_bot}")

# ---- 2) B/S/O と Bases（SVG） ----
def bases_svg(r1: bool, r2: bool, r3: bool) -> str:
    """ひし形の塁マーク。走者がいれば緑、なければ白。"""
    def base(x, y, filled: bool) -> str:
        color = "#2E7D32" if filled else "#FFFFFF"
        return (
            f'<polygon points="{x},{y-12} {x+12},{y} {x},{y+12} {x-12},{y}" '
            f'fill="{color}" stroke="#111" stroke-width="2"/>'
        )

    return (
        '<svg width="160" height="120" viewBox="0 0 160 120">'
        '<rect x="0" y="0" width="160" height="120" fill="transparent"/>'
        f'{base(110, 60, r1)}'     # 一塁（右）
        f'{base(80,  30, r2)}'     # 二塁（上）
        f'{base(50,  60, r3)}'     # 三塁（左）
        f'{base(80,  90, False)}'  # 本塁（常に白）
        '</svg>'
    )

def bso_lights(b, s, o) -> str:
    """B/S/O を丸ランプで表示"""
    def lamps(n, on, color):
        dots = []
        for i in range(n):
            fill = color if i < on else "#ddd"
            dots.append(f'<circle cx="{12+i*18}" cy="10" r="6" fill="{fill}" />')
        return "".join(dots)

    return (
        '<svg width="200" height="40" viewBox="0 0 200 40">'
        '<text x="0" y="15" font-size="12">B</text>'
        '<g transform="translate(12,0)">' + lamps(3, b, "#43A047") + '</g>'
        '<text x="72" y="15" font-size="12">S</text>'
        '<g transform="translate(84,0)">' + lamps(2, s, "#FB8C00") + '</g>'
        '<text x="132" y="15" font-size="12">O</text>'
        '<g transform="translate(144,0)">' + lamps(2, min(2, o), "#E53935") + '</g>'
        '</svg>'
    )

# ランナーは“打席情報フォーム”の値をそのまま表示（自動進塁は未実装）
rinfo = st.session_state.atbat_info if st.session_state.atbat_info else {}
r1 = bool(rinfo.get("runner_1b"))
r2 = bool(rinfo.get("runner_2b"))
r3 = bool(rinfo.get("runner_3b"))

colA, colB = st.columns([3, 4])
with colA:
    st.markdown("#### B / S / O")
    st.components.v1.html(bso_lights(state["balls"], state["strikes"], state["outs"]), height=45)
    st.markdown("#### Bases")
    st.components.v1.html(bases_svg(r1, r2, r3), height=130)

# ---- 3) 最終プレー：直近5「打席」を“短い結果表記”で、作戦/走者/イニング見出し付き ----
def _join_nonempty(sep, *xs):
    return sep.join([x for x in xs if x])

def _runner_label(rec: dict) -> str:
    r1 = bool(rec.get("runner_1b")); r2 = bool(rec.get("runner_2b")); r3 = bool(rec.get("runner_3b"))
    if not (r1 or r2 or r3):
        return "走者なし"
    names = []
    if r1: names.append("一")
    if r2: names.append("二")
    if r3: names.append("三")
    return "走者:" + "".join(names) + "塁"

# 結果フォーマッタ（例：左中2塁打／遊ゴロ／三振(空)／四球…）
_ABBR_BATTED_TYPE = {"フライ": "飛", "ゴロ": "ゴロ", "ライナー": "直"}
def format_play_result(rec: dict) -> str:
    ar = (rec.get("atbat_result") or "").strip()
    if not ar:
        return rec.get("pitch_result", "") or "打席終了"
    if ar != "インプレー":
        return ar
    pos  = (rec.get("batted_position") or "").strip()
    btyp = (rec.get("batted_type") or "").strip()
    outc = (rec.get("batted_outcome") or "").strip()
    btyp_abbr = _ABBR_BATTED_TYPE.get(btyp, "")
    if outc in ("ヒット", "２塁打", "3塁打", "ホームラン", "エラー"):
        return f"{pos}{outc}"
    if outc in ("犠打", "犠飛"):
        return f"{pos}{outc}"
    if outc == "併殺":
        return f"{pos}{btyp_abbr}併殺" if btyp_abbr else f"{pos}併殺"
    if outc == "アウト":
        return f"{pos}{btyp_abbr}" if btyp_abbr else f"{pos}アウト"
    return " ".join([x for x in (ar, btyp, pos, outc) if x])

def last_5_atbats_grouped(pitches: list[dict]) -> list[tuple[str, str]]:
    """
    直近5打席を (見出し, 本文) の配列で返す。
    見出し：'3回表' など（前件とイニングが変わる箇所だけ入る／同じなら空文字）
    本文  ：'打者 vs 投手｜短い結果表記｜作戦:◯（成否）｜走者:...'
    ※ 新しい→古いの順で返す
    """
    ab = [rec for rec in pitches if rec.get("pitch_result") == "打席終了"][-5:]
    prev_inn = prev_tb = None
    tmp: list[tuple[str, str]] = []
    for rec in ab:
        inn = rec.get("inning", "?"); tb = rec.get("top_bottom", "?")
        batter  = rec.get("batter", ""); pitcher = rec.get("pitcher", "")
        play_disp = format_play_result(rec)
        strat = rec.get("strategy", "なし") or "なし"
        sres  = rec.get("strategy_result", "")
        strat_disp = f"作戦:{strat}" + (f"（{sres}）" if strat != "なし" and sres else "") if strat != "なし" else ""
        runners = _runner_label(rec)
        body = "｜".join([f"{batter} vs {pitcher}", play_disp, strat_disp if strat_disp else "", runners]).replace("｜｜", "｜").strip("｜")
        heading = ""
        if (inn, tb) != (prev_inn, prev_tb):
            heading = f"{inn}回{tb}"
            prev_inn, prev_tb = inn, tb
        tmp.append((heading, body))
    return list(reversed(tmp))  # 最新→過去

with colB:
    st.markdown("#### 最終プレー（直近5打席）")
    items = last_5_atbats_grouped(st.session_state.pitches)
    if items:
        current_heading = None
        for heading, body in items:
            if heading and heading != current_heading:
                st.markdown(f"**— {heading} —**")
                current_heading = heading
            st.markdown(f"- {body}")
    else:
        st.caption("まだ打席結果がありません。")

# ---- 4) イニングごとの記録（短い結果表記で） ----
with st.expander("🧾 イニングごとの記録（結果）", expanded=False):
    ab = [rec for rec in st.session_state.pitches if rec.get("pitch_result") == "打席終了"]
    if not ab:
        st.caption("記録がまだありません。")
    else:
        # イニング→表裏→時系列
        def _sort_key(rec):
            inn = int(rec.get("inning") or 0)
            tb  = 0 if rec.get("top_bottom") == "表" else 1
            return (inn, tb)
        ab_sorted = sorted(ab, key=_sort_key)

        current = (None, None)
        for rec in ab_sorted:
            inn  = rec.get("inning", "?")
            tb   = rec.get("top_bottom", "?")
            if (inn, tb) != current:
                st.markdown(f"**— {inn}回{tb} —**")
                current = (inn, tb)

            batter  = rec.get("batter", "")
            pitcher = rec.get("pitcher", "")
            play    = format_play_result(rec)
            strat   = rec.get("strategy", "なし") or "なし"
            sres    = rec.get("strategy_result", "")
            strat_disp = f"｜作戦:{strat}" + (f"（{sres}）" if strat != "なし" and sres else "") if strat != "なし" else ""
            rlab = _runner_label(rec)
            st.markdown(f"- {batter} vs {pitcher}｜{play}{strat_disp}｜{rlab}")
# =======================================================================

# =======================================================================
# === 補足入力（簡易入力との統合＋自動シート名生成） ==========================
# =======================================================================
st.header("補足入力（簡易入力データの後編集）")

# 1. 試合情報を入力（自動でシート名を生成）
st.subheader("対象試合を指定")

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

# 2. シートを読み込む
try:
    df = load_game_sheet(sheet_name)
except Exception as e:
    st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("この試合シートにはまだデータがありません。")
    st.stop()

st.dataframe(df)

# 3. 編集対象を指定
st.subheader("編集対象の指定")
inning = st.number_input("イニング", min_value=1, step=1)
top_bottom = st.radio("表裏", ["表", "裏"], horizontal=True)
order = st.number_input("打順", min_value=1, max_value=9, step=1)

# 4. 条件に一致する行を検索
target = df[
    (df["inning"].astype(str) == str(inning)) &
    (df["top_bottom"] == top_bottom) &
    (df["order"].astype(str) == str(order))
]

if len(target) == 0:
    st.warning("一致する行が見つかりません。")
    st.stop()
elif len(target) > 1:
    st.warning("該当する行が複数あります。row_idで区別が必要です。")
    st.dataframe(target)
    row_id = st.selectbox("更新したい行を選択（row_id）", target["row_id"].tolist())
    target_row = target[target["row_id"] == row_id].iloc[0]
else:
    target_row = target.iloc[0]
    row_id = target_row["row_id"]
    st.success(f"{inning}回{top_bottom} {order}番 → row_id: {row_id}")

# 5. 補足フォーム
st.subheader("補足情報の入力")
batter = st.text_input("打者名", value=target_row.get("batter", ""))
batter_side = st.selectbox("打者の利き腕", ["右", "左", "両"], 
                           index=["右", "左", "両"].index(target_row.get("batter_side", "右")) if "batter_side" in target_row else 0)
pitcher = st.text_input("投手名", value=target_row.get("pitcher", ""))
pitcher_side = st.selectbox("投手の利き腕", ["右", "左"],
                            index=["右", "左"].index(target_row.get("pitcher_side", "右")) if "pitcher_side" in target_row else 0)
runner_1b = st.text_input("一塁ランナー", value=target_row.get("runner_1b", ""))
runner_2b = st.text_input("二塁ランナー", value=target_row.get("runner_2b", ""))
runner_3b = st.text_input("三塁ランナー", value=target_row.get("runner_3b", ""))

pitch_result = st.selectbox("結果（任意補足）", 
                            ["", "ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "打席終了"],
                            index=0)

atbat_result = st.text_input("打席結果（例: 左中2塁打・三振など）", value=target_row.get("atbat_result", ""))
batted_position = st.text_input("打球方向（例: 遊撃・中堅など）", value=target_row.get("batted_position", ""))
batted_outcome = st.text_input("結果（例: ヒット・併殺など）", value=target_row.get("batted_outcome", ""))
strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ", "盗塁", "バスター"],
                        index=["なし", "バント", "エンドラン", "スクイズ", "盗塁", "バスター"].index(target_row.get("strategy", "なし")) if "strategy" in target_row else 0)
strategy_result = st.selectbox("作戦結果", ["", "成", "否"], 
                               index=["", "成", "否"].index(target_row.get("strategy_result", "")) if "strategy_result" in target_row else 0)

# 6. 更新処理
if st.button("この行を更新"):
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
        "batted_position": batted_position,
        "batted_outcome": batted_outcome,
        "strategy": strategy,
        "strategy_result": strategy_result,
    }

    ok = update_row_by_id(sheet_name, row_id, updates)
    if ok:
        st.success(f"{inning}回{top_bottom} {order}番（row_id: {row_id[:8]}...）を更新しました！")
    else:
        st.error("更新に失敗しました。対象行が見つからない可能性があります。")