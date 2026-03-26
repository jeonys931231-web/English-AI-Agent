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
st.set_page_config(page_title="출퇴근 갓생 단어장 V2.0", layout="centered")

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
    todays = []
    existing = []
    
    for i, row in enumerate(rows):
        row += [''] * (6 - len(row)) # 빈칸 방어 (6개 칼럼 꽉 채우기)
        word = str(row[0]).strip()
        if word: existing.append(word.lower())
        
        # [핵심] 복습일이 오늘이거나, 오늘보다 과거(밀린 단어)인 경우 모두 호출
        if row[4] and str(row[4]) <= today_str:
            todays.append({
                "row_idx": i + 2, # 시트 내 실제 행 번호 (A2부터 시작하므로 인덱스+2)
                "word": word,
                "meaning": row[1],
                "example": row[2],
                "level": int(row[3]) if str(row[3]).isdigit() else 0,
                "date": row[4],
                "mistakes": int(row[5]) if str(row[5]).isdigit() else 0
            })
            
    st.session_state.all_data = rows
    st.session_state.existing_words = existing
    st.session_state.todays_words = todays
    st.session_state.current_idx = 0
    st.session_state.is_finished = False

def update_sheet_word(row_idx, new_level, new_date, new_mistakes):
    """(우선순위 1) 변경된 학습 데이터를 실제 구글 시트에 덮어씁니다."""
    body = {"values": [[new_level, new_date, new_mistakes]]}
    st.session_state.sh.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"'Voca'!D{row_idx}:F{row_idx}",
        valueInputOption="RAW", body=body
    ).execute()

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

# --- [2. 사이드바: AI 똑똑한 단어 복사기 (우선순위 3 적용)] ---
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
                
                # 기존 단어 목록 문자열화 (중복 방지용)
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


'''
### ✨ V2.0에서 꼭 확인하셔야 할 포인트!
1. **버튼 누르고 시트 확인해 보기:** 앱에서 단어 하나를 ⭕(맞춤)이나 ❌(틀림)으로 넘긴 직후, **실제 구글 스프레드시트 창을 열어보세요.** E열(다음 복습일)과 F열(오답수)의 데이터가 실시간으로 샤라락 바뀌는 짜릿한 경험을 하실 수 있습니다!
2. **AI 단어 추출 탄력성 확인:** 사이드바에 짧은 글을 넣으면 3개, 긴 영어 기사 본문을 통째로 복붙하면 알아서 7~10개 정도를 유동적으로 뽑아냅니다. 

깃허브에 코드를 업데이트하시고 스트림릿을 새로고침 하시면 V2.0 대시보드가 뜰 겁니다. 드디어 지하철에서 완벽하게 사용할 무기가 완성되었으니, 직접 한번 눌러보시고 피드백을 들려주십시오! 🚀
'''
