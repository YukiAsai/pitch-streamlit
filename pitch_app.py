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
GRID_N = 9         # 9×9

@st.cache_resource(show_spinner=False)
def make_strike_zone_base(hand: str = "右") -> Image.Image:
    """
    ストライクゾーンを線だけで描いたベース画像（RGB）を生成して返す。
    hand == "左" の場合は水平反転して返す（お好みで）。
    """
    # 好みのレイアウト（見やすい比率）
    W = TARGET_WIDTH
    H = int(W * 1.1)     # 縦横比は自由。ここでは少し縦長
    PAD = int(W * 0.1)   # 余白
    STROKE = 2
    BG = (255, 255, 255)
    LINE = (0, 0, 0)

    # 画像キャンバス
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # ゾーン矩形（ここを“左端/右端/上端/下端”の基準にする）
    x_left  = PAD
    x_right = W - PAD
    y_top   = PAD
    y_bot   = H - PAD

    # 外枠
    draw.rectangle([x_left, y_top, x_right, y_bot], outline=LINE, width=STROKE)

    # 9×9のグリッド線
    # 縦線
    for i in range(1, GRID_N):
        x = x_left + (x_right - x_left) * i / GRID_N
        draw.line([(x, y_top), (x, y_bot)], fill=LINE, width=1)
    # 横線
    for j in range(1, GRID_N):
        y = y_top + (y_bot - y_top) * j / GRID_N
        draw.line([(x_left, y), (x_right, y)], fill=LINE, width=1)

    # 投手・打者向きで反転したければここで水平反転
    if hand == "左":
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    # 必要なら座標をセッションに入れておく（後で使う）
    st.session_state["_zone_bounds"] = dict(
        x_left=x_left, x_right=x_right, y_top=y_top, y_bottom=y_bot, W=W, H=H
    )
    return img

def compose_marked_image_png(base: Image.Image, coords: dict | None) -> bytes:
    canvas = base.copy()
    if coords:
        draw = ImageDraw.Draw(canvas)
        x, y = coords["x"], coords["y"]
        r = 3
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 0, 0))
    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)  # 線画はPNGの方が効く
    return buf.getvalue()


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
    st.markdown("### グリッドでコースを選択（9×9）")

    # 表示用のベース画像（固定幅に縮小済み）
    batter_side = st.session_state.atbat_info.get("batter_side", "右") if st.session_state.atbat_info else "右"
    base_img = get_base_image(batter_side)

    # --- ここが今回の肝：ゾーン境界（表示画像のピクセル基準） ---
    X_LEFT, X_RIGHT = 89, 212
    Y_TOP, Y_BOTTOM = 215, 81   # 上が215、下が81（Yが大きい方が上という仕様に合わせる）

    # 選択UI
    c1, c2 = st.columns(2)
    with c1:
        gx = st.select_slider("横（1=内角, 9=外角）", options=list(range(1, 10)), value=5, key="grid_x")
    with c2:
        gy = st.select_slider("縦（1=低め, 9=高め）", options=list(range(1, 10)), value=5, key="grid_y")

    # 区間の中心にマップ（線形補間）。Yは top→bottom へ向かうので逆方向もOK
    def lerp(a, b, t):  # t in [0,1)
        return a + (b - a) * t

    t_x = (gx - 0.5) / 9.0
    t_y = (gy - 0.5) / 9.0

    x = int(round(lerp(X_LEFT,  X_RIGHT,  t_x)))
    y = int(round(lerp(Y_TOP,   Y_BOTTOM, t_y)))  # 上端=215→下端=81 方向で補間

    # 状態更新＆表示
    st.session_state.last_coords = {"x": x, "y": y}
    st.session_state.marked_img_bytes = compose_marked_image_png(base_img, st.session_state.last_coords)

    st.image(Image.open(BytesIO(st.session_state.marked_img_bytes)), width=TARGET_WIDTH)
    pitch_course = f"X:{x}, Y:{y}"

else:
    # 打席情報から打者の利き腕
    batter_side = st.session_state.atbat_info.get("batter_side", "右") if st.session_state.atbat_info else "右"

    # ディスクから読まずに線画ベースを生成 & キャッシュ取得
    base_img = make_strike_zone_base(batter_side)
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

    # 表示→実画像のスケール補正（幅は固定なので誤差ほぼゼロだが一応）
    def to_image_coords(c):
        if not c:
            return None
        sx = img_w / float(display_w)
        sy = img_h / float(display_h)
        return {"x": int(round(c["x"] * sx)), "y": int(round(c["y"] * sy))}

    img_coords = to_image_coords(coords_disp) if coords_disp else None

    # 座標が変わった時だけ赤点を再描画
    if img_coords and img_coords != st.session_state.last_coords:
        st.session_state.last_coords = img_coords
        st.session_state.marked_img_bytes = compose_marked_image_png(base_img, img_coords)

    # 表示用のコース文字列
    if st.session_state.last_coords:
        pitch_course = f"X:{st.session_state.last_coords['x']}, Y:{st.session_state.last_coords['y']}"
    else:
        pitch_course = "未選択"
   

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

