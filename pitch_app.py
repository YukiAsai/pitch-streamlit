import streamlit as st
from datetime import datetime
from PIL import Image, ImageDraw, ImageOps
from streamlit_image_coordinates import streamlit_image_coordinates
import os
import uuid

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

from io import BytesIO

TARGET_WIDTH = 300
BACKGROUND_RGB = (255, 255, 255)   # ← 背景を白に。薄いグレーなら (245,245,245) など

from io import BytesIO

TARGET_WIDTH = 300  # 表示幅は固定に
GRID_TOTAL = 5          # 5x5：外周1マスがボールゾーン
GRID_CORE = 3           # 内側3x3がストライクゾーン
PAD_RATIO = 0.1         # 画像の余白割合

# 線画ベースを生成＆キャッシュ
@st.cache_resource(show_spinner=False)
def make_strike_zone_base(hand: str = "右"):
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

    # 打者が左なら左右反転（好みで）
    if hand == "左":
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

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

    # スプレッドシート本体を開く
    spreadsheet = client.open("Pitch_Data_2025")

    # 最新の一球
    latest = data[-1]  # ここに "row_id" が含まれている前提
    date = latest.get("date", "unknown")
    top_team = latest.get("top_team", "TopTeam")
    bottom_team = latest.get("bottom_team", "BottomTeam")
    top_bottom = latest.get("top_bottom", "表")

    # 攻撃側チーム名でシート名
    batter_team = top_team if top_bottom == "表" else bottom_team
    sheet_name = f"{date}_{batter_team}"

    # シート取得または作成
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        existing_data = worksheet.get_all_values()
        has_header = len(existing_data) > 0
        existing_header = existing_data[0] if has_header else []
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=30)
        has_header = False
        existing_header = []

    # 望ましいヘッダー（latest のキー順）
    desired_header = list(latest.keys())

    if not has_header:
        worksheet.append_row(desired_header)
        existing_header = desired_header
    else:
        # 既存ヘッダーに不足カラムがあれば右端に追加
        missing = [col for col in desired_header if col not in existing_header]
        if missing:
            new_header = existing_header + missing
            worksheet.update('1:1', [new_header])
            existing_header = new_header

    # 行データを既存ヘッダー順に並べる
    row_to_append = [latest.get(col, "") for col in existing_header]
    worksheet.append_row(row_to_append)

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
    base_img, zone_bounds = make_strike_zone_base(batter_side)

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
    base_img, zone_bounds = make_strike_zone_base(batter_side)
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

strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ"])
if strategy != "なし":
    strategy_result = st.selectbox(" 作戦結果",["成", "否"] ,key="stategy_result_select")
else:
    atbat_result = ""
pitch_type = st.selectbox("球種", ["ストレート", "カーブ", "スライダー", "チェンジアップ", "フォーク", "その他"])
pitch_result = st.selectbox("結果", ["ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル",  "牽制", "打席終了"], key="pitch_result_selectbox")


# ↓打席終了のときフォーム外で詳細を即時入力
if pitch_result == "打席終了":
    st.markdown("**【打席結果入力】**")
    atbat_result = st.selectbox("打席結果",["三振(見)", "三振(空)","四球","死球","インプレー"] ,key="batted_type_select")
else:
    atbat_result = ""


# ↓インプレーのときだけフォーム外で詳細を即時入力
if atbat_result == "インプレー":
    st.markdown("**【インプレー詳細入力】**")
    batted_type = st.selectbox("打球の種類", ["フライ", "ゴロ", "ライナー"], key="inplay_result_select")
    batted_position = st.selectbox("打球方向", ["投手", "一塁", "二塁", "三塁", "遊撃", "左翼", "中堅", "右翼","左中","右中"], key="batted_pos_select")
    batted_outcome = st.selectbox("結果", ["ヒット","２塁打","3塁打","ホームラン", "アウト", "エラー", "併殺", "犠打", "犠飛"], key="batted_out_select")

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
    st.subheader("📊 最近の投球記録（直近5件）")
    st.dataframe(st.session_state.pitches[-5:])

