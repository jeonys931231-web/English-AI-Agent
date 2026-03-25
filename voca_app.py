import streamlit as st
import google.generativeai as genai
import json
from datetime import datetime
from gtts import gTTS
from io import BytesIO
from google.oauth2 import service_account
from googleapiclient.discovery import build

# [필수] 페이지 설정 (모바일 최적화)
st.set_page_config(page_title="출퇴근 영어 전용 무기", layout="centered")

# --- [0. 초기화 및 서비스 연동] ---
if 'sh' not in st.session_state: st.session_state.sh = None
if 'todays_words' not in st.session_state: st.session_state.todays_words = []
if 'current_idx' not in st.session_state: st.session_state.current_idx = 0
if 'show_meaning' not in st.session_state: st.session_state.show_meaning = False

SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

def auto_google_sync():
    """구글 스프레드시트 연동 (Service Account)"""
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return build('sheets', 'v4', credentials=creds)

def play_audio(text, lang='en'):
    """gTTS를 이용한 무료 발음 듣기 구현"""
    tts = gTTS(text=text, lang=lang)
    mp3_fp = BytesIO()
    tts.write_to_fp(mp3_fp)
    st.audio(mp3_fp, format='audio/mp3')

# --- [1. UI 스타일: 모바일 플래시카드 디자인] ---
st.markdown("""
    <style>
        [data-testid="stSidebar"] { min-width: 300px; max-width: 300px; }
        .stButton>button { width: 100%; height: 50px; font-weight: bold; font-size: 1rem; border-radius: 10px;}
        .word-card { background-color: #1e1e1e; padding: 30px; border-radius: 20px; border: 2px solid #4F8BF9; text-align: center; margin-bottom: 20px; }
        .word-card h1 { color: #4F8BF9; font-size: 3rem; margin: 0; }
        .meaning-box { color: #ffffff; font-size: 1.2rem; line-height: 1.8; text-align: left; }
    </style>
""", unsafe_allow_html=True)

# --- [2. 사이드바: AI 단어 무한 복사기 (전략 1 구현)] ---
with st.sidebar:
    st.title("🤖 AI 단어 복사기")
    input_text = st.text_area("주제(마케팅 등) 혹은 학습용 문장 입력", placeholder="예) performance marketing, 통근 중 영어 공부법에 대한 기사 본문", height=100)
    
    if st.button("✨ AI 영단어 추출 및 시트 추가", use_container_width=True):
        if not input_text.strip():
            st.warning("내용을 입력해주세요.")
        else:
            with st.spinner("AI가 음매님 맞춤형 단어/예문을 생성 중입니다..."):
                genai.configure(api_key=GEMINI_API_KEY.strip(), transport='rest')
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                # ⭐ 세밀하게 깎은 프롬프트 (요청하신 규칙 및 context 반영)
                prompt = f"""
                당신은 한국의 퍼포먼스 마케터이자 데이터 분석가인 '음매'님의 영어 비서입니다. 
                아래 입력 텍스트에서 학습할 만한 핵심 단어 3개를 추출하고 JSON 형태로 답변하세요.
                
                [입력 텍스트]: {input_text}
                
                ### 지침 (엄수):
                1. 한국어 뜻 기입 규칙: 품사 약어를 활용해 "품사. 뜻" 형태로 작성. 명사=n. 통근 / 동사=v. 통근하다 / 형용사=a. 통근의 등
                2. 예문 기입 규칙: '음매'님의 상황에 맞춘 초개인화된 예문 생성. (성남 거주, 야탑-정자 지하철 통근, 마케터 직무, 데이터 분석 context 활용)
                3. JSON 형태 답변: Markdown 태그 없이 순수 JSON만 출력. 칼럼명: "단어", "뜻", "예문"
                
                ### JSON 예시:
                [
                    {{"단어": "commute", "뜻": "n. 통근 / v. 통근하다", "예문": "I use my g-sang engine app every day while commuting between Yatab and Jeongja."}},
                    {{"단어": "optimization", "뜻": "n. 최적화", "예문": "As a marketer, my goal is the optimization of my owned media performance."}}
                ]
                """
                
                try:
                    response = model.generate_content(prompt)
                    new_words = json.loads(response.text.strip())
                    
                    if st.session_state.sh is None:
                        st.session_state.sh = auto_google_sync()
                        
                    # 시트에 추가할 데이터 준비 (학습단계=0, 복습일=오늘, 오답수=0)
                    today_str = str(datetime.now().date())
                    sheet_data = [[w["단어"], w["뜻"], w["예문"], 0, today_str, 0] for w in new_words]
                    
                    # 스프레드시트에 Append
                    st.session_state.sh.spreadsheets().values().append(
                        spreadsheetId=SPREADSHEET_ID, range="'Voca'!A1",
                        valueInputOption="RAW", body={'values': sheet_data}
                    ).execute()
                    
                    st.success(f"🎉 '{input_text[:10]}...' 관련 단어 {len(new_words)}개가 시트에 자동 추가되었습니다!")
                    st.balloons()
                    
                except Exception as e:
                    st.error(f"AI 생성 또는 시트 추가 실패: {e}")

# --- [3. 메인 화면: 오늘 학습할 플래시카드 (gTTS 발음 포함)] ---
st.title("📱 출퇴근 갓생 단어장 (V1.0)")

# 구글 시트 연동 실패 시 재시도
if st.session_state.sh is None:
    try:
        st.session_state.sh = auto_google_sync()
    except Exception as e:
        st.error(f"구글 연동 실패: {e}")
        st.stop()

# 오늘 날짜 단어 가져오기 함수
def fetch_todays_words():
    try:
        result = st.session_state.sh.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range="'Voca'!A2:F1000").execute()
        all_data = result.get('values', [])
        today_str = str(datetime.now().date())
        # Column E(인덱스 4)가 오늘 복습일인 것만 필터링
        return [row for row in all_data if len(row) > 4 and str(row[4]) == today_str]
    except: return []

# 처음 앱 켤 때만 데이터 가져오기
if not st.session_state.todays_words and st.session_state.sh:
    st.session_state.todays_words = fetch_todays_words()

words = st.session_state.todays_words
idx = st.session_state.current_idx

if not words:
    st.info("🎉 오늘 복습할 단어가 없습니다! 야탑-정자 통근길을 가볍게 즐기시거나, 사이드바에서 AI 단어를 추가해보세요.")
else:
    # 플래시카드 UI
    st.write(f"[오늘의 진도: {idx + 1} / {len(words)}]")
    current_word = words[idx]
    
    with st.container():
        st.markdown(f"""
            <div class="word-card">
                <h1>{current_word[0]}</h1>
            </div>
        """, unsafe_allow_html=True)
        
        # 🔊 발음 듣기 버튼 (단어)
        if st.button("🔊 영단어 발음 듣기", key=f"tts_word_{idx}", use_container_width=True):
            play_audio(current_word[0])

    st.markdown("---")
    
    # 뜻 가리기/보기 로직
    if not st.session_state.show_meaning:
        if st.button("🧐 뜻과 예문 보기", key=f"show_{idx}", use_container_width=True):
            st.session_state.show_meaning = True
            st.rerun()
    else:
        # 뜻(규칙 준수) 및 예문(음매 맞춤형) 표시
        st.markdown(f"""
            <div class="meaning-box">
                <b>💡 뜻 (N./V./A.):</b><br>{current_word[1]}<br><br>
                <b>✍️ 음매 맞춤형 예문:</b><br>{current_word[2]}
            </div>
        """, unsafe_allow_html=True)
        
        # 🔊 발음 듣기 버튼 (예문)
        if st.button("🔊 예문 전체 발음 듣기", key=f"tts_ex_{idx}", use_container_width=True):
            play_audio(current_word[2])
            
        st.markdown("---")
        
        # 학습 버튼 (V2.0에서 알고리즘 구현 예정)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⭕ 맞춤", key=f"correct_{idx}", use_container_width=True):
                # (V2.0 예고: 시트의 학습단계 및 오답수 업데이트 로직 추가)
                st.session_state.current_idx = (idx + 1) % len(words)
                st.session_state.show_meaning = False
                st.rerun()
        with c2:
            if st.button("❌ 틀림", key=f"incorrect_{idx}", use_container_width=True):
                # (V2.0 예고: 다음 복습일을 오늘로 초기화하는 로직 추가)
                st.session_state.current_idx = (idx + 1) % len(words)
                st.session_state.show_meaning = False
                st.rerun()