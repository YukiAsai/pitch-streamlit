import streamlit as st
from datetime import datetime

st.set_page_config(page_title="一球データ入力アプリver2", layout="wide")

# ■■ セッション情報初期化 ■■
if "game_info" not in st.session_state:
    st.session_state.game_info = {}
if "inning_info" not in st.session_state:
    st.session_state.inning_info = {}
if "atbat_info" not in st.session_state:
    st.session_state.atbat_info = {}
if "pitches" not in st.session_state:
    st.session_state.pitches = []

# □ 試合リセットボタン
st.sidebar.header("リセット操作")
if st.sidebar.button("🔄 全てのデータをリセット"):
    st.session_state.clear()
    st.experimental_rerun()

# □ 1. 試合情報入力
st.header("1. 試合情報 (最初の1回のみ入力)")
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
else:
    game = st.session_state.game_info
    st.info(f"試合日: {game['date']} | 先攻: {game['top_team']} | 後攻: {game['bottom_team']}")

# □ 2. イニング情報
st.header("2. イニング情報 (表/裏 の切替)")
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
with st.form("pitch_form"):
    pitch_type = st.selectbox("球種", ["ストレート", "カーブ", "スライダー", "チェンジアップ", "フォーク", "その他"])
    pitch_result = st.selectbox("結果", ["ストライク（見逃し）", "ストライク（空振り）", "ボール", "ファウル", "インプレー", "牽制", "死球", "その他"])
    pitch_course = st.text_input("コース（例：外角低め）")
    strategy = st.selectbox("作戦", ["なし", "バント", "エンドラン", "スクイズ"])
    submit_pitch = st.form_submit_button("この一球を記録")
    if submit_pitch:
        pitch_record = {
            "inning": st.session_state.inning_info.get("inning", ""),
            "top_bottom": st.session_state.inning_info.get("top_bottom", ""),
            "batter": st.session_state.atbat_info.get("batter", ""),
            "pitcher": st.session_state.atbat_info.get("pitcher", ""),
            "pitch_type": pitch_type,
            "pitch_result": pitch_result,
            "pitch_course": pitch_course,
            "strategy": strategy
        }
        st.session_state.pitches.append(pitch_record)
        st.success("一球の情報を保存しました")

# □ 最新の入力履歴表示
if st.session_state.pitches:
    st.subheader("📊 最近の投球記録（直近5件）")
    st.table(st.session_state.pitches[-5:])