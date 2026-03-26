import streamlit as st
import google.generativeai as genai
import json
import re
from datetime import datetime, timedelta
from gtts import gTTS
from io import BytesIO
from google.oauth2 import service_account
from googleapiclient.discovery import build

# [필수] 페이지 설정
st.set_page_config(page_title="출퇴근 갓생 단어장 V2.2", layout="centered")

SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

# --- [0. 초기화 및 상태 관리] ---
if 'sh' not in st.session_state: st.session_state.sh = None
if 'all_data' not in st.session_state: st.session_state.all_data = []
if 'existing_words' not in st.session_state: st.session_state.existing_words = []
if 'todays_words' not in st.session_state: st.session_state.todays_words = []
if 'current_idx' not in st.session_state: st.session_state.current_idx = 0
if 'show_meaning' not in st.session_state: st.session_state.show_meaning = False
if 'is_finished' not in st.session_state: st.session_state.is_finished = False

# 학습 통계 및 일괄 업데이트용 메모리 공간
if 'correct_cnt' not in st.session_state: st.session_state.correct_cnt = 0
if 'wrong_cnt' not in st.session_state: st.session_state.wrong_cnt = 0
if 'pending_updates' not in st.session_state: st.session_state.pending_updates = {}
if 'saved_to_cloud' not in st.session_state: st.session_state.saved_to_cloud = False

# [V2.2 신규] 하루 신규 단어 할당량 기본값 설정
if 'daily_quota' not in st.session_state: st.session_state.daily_quota = 15

def auto_google_sync():
    """구글 스프레드시트 연동"""
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return build('sheets', 'v4', credentials=creds)

def load_voca_data():
    """구글 시트에서 데이터를 긁어와 대시보드 및 오늘 단어를 세팅합니다."""
    if st.session_state.sh is None:
        st.session_state.sh = auto_google_sync()
        
    result = st.session_state.sh.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="'Voca'!A2:F1000").execute()
    rows = result.get('values', [])
    
    today_str = str(datetime.now().date())
    
    review_words = [] # 복습해야 할 단어 (레벨 1 이상)
    new_words = []    # 신규 단어 (레벨 0, 날짜 없음/오늘)
    existing = []
    
    for i, row in enumerate(rows):
        row += [''] * (6 - len(row)) # 빈칸 방어
        word = str(row[0]).strip()
        if word: existing.append(word.lower())
        
        # 데이터 정제
        level_str = str(row[3]).strip()
        level = int(level_str) if level_str.isdigit() else 0
        date_str = str(row[4]).strip()
        mistakes_str = str(row[5]).strip()
        mistakes = int(mistakes_str) if mistakes_str.isdigit() else 0
        
        word_data = {
            "row_idx": i + 2, 
            "word": word,
            "meaning": row[1],
            "example": row[2],
            "level": level,
            "date": date_str,
            "mistakes": mistakes
        }
        
        # [V2.2 핵심 로직] 복습 단어 vs 신규 단어 분리
        # 1. 복습 단어: 레벨이 1 이상이고, 복습 날짜가 오늘 이하인 경우 (무조건 포함)
        if level > 0 and date_str and date_str <= today_str:
            review_words.append(word_data)
            
        # 2. 신규 단어: 레벨이 0이고, 날짜가 비어있거나 오늘 이하인 경우 (할당량만큼만 자름)
        elif level == 0 and (not date_str or date_str <= today_str):
            new_words.append(word_data)
            
    # 할당량(Quota) 적용하여 오늘의 학습 리스트 완성
    todays = review_words + new_words[:st.session_state.daily_quota]
            
    st.session_state.all_data = rows
    st.session_state.existing_words = existing
    st.session_state.todays_words = todays
    st.session_state.current_idx = 0
    st.session_state.is_finished = False
    
    # 통계 및 메모리 초기화
    st.session_state.correct_cnt = 0
    st.session_state.wrong_cnt = 0
    st.session_state.pending_updates = {}
    st.session_state.saved_to_cloud = False

def batch_update_to_sheet():
    """메모리에 쌓인 업데이트 내역을 구글 시트에 한 번에 전송합니다."""
    if not st.session_state.pending_updates: return
    
    data = []
    for row_idx, values in st.session_state.pending_updates.items():
        data.append({
            "range": f"'Voca'!D{row_idx}:F{row_idx}",
            "values": [values]
        })
        
    body = {"valueInputOption": "RAW", "data": data}
    st.session_state.sh.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body
    ).execute()
    st.session_state.pending_updates.clear()

def play_audio(text, lang='en'):
    tts = gTTS(text=text, lang=lang)
    mp3_fp = BytesIO()
    tts.write_to_fp(mp3_fp)
    st.audio(mp3_fp, format='audio/mp3')

# 처음 로드 시 데이터 세팅
if st.session_state.sh is None or not st.session_state.all_data:
    try:
        load_voca_data()
    except Exception as e:
        st.error(f"구글 연동 또는 데이터 로드 실패: {e}")
        st.stop()

# --- [1. UI 스타일] ---
st.markdown("""
    <style>
        [data-testid="stSidebar"] { min-width: 320px; max-width: 320px; }
        .stButton>button { width: 100%; height: 55px; font-weight: bold; font-size: 1.1rem; border-radius: 12px;}
        .word-card { background-color: #1e1e1e; padding: 40px 20px; border-radius: 20px; border: 2px solid #4F8BF9; text-align: center; margin-bottom: 20px; box-shadow: 0 10px 20px rgba(0,0,0,0.5);}
        .word-card h1 { color: #4F8BF9; font-size: 3.5rem; margin: 0; }
        .meaning-box { color: #ffffff; font-size: 1.2rem; line-height: 1.8; text-align: left; background-color:#2a2d33; padding:20px; border-radius:15px; }
    </style>
""", unsafe_allow_html=True)

# --- [2. 사이드바: AI 추출기 & 할당량 설정] ---
with st.sidebar:
    st.title("🤖 AI 단어 추출기 V2")
    input_text = st.text_area("주제 및 본문 입력", placeholder="예) performance marketing 기사 복붙", height=150)
    
    if st.button("✨ 단어 추출 및 시트 추가", use_container_width=True):
        if not input_text.strip():
            st.warning("텍스트를 입력해주세요.")
        else:
            with st.spinner("AI가 중복을 피해 단어를 추출 중입니다..."):
                genai.configure(api_key=GEMINI_API_KEY.strip(), transport='rest')
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                exist_str = ", ".join(st.session_state.existing_words)
                prompt = f"""
                당신은 한국의 퍼포먼스 마케터이자 데이터 분석가인 '음매'님의 영어 비서입니다. 
                아래 텍스트의 길이를 스스로 판단하여 핵심 단어를 3개 ~ 10개 사이로 추출하세요.
                
                [입력 텍스트]: {input_text}
                [이미 학습 중인 단어 (절대 추출 금지)]: {exist_str}
                
                ### 지침:
                1. 한국어 뜻 기입: "품사. 뜻" 형태로 작성 (n. 명사 / v. 동사 등).
                2. 예문 기입: '음매'님의 상황(야탑-정자 통근, 마케터 직무 등)에 맞춘 초개인화된 예문 생성.
                3. 마크다운 없이 순수 JSON 배열만 출력. 칼럼명: "단어", "뜻", "예문"
                """
                
                try:
                    response = model.generate_content(prompt)
                    clean_text = re.sub(r"```[a-zA-Z]*\n", "", response.text)
                    clean_text = clean_text.replace("```", "").strip()
                    new_words = json.loads(clean_text)
                    
                    # 새 단어는 날짜 없이(빈칸) 저장하여 V2.2 신규 단어 로직에 자동으로 태웁니다!
                    sheet_data = [[w["단어"], w["뜻"], w["예문"], 0, "", 0] for w in new_words]
                    
                    st.session_state.sh.spreadsheets().values().append(
                        spreadsheetId=SPREADSHEET_ID, range="'Voca'!A1",
                        valueInputOption="RAW", body={'values': sheet_data}
                    ).execute()
                    
                    st.success(f"🎉 성공! 새로운 단어 {len(new_words)}개가 큐(Queue)에 적재되었습니다!")
                    load_voca_data()
                    st.rerun()
                except Exception as e:
                    st.error(f"추출 실패: {e}")
                    
    st.markdown("---")
    st.subheader("⚙️ 학습 트래픽 설정")
    # 사용자가 직접 슬라이더로 하루에 소화할 신규 단어 양을 조절할 수 있습니다.
    new_quota = st.slider("하루 신규 단어 할당량", min_value=5, max_value=50, value=st.session_state.daily_quota, step=5)
    if new_quota != st.session_state.daily_quota:
        st.session_state.daily_quota = new_quota
        load_voca_data() # 할당량이 바뀌면 데이터를 즉시 다시 긁어옵니다.
        st.rerun()

# --- [3. 메인 화면: DA 대시보드] ---
st.title("📱 출퇴근 갓생 단어장 (V2.2)")

total_words = len(st.session_state.all_data)
remains = len(st.session_state.todays_words) - st.session_state.current_idx

sorted_by_mistakes = sorted(
    [row for row in st.session_state.all_data if len(row) > 5 and str(row[5]).isdigit()], 
    key=lambda x: int(x[5]), reverse=True
)
worst_word = f"{sorted_by_mistakes[0][0]} ({sorted_by_mistakes[0][5]}회)" if sorted_by_mistakes and int(sorted_by_mistakes[0][5]) > 0 else "없음 갓벽!"

st.markdown("---")
c1, c2, c3 = st.columns(3)
c1.metric("📚 총 누적 단어", f"{total_words}개")
c2.metric("🔥 큐(Queue) 대기열", f"{remains if remains > 0 else 0}개")
c3.metric("👿 최다 오답 단어", worst_word)
st.markdown("---")

# --- [4. 플래시카드 및 SRS 알고리즘 로직] ---
words = st.session_state.todays_words
idx = st.session_state.current_idx

# 종료 화면 처리
if idx >= len(words) and len(words) > 0:
    st.session_state.is_finished = True

if len(words) == 0:
    st.info("🎉 오늘 복습할 단어가 없습니다! 사이드바에서 AI 단어를 추가해보세요.")
elif st.session_state.is_finished:
    if not st.session_state.saved_to_cloud:
        st.balloons()
        st.success("🎉 오늘 통근길 영단어 미션을 모두 클리어했습니다!")
        
        total_clicks = st.session_state.correct_cnt + st.session_state.wrong_cnt
        accuracy = int((st.session_state.correct_cnt / total_clicks) * 100) if total_clicks > 0 else 0
        
        st.info(f"**📊 오늘 학습 퍼포먼스 리포트**\n* 🎯 정답률(전환율): **{accuracy}%**\n* ⭕ 정답: {st.session_state.correct_cnt}회\n* ❌ 오답: {st.session_state.wrong_cnt}회")
        st.warning("⚠️ 학습 데이터가 아직 임시 메모리에 있습니다. 반드시 아래 버튼을 눌러 클라우드 시트에 저장하세요!")
        
        if st.button("💾 구글 시트에 학습 결과 최종 연동하기", type="primary", use_container_width=True):
            with st.spinner("서버에 일괄 저장 중입니다. 잠시만 대기..."):
                batch_update_to_sheet()
                st.session_state.saved_to_cloud = True
            st.rerun()
    else:
        st.success("✅ 구글 시트 연동 완료! 안심하고 앱을 끄셔도 됩니다.")
        if st.button("🔄 내일 단어 미리 당겨오기 (새로고침)", use_container_width=True):
            load_voca_data()
            st.rerun()
else:
    current_word = words[idx]
    
    st.markdown(f"""
        <div class="word-card">
            <h5 style="color:#aaa; text-align:left; margin:0;">[진도: {idx + 1} / 전체 큐: {len(words)}] | 현재 Lv.{current_word['level']}</h5>
            <br>
            <h1>{current_word['word']}</h1>
        </div>
    """, unsafe_allow_html=True)
    
    if st.button("🔊 영단어 발음 듣기", key=f"tts_w_{idx}", use_container_width=True):
        play_audio(current_word['word'])
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    if not st.session_state.show_meaning:
        if st.button("🧐 뜻과 예문 보기", key=f"show_{idx}", use_container_width=True):
            st.session_state.show_meaning = True
            st.rerun()
    else:
        st.markdown(f"""
            <div class="meaning-box">
                <b>💡 뜻:</b><br>{current_word['meaning']}<br><br>
                <b>✍️ 음매 맞춤형 예문:</b><br>{current_word['example']}
            </div>
            <br>
        """, unsafe_allow_html=True)
        
        if st.button("🔊 예문 발음 듣기", key=f"tts_ex_{idx}", use_container_width=True):
            play_audio(current_word['example'])
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        col_correct, col_wrong = st.columns(2)
        
        with col_correct:
            if st.button("⭕ 맞춤 (레벨업)", type="primary", key=f"cor_{idx}", use_container_width=True):
                new_level = current_word['level'] + 1
                days_to_add = 2 ** new_level 
                new_date = str((datetime.now() + timedelta(days=days_to_add)).date())
                
                st.session_state.pending_updates[current_word['row_idx']] = [new_level, new_date, current_word['mistakes']]
                st.session_state.correct_cnt += 1
                st.session_state.show_meaning = False
                st.session_state.current_idx += 1
                st.rerun()
                
        with col_wrong:
            if st.button("❌ 틀림 (스파르타 큐 등록)", key=f"wrg_{idx}", use_container_width=True):
                new_level = 0
                new_date = str((datetime.now() + timedelta(days=1)).date())
                new_mistakes = current_word['mistakes'] + 1
                
                st.session_state.pending_updates[current_word['row_idx']] = [new_level, new_date, new_mistakes]
                
                re_queue_word = current_word.copy()
                re_queue_word['level'] = new_level
                re_queue_word['date'] = new_date
                re_queue_word['mistakes'] = new_mistakes
                st.session_state.todays_words.append(re_queue_word)
                
                st.session_state.wrong_cnt += 1
                st.session_state.show_meaning = False
                st.session_state.current_idx += 1
                st.rerun()
