from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import pandas as pd
import requests
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

@app.get("/api/health")
def health_check():
    return {"status": "online"}

GNEWS_KEY = "651b77a31242ef76da2e1567a9975c7e"

# 야후 파이낸스 라이브러리(yfinance)를 버리고, 서버 직통 통신으로 우회하는 함수
def get_yahoo_direct_quote(ticker):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()
        result = data.get('quoteResponse', {}).get('result', [])
        if result:
            return result[0]
    except:
        pass
    return {}

def get_yahoo_direct_chart(ticker, period="3mo"):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    range_map = {'1d': '1d', '5d': '5d', '3mo': '3mo', '1y': '1y', '5y': '5y'}
    interval_map = {'1d': '5m', '5d': '15m', '3mo': '1d', '1y': '1d', '5y': '1wk'}
    r = range_map.get(period, '3mo')
    i = interval_map.get(period, '1d')
    
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range={r}&interval={i}"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()
        closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
        return [c for c in closes if c is not None]
    except:
        return []

def extract_three_sentences(text: str) -> str:
    if not text:
        return ""
    clean_text = re.sub(r'<[^>]+>', '', html.unescape(str(text)))
    sentences = clean_text.split('. ')
    return '. '.join(sentences[:3]) + ('.' if len(sentences) >= 3 else '')

@app.get("/api/ticker/{ticker}")
def get_ticker_data(ticker: str, period: str = "3mo"):
    try:
        ticker = ticker.upper()
        
        # 1. 주가 정보 직통 호출
        quote = get_yahoo_direct_quote(ticker)
        if not quote:
            return {"status": "error", "message": "없는 티커이거나 상장 폐지된 종목입니다."}
            
        current_price = quote.get("regularMarketPrice", "N/A")
        raw_pe = quote.get("forwardPE", "N/A")
        formatted_pe = round(raw_pe, 2) if isinstance(raw_pe, (int, float)) else "N/A"
        raw_pbr = quote.get("priceToBook", "N/A")
        formatted_pbr = round(raw_pbr, 2) if isinstance(raw_pbr, (int, float)) else "N/A"
        
        # 2. 차트 데이터 직통 호출
        prices = get_yahoo_direct_chart(ticker, period)
        
        ma50, ma200, upper_band, lower_band, macd_histogram = [], [], [], [], []
        current_rsi = "N/A"
        
        if prices:
            df = pd.DataFrame({'Close': prices})
            
            if len(prices) >= 50:
                ma50 = df['Close'].rolling(window=50).mean().fillna(0).tolist()
            if len(prices) >= 200:
                ma200 = df['Close'].rolling(window=200).mean().fillna(0).tolist()
            if len(prices) >= 20:
                ma20 = df['Close'].rolling(window=20).mean()
                std20 = df['Close'].rolling(window=20).std()
                upper_band = (ma20 + (std20 * 2)).fillna(0).tolist()
                lower_band = (ma20 - (std20 * 2)).fillna(0).tolist()
            if len(prices) >= 26:
                ema12 = df['Close'].ewm(span=12, adjust=False).mean()
                ema26 = df['Close'].ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_histogram = (macd_line - signal_line).fillna(0).tolist()
            if len(prices) >= 14:
                delta = df['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi_series = 100 - (100 / (1 + rs))
                if not pd.isna(rsi_series.iloc[-1]):
                    current_rsi = round(rsi_series.iloc[-1], 2)

        # 환율 가져오기
        krw_quote = get_yahoo_direct_quote("KRW=X")
        krw_rate = krw_quote.get("regularMarketPrice", 1350.0) if krw_quote else 1350.0

        # 기업 개요 (사용자 요청에 따라 임시 스킵)
        summary_ko = "안정적인 서비스 제공을 위해 기업 개요 기능은 임시 점검 중입니다."

        # 관련 뉴스 파싱 (GNews)
        news_data_list = []
        try:
            gnews_url = f"https://gnews.io/api/v4/search?q={ticker} stock&lang=en&country=us&max=5&apikey={GNEWS_KEY}"
            res = requests.get(gnews_url, timeout=5)
            if res.status_code == 200:
                articles = res.json().get("articles", [])
                for a in articles:
                    title_en = a.get("title", "제목 없음")
                    desc_en = a.get("content", a.get("description", ""))
                    desc_en_3lines = extract_three_sentences(desc_en)
                    
                    news_data_list.append({
                        "title_en": title_en, "title_ko": title_en, # 속도를 위해 번역 임시 제외
                        "publisher": a.get("source", {}).get("name", "GNews"),
                        "link": a.get("url", ""),
                        "summary_en": desc_en_3lines, "summary_ko": desc_en_3lines
                    })
        except:
            pass

        return {
            "status": "success",
            "ticker": ticker,
            "current_price": current_price, 
            "forward_pe": formatted_pe,
            "pbr": formatted_pbr,
            "psr": "N/A", "sector_ko": "기업", "industry_ko": "주식", 
            "health_eval": "적정", 
            "target_mean": "N/A", "target_high": "N/A", "target_low": "N/A", "recommendation": "N/A",
            "recent_earnings": "미정", "next_earnings": "미정",
            "current_rsi": current_rsi,
            "short_ratio": "N/A", "held_by_institutions": "N/A", 
            "business_summary_en": summary_ko,
            "business_summary_ko": summary_ko, 
            "exchange_rate": round(krw_rate, 2), 
            "chart_prices": prices,
            "ma50": ma50, "ma200": ma200, "upper_band": upper_band, "lower_band": lower_band, 
            "macd_histogram": macd_histogram,
            "financials": [],
            "news": news_data_list,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": "데이터 처리 중 오류 발생"}

@app.get("/api/macro")
def get_macro_data(sector_query: str = "economy"):
    try:
        macro_indicators = {"us10y_yield": 4.25, "vix": 16.5, "exchange_rate": 1350.0, "market_sentiment": "안정"}
        history_data = {"us10y": [], "vix": [], "krw": []}
        
        history_data["us10y"] = get_yahoo_direct_chart("^TNX", "6mo")
        history_data["vix"] = get_yahoo_direct_chart("^VIX", "6mo")
        history_data["krw"] = get_yahoo_direct_chart("USDKRW", "6mo")
        
        if history_data["us10y"]: macro_indicators["us10y_yield"] = round(history_data["us10y"][-1], 2)
        if history_data["vix"]: macro_indicators["vix"] = round(history_data["vix"][-1], 2)
        if history_data["krw"]: macro_indicators["exchange_rate"] = round(history_data["krw"][-1], 2)

        return {
            "status": "success",
            "indicators": macro_indicators,
            "history": history_data,
            "news": [],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": "매크로 데이터를 불러올 수 없습니다."}

@app.get("/api/gurus")
def get_gurus_data():
    return {"status": "success", "disclaimer": "임시 하드코딩", "gurus": []}

@app.get("/api/calendar")
def get_calendar(tickers: str = ""):
    return {"status": "success", "calendar": []}
