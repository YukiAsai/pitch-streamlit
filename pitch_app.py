import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheets設定
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
client = gspread.authorize(creds)
sheet = client.open("Pitch_Data_2025").sheet1  # スプレッドシート名をここで指定

# タイトル
st.title("⚾ 一球データ入力アプリ（Google Sheets連携版）")

# 入力フォーム
with st.form("pitch_form"):
    col1, col2 = st.columns(2)
    with col1:
        game_date = st.date_input("試合日", value=datetime.today())
        top_team = st.text_input("先攻（チーム名）")
        bottom_team = st.text_input("後攻（チーム名）")
        score_top = st.number_input("得点（先攻）", min_value=0, step=1)
        score_bottom = st.number_input("得点（後攻）", min_value=0, step=1)
        inning = st.number_input("イニング", min_value=1, step=1)
        top_bottom = st.selectbox("表裏", ["表", "裏"])
        out_count = st.selectbox("アウトカウント", ["0", "1", "2"])

    with col2:
        runner_1b = st.text_input("一塁ランナー（名前 or 無し）")
        runner_2b = st.text_input("二塁ランナー（名前 or 無し）")
        runner_3b = st.text_input("三塁ランナー（名前 or 無し）")
        batter = st.text_input("打者")
        batter_side = st.selectbox("打者左右", ["右", "左", "両"])
        pitcher = st.text_input("投手")
        pitcher_side = st.selectbox("投手左右", ["右", "左"])
        pitch_type = st.selectbox("球種", ["ストレート", "カーブ", "スライダー", "チェンジアップ"])
        pitch_course = st.text_input("コース（例：内角高め）")
        result = st.text_input("結果（例：空振り、右飛など）")
        strategy_flag = st.selectbox("作戦有無", ["なし", "バント", "エンドラン", "スクイズ"])

    submitted = st.form_submit_button("保存する")

    if submitted:
        new_data = [
            game_date.strftime("%Y-%m-%d"),
            top_team,
            bottom_team,
            score_top,
            score_bottom,
            inning,
            top_bottom,
            out_count,
            runner_1b,
            runner_2b,
            runner_3b,
            batter,
            batter_side,
            pitcher,
            pitcher_side,
            pitch_type,
            pitch_course,
            result,
            strategy_flag
        ]

        if not batter or not pitcher:
            st.warning("打者名と投手名は必須です。")
        else:
            sheet.append_row(new_data)
            st.success("✅ Google Sheets にデータを保存しました！")

# 入力済みデータの表示（直近10行）
data = sheet.get_all_records()
df = pd.DataFrame(data)
if not df.empty:
    st.subheader("📊 入力済みデータ（最新10件）")
    st.dataframe(df.tail(10), use_container_width=True)