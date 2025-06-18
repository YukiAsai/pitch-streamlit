import streamlit as st
from datetime import datetime
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates
import os

# スプレッドシートへの保存
def save_to_google_sheets(data):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scope
    )
    client = gspread.authorize(creds)
    sheet = client.open("Pitch_Data_2025").sheet1

    df = pd.DataFrame(data)
    sheet.clear()
    sheet.update([df.columns.values.tolist()] + df.values.tolist())

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

# □ 試合リセットボタン
st.sidebar.header("リセット操作")
if st.sidebar.button("🔄 全てのデータをリセット"):
    st.session_state.clear()
    st.rerun()

# □ 1. 試合情報入力

col1, col2 = st.columns(2)

with col1:
    with st.expander("試合情報", expanded=False): 
        if not st.session_state.game_info:
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
with st.form("atbat_form"):
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

# 打席情報から打者の利き腕を取得
batter_side = st.session_state.atbat_info.get("batter_side", "右") if st.session_state.atbat_info else "右"
strike_zone_img = "strike_zone_right.png" if batter_side == "右" else "strike_zone_left.png"

# 画像の存在チェック
if not os.path.exists(strike_zone_img):
    st.error(f"❌ {strike_zone_img} が見つかりません。ファイル名・場所を確認してください。")
    st.stop()

# ストライクゾーン画像クリックで投球コース
base_img = Image.open(strike_zone_img).convert("RGBA")
img = base_img.copy()

if st.session_state.last_coords:
    draw = ImageDraw.Draw(img)
    x = st.session_state.last_coords["x"]
    y = st.session_state.last_coords["y"]
    radius = 5
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="red")

st.markdown("### ストライクゾーンをクリック👇")
coords = streamlit_image_coordinates(img, key="strike_zone_coords")
if coords:
    st.session_state.last_coords = coords

if st.session_state.last_coords:
    pitch_course = f"X:{st.session_state.last_coords['x']}, Y:{st.session_state.last_coords['y']}"
else:
    pitch_course = "未選択"

# 一球の共通入力（フォーム外。pitch_resultはここで選ぶ）

strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ"])
pitch_type = st.selectbox("球種", ["ストレート", "カーブ", "スライダー", "チェンジアップ", "フォーク", "その他"])
pitch_result = st.selectbox("結果", ["ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "インプレー", "牽制", "死球", "その他"], key="pitch_result_selectbox")

# ↓インプレーのときだけフォーム外で詳細を即時入力
if pitch_result == "インプレー":
    st.markdown("**【インプレー詳細入力】**")
    batted_type = st.selectbox("打球の種類", ["フライ", "ゴロ", "ライナー"], key="batted_type_select")
    batted_position = st.selectbox("打球方向", ["投手方向", "一塁方向", "二塁方向", "三塁方向", "遊撃方向", "左翼", "中堅", "右翼"], key="batted_pos_select")
    batted_outcome = st.selectbox("結果", ["ヒット", "アウト", "エラー", "併殺", "犠打", "犠飛"], key="batted_out_select")

else:
    batted_type = ""
    batted_position = ""
    batted_outcome = ""


# 記録ボタン
if st.button("この一球を記録"):
    pitch_record = {
        "inning": st.session_state.inning_info.get("inning", ""),
        "top_bottom": st.session_state.inning_info.get("top_bottom", ""),
        "batter": st.session_state.atbat_info.get("batter", ""),
        "pitcher": st.session_state.atbat_info.get("pitcher", ""),
        "pitch_type": pitch_type,
        "pitch_result": pitch_result,
        "pitch_course": pitch_course,
        "strategy": strategy,
        "batted_type": batted_type,
        "batted_position": batted_position,
        "batted_outcome": batted_outcome,
    }
    st.session_state.pitches.append(pitch_record)
    save_to_google_sheets(st.session_state.pitches)
    st.success("一球の情報を保存しました")

# □ 最新の入力履歴表示
if st.session_state.pitches:
    st.subheader("📊 最近の投球記録（直近5件）")
    st.dataframe(st.session_state.pitches[-5:])

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
