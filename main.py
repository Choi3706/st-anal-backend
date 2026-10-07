from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from datetime import datetime
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor
import translators as ts
import html
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 앱이 서버 상태를 확인하는 핑(Ping) 주소
@app.get("/api/health")
def health_check():
    return {"status": "online"}

FMP_KEY = "bqW2GNXRz0Kr1a02eNPaFNID6ASutzCU"
GNEWS_KEY = "651b77a31242ef76da2e1567a9975c7e"

def get_yf_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*"
    })
    return session

def safe_translate(text: str) -> str:
    if not text or not str(text).strip() or text == "정보 없음" or text == "N/A":
        return str(text)
    try:
        return ts.translate_text(str(text).strip(), translator='google', from_language='en', to_language='ko')
    except Exception:
        return str(text)

def extract_three_sentences(text: str) -> str:
    if not text:
        return ""
    clean_text = re.sub(r'<[^>]+>', '', html.unescape(str(text)))
    sentences = clean_text.split('. ')
    return '. '.join(sentences[:3]) + ('.' if len(sentences) >= 3 else '')

@app.get("/api/ticker/{ticker}")
def get_ticker_data(ticker: str, period: str = "3mo"):
    try:
        session = get_yf_session()
        ticker = ticker.upper()
        stock = yf.Ticker(ticker, session=session)
        
        # 1. 뼈대(차트) 데이터 먼저 시도 (가장 차단 확률이 낮음)
        try:
            hist = stock.history(period=period)
        except Exception:
            hist = pd.DataFrame()

        if hist.empty:
            return {"status": "error", "message": "없는 티커이거나 현재 통신망에서 차단되었습니다."}

        prices = hist['Close'].tolist()
        
        # 2. 야후 기업 정보 (에러 발생 시 서버가 죽지 않고 빈 딕셔너리로 패스)
        try:
            data = stock.info
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

        # 3. FMP 무료 주가/실적 API 보완 (야후가 막혔을 때를 대비한 스페어 타이어)
        fmp_quote = {}
        try:
            q_url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={FMP_KEY}"
            q_res = requests.get(q_url, timeout=3)
            if q_res.status_code == 200:
                q_json = q_res.json()
                if isinstance(q_json, list) and len(q_json) > 0:
                    fmp_quote = q_json[0]
        except Exception:
            pass

        krw_rate = 1350.0
        try:
            krw_ticker = yf.Ticker("KRW=X", session=session)
            krw_info = krw_ticker.info
            if isinstance(krw_info, dict):
                krw_rate = krw_info.get("regularMarketPrice", 1350.0)
        except:
            pass
        
        # 기술적 지표 연산 (오류 원천 차단)
        ma50, ma200, upper_band, lower_band, macd_histogram = [], [], [], [], []
        current_rsi = "N/A"
        
        try:
            if len(hist) >= 50:
                ma50 = hist['Close'].rolling(window=50).mean().fillna(0).tolist()
            if len(hist) >= 200:
                ma200 = hist['Close'].rolling(window=200).mean().fillna(0).tolist()
            if len(hist) >= 20:
                ma20 = hist['Close'].rolling(window=20).mean()
                std20 = hist['Close'].rolling(window=20).std()
                upper_band = (ma20 + (std20 * 2)).fillna(0).tolist()
                lower_band = (ma20 - (std20 * 2)).fillna(0).tolist()
            if len(hist) >= 26:
                ema12 = hist['Close'].ewm(span=12, adjust=False).mean()
                ema26 = hist['Close'].ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_histogram = (macd_line - signal_line).fillna(0).tolist()
            if len(hist) >= 14:
                delta = hist['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi_series = 100 - (100 / (1 + rs))
                if not pd.isna(rsi_series.iloc[-1]):
                    current_rsi = round(rsi_series.iloc[-1], 2)
        except:
            pass
        
        # 데이터 병합 (FMP를 우선시하고 없으면 야후 데이터 사용)
        current_price = fmp_quote.get("price", data.get("currentPrice", data.get("regularMarketPrice", "N/A")))
        raw_pe = fmp_quote.get("pe", data.get("forwardPE", "N/A"))
        formatted_pe = round(raw_pe, 2) if isinstance(raw_pe, (int, float)) else "N/A"
        
        raw_pbr = data.get("priceToBook", "N/A")
        formatted_pbr = round(raw_pbr, 2) if isinstance(raw_pbr, (int, float)) else "N/A"
        raw_psr = data.get("priceToSalesTrailing12Months", "N/A")
        formatted_psr = round(raw_psr, 2) if isinstance(raw_psr, (int, float)) else "N/A"

        sector = data.get("sector", "N/A")
        industry = data.get("industry", "N/A")
        
        sector_ko_map = {
            "Technology": "기술 (IT)", "Financial Services": "금융", "Healthcare": "헬스케어", 
            "Consumer Cyclical": "자유소비재", "Industrials": "산업재", "Communication Services": "통신", 
            "Consumer Defensive": "필수소비재", "Energy": "에너지", "Basic Materials": "소재", 
            "Real Estate": "부동산", "Utilities": "유틸리티"
        }
        sector_ko = sector_ko_map.get(sector, sector)
        industry_ko = safe_translate(industry) if industry != "N/A" else "N/A"

        health_eval = "데이터 부족으로 진단 불가"
        if isinstance(raw_pe, (int, float)):
            if raw_pe < 0: health_eval = "현재 적자 상태 (펀더멘털 주의)"
            else:
                if sector in ["Technology", "Healthcare", "Communication Services"]:
                    if raw_pe <= 25: health_eval = "성장주 업계 평균 대비 저평가 (건전)"
                    elif raw_pe <= 40: health_eval = "성장주 업계 평균 수준 (보통)"
                    else: health_eval = "성장주 업계 대비 고평가 (과열 의심)"
                else:
                    if raw_pe <= 15: health_eval = "시장 평균 대비 저평가 (건전)"
                    elif raw_pe <= 25: health_eval = "시장 평균 수준 (보통)"
                    else: health_eval = "시장 평균 대비 고평가 (과열 의심)"

        def format_price(val):
            return round(val, 2) if isinstance(val, (int, float)) else "N/A"
            
        target_mean = format_price(data.get("targetMeanPrice", "N/A"))
        target_high = format_price(data.get("targetHighPrice", "N/A"))
        target_low = format_price(data.get("targetLowPrice", "N/A"))
        recommendation = data.get("recommendationKey", "N/A")
        if recommendation != "N/A":
            rec_map = {"buy": "매수", "strong_buy": "강력 매수", "hold": "보유", "sell": "매도", "strong_sell": "강력 매도"}
            recommendation = rec_map.get(recommendation.lower(), recommendation.upper())

        # 실적 발표일 처리
        next_earnings = fmp_quote.get("earningsAnnouncement", "미정")
        if next_earnings and next_earnings != "미정":
            next_earnings = next_earnings.split('T')[0]
            
        recent_earnings = "미정"
        try:
            earnings = stock.get_earnings_dates(limit=10)
            if earnings is not None and not earnings.empty:
                now = pd.Timestamp.now(tz=earnings.index.tz)
                past_dates = earnings[earnings.index < now]
                if not past_dates.empty: 
                    recent_earnings = past_dates.index[0].strftime('%Y-%m-%d')
        except:
            pass

        # 기업 개요 3문장 파싱
        desc_full = data.get("longBusinessSummary", "N/A")
        if desc_full != "N/A":
            summary_en = extract_three_sentences(desc_full)
            summary_ko = safe_translate(summary_en)
        else:
            summary_en = "데이터 센터 접근 차단으로 기업 개요를 불러올 수 없습니다."
            summary_ko = summary_en

        # 뉴스 파싱 (GNews 3문장 요약)
        news_data_list = []
        try:
            gnews_url = f"https://gnews.io/api/v4/search?q={ticker} stock&lang=en&country=us&max=7&apikey={GNEWS_KEY}"
            res = requests.get(gnews_url, timeout=5)
            if res.status_code == 200:
                articles = res.json().get("articles", [])
                for a in articles:
                    title_en = a.get("title", "제목 없음")
                    desc_en = a.get("content", a.get("description", ""))
                    desc_en_3lines = extract_three_sentences(desc_en)
                    title_ko = safe_translate(title_en)
                    desc_ko = safe_translate(desc_en_3lines) if desc_en_3lines else "본문 요약이 없습니다."
                    
                    news_data_list.append({
                        "title_en": title_en, "title_ko": title_ko,
                        "publisher": a.get("source", {}).get("name", "GNews"),
                        "link": a.get("url", ""),
                        "summary_en": desc_en_3lines, "summary_ko": desc_ko
                    })
        except:
            pass

        return {
            "status": "success",
            "ticker": ticker,
            "current_price": current_price, 
            "forward_pe": formatted_pe,
            "pbr": formatted_pbr,
            "psr": formatted_psr,
            "sector_ko": sector_ko, 
            "industry_ko": industry_ko, 
            "health_eval": health_eval, 
            "target_mean": target_mean,
            "target_high": target_high,
            "target_low": target_low,
            "recommendation": recommendation,
            "recent_earnings": recent_earnings,
            "next_earnings": next_earnings,
            "current_rsi": current_rsi,
            "short_ratio": "N/A", 
            "held_by_institutions": "N/A", 
            "business_summary_en": summary_en,
            "business_summary_ko": summary_ko, 
            "exchange_rate": round(krw_rate, 2), 
            "chart_prices": prices,
            "ma50": ma50,    
            "ma200": ma200,
            "upper_band": upper_band, 
            "lower_band": lower_band, 
            "macd_histogram": macd_histogram,
            "financials": [],
            "news": news_data_list,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": "앱 내부 데이터 변환 중 오류가 발생했습니다."}

@app.get("/api/macro")
def get_macro_data(sector_query: str = "economy"):
    try:
        session = get_yf_session()
        macro_indicators = {}
        history_data = {"us10y": [], "vix": [], "krw": []}
        
        try:
            tnx_ticker = yf.Ticker("^TNX", session=session)
            vix_ticker = yf.Ticker("^VIX", session=session)
            krw_ticker = yf.Ticker("KRW=X", session=session)
            
            tnx_info = tnx_ticker.info if isinstance(tnx_ticker.info, dict) else {}
            vix_info = vix_ticker.info if isinstance(vix_ticker.info, dict) else {}
            krw_info = krw_ticker.info if isinstance(krw_ticker.info, dict) else {}
            
            tnx = tnx_info.get("regularMarketPrice", 0.0)
            vix = vix_info.get("regularMarketPrice", 0.0)
            krw = krw_info.get("regularMarketPrice", 1350.0)
            
            history_data["us10y"] = tnx_ticker.history(period="6mo")['Close'].tolist() if not tnx_ticker.history(period="6mo").empty else []
            history_data["vix"] = vix_ticker.history(period="6mo")['Close'].tolist() if not vix_ticker.history(period="6mo").empty else []
            history_data["krw"] = krw_ticker.history(period="6mo")['Close'].tolist() if not krw_ticker.history(period="6mo").empty else []

            macro_indicators = {
                "us10y_yield": round(tnx, 2) if tnx else 4.25,
                "vix": round(vix, 2) if vix else 16.5,
                "exchange_rate": round(krw, 2),
                "market_sentiment": "과열 (탐욕)" if vix < 15 else ("안정" if vix < 20 else ("경계 (공포)" if vix < 30 else "극심한 공포"))
            }
        except:
            macro_indicators = {"us10y_yield": 4.25, "vix": 16.5, "exchange_rate": 1350.0, "market_sentiment": "안정"}

        sector_topics = {
            "economy": "economy OR interest rate",
            "fed_wallstreet": "Federal Reserve OR Wall Street",
            "geopolitics": "geopolitics OR crude oil",
            "science_tech": "artificial intelligence OR technology",
            "society": "employment OR housing",
            "politics": "US election OR Congress"
        }
        
        search_term = sector_topics.get(sector_query, "economy")
        news_data_list = []
        
        try:
            gnews_url = f"https://gnews.io/api/v4/search?q={search_term}&lang=en&country=us&max=6&apikey={GNEWS_KEY}"
            res = requests.get(gnews_url, timeout=5)
            if res.status_code == 200:
                articles = res.json().get("articles", [])
                for a in articles:
                    title_en = a.get("title", "제목 없음")
                    desc_en = a.get("content", a.get("description", ""))
                    desc_en_3lines = extract_three_sentences(desc_en)
                    title_ko = safe_translate(title_en)
                    desc_ko = safe_translate(desc_en_3lines) if desc_en_3lines else "본문 요약이 없습니다."
                    
                    news_data_list.append({
                        "title_en": title_en, "title_ko": title_ko,
                        "publisher": a.get("source", {}).get("name", "GNews"),
                        "link": a.get("url", ""),
                        "summary_en": desc_en_3lines, "summary_ko": desc_ko
                    })
        except:
            pass

        return {
            "status": "success",
            "indicators": macro_indicators,
            "history": history_data,
            "news": news_data_list,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": "매크로 데이터를 불러올 수 없습니다."}

@app.get("/api/gurus")
def get_gurus_data():
    return {
        "status": "success",
        "disclaimer": "해당 데이터는 프로토타입 구현을 위한 최근 13F 공시 기준 하드코딩 데이터입니다.",
        "gurus": [
            {
                "name": "워런 버핏 (Berkshire Hathaway)",
                "summary": "방어적 태세 유지. 주력 포트폴리오 비중 축소 및 단기 국채 매입에 집중.",
                "top_holdings": [{"company": "Apple", "ticker": "AAPL", "percent": 28.5}]
            }
        ]
    }

@app.get("/api/calendar")
def get_calendar(tickers: str = ""):
    return {"status": "success", "calendar": []}
