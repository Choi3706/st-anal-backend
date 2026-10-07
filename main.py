from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

@app.get("/api/health")
def health_check():
    return {"status": "online"}

FMP_KEY = "bqW2GNXRz0Kr1a02eNPaFNID6ASutzCU"
GNEWS_KEY = "651b77a31242ef76da2e1567a9975c7e"

def fetch_json(url: str):
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return None

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
        ticker = ticker.upper()
        
        # 1. 야후를 버리고 FMP API로 현재가 및 정보 즉시 호출
        quote_url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={FMP_KEY}"
        quote_data = fetch_json(quote_url)
        
        if not quote_data or len(quote_data) == 0:
            return {"status": "error", "message": "없는 티커이거나 상장 폐지된 종목입니다."}
            
        quote = quote_data[0]
        current_price = quote.get("price", "N/A")
        raw_pe = quote.get("pe")
        formatted_pe = round(raw_pe, 2) if isinstance(raw_pe, (int, float)) else "N/A"
        
        # 실적 발표일 파싱
        next_earnings = quote.get("earningsAnnouncement", "미정")
        if next_earnings and next_earnings != "미정":
            next_earnings = next_earnings.split('T')[0]

        # 2. 야후를 버리고 FMP API로 1년치 차트 역사적 데이터 호출
        hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}?timeseries=300&apikey={FMP_KEY}"
        hist_data_res = fetch_json(hist_url)
        
        prices, ma50, ma200, upper_band, lower_band, macd_histogram = [], [], [], [], [], []
        current_rsi = "N/A"
        
        if hist_data_res and "historical" in hist_data_res:
            # FMP는 최신 날짜가 맨 앞이므로, 차트를 위해 역순(과거->최신)으로 뒤집음
            hist_list = hist_data_res["historical"][::-1]
            df = pd.DataFrame(hist_list)
            
            slice_map = {'1d': 1, '5d': 5, '3mo': 63, '1y': 252, '5y': 1260}
            limit = slice_map.get(period, 63)
            
            prices_full = df['close']
            
            if len(prices_full) >= 50:
                df['ma50'] = prices_full.rolling(window=50).mean().fillna(0)
            if len(prices_full) >= 200:
                df['ma200'] = prices_full.rolling(window=200).mean().fillna(0)
            if len(prices_full) >= 20:
                df['ma20'] = prices_full.rolling(window=20).mean()
                df['std20'] = prices_full.rolling(window=20).std()
                df['upper'] = (df['ma20'] + (df['std20'] * 2)).fillna(0)
                df['lower'] = (df['ma20'] - (df['std20'] * 2)).fillna(0)
            if len(prices_full) >= 26:
                ema12 = prices_full.ewm(span=12, adjust=False).mean()
                ema26 = prices_full.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                df['macd_hist'] = (macd_line - signal_line).fillna(0)
            if len(prices_full) >= 14:
                delta = prices_full.diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi_series = 100 - (100 / (1 + rs))
                if not pd.isna(rsi_series.iloc[-1]):
                    current_rsi = round(rsi_series.iloc[-1], 2)
            
            df_sliced = df.tail(limit)
            prices = df_sliced['close'].tolist()
            if 'ma50' in df.columns: ma50 = df_sliced['ma50'].tolist()
            if 'ma200' in df.columns: ma200 = df_sliced['ma200'].tolist()
            if 'upper' in df.columns: upper_band = df_sliced['upper'].tolist()
            if 'lower' in df.columns: lower_band = df_sliced['lower'].tolist()
            if 'macd_hist' in df.columns: macd_histogram = df_sliced['macd_hist'].tolist()

        # 원/달러 환율 (FMP USDKRW)
        krw_rate = 1350.0
        krw_res = fetch_json(f"https://financialmodelingprep.com/api/v3/quote/USDKRW?apikey={FMP_KEY}")
        if krw_res and len(krw_res) > 0:
            krw_rate = krw_res[0].get("price", 1350.0)

        # FMP 프로필이 막혔으므로, 빈 값 처리
        summary_en = "무료 API 한계로 기업 개요 텍스트는 제공되지 않습니다."
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
            "pbr": "N/A", "psr": "N/A", "sector_ko": "주식", "industry_ko": "주식", 
            "health_eval": "데이터 부족으로 진단 불가", 
            "target_mean": "N/A", "target_high": "N/A", "target_low": "N/A", "recommendation": "N/A",
            "recent_earnings": "미정",
            "next_earnings": next_earnings,
            "current_rsi": current_rsi,
            "short_ratio": "N/A", "held_by_institutions": "N/A", 
            "business_summary_en": summary_en,
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
        return {"status": "error", "message": "앱 내부 데이터 변환 중 오류가 발생했습니다."}

@app.get("/api/macro")
def get_macro_data(sector_query: str = "economy"):
    try:
        macro_indicators = {"us10y_yield": 4.25, "vix": 16.5, "exchange_rate": 1350.0, "market_sentiment": "안정"}
        history_data = {"us10y": [], "vix": [], "krw": []}
        
        # 3. 매크로 차트도 야후를 버리고 FMP API로 연결 복구
        def get_fmp_close(symbol):
            url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{symbol}?timeseries=130&apikey={FMP_KEY}"
            res = fetch_json(url)
            if res and "historical" in res:
                return [day["close"] for day in res["historical"][::-1]]
            return []

        try:
            history_data["us10y"] = get_fmp_close("^TNX")
            history_data["vix"] = get_fmp_close("^VIX")
            history_data["krw"] = get_fmp_close("USDKRW")
            
            if history_data["us10y"]: macro_indicators["us10y_yield"] = round(history_data["us10y"][-1], 2)
            if history_data["vix"]: macro_indicators["vix"] = round(history_data["vix"][-1], 2)
            if history_data["krw"]: macro_indicators["exchange_rate"] = round(history_data["krw"][-1], 2)
            
            vix_val = macro_indicators["vix"]
            macro_indicators["market_sentiment"] = "과열 (탐욕)" if vix_val < 15 else ("안정" if vix_val < 20 else ("경계 (공포)" if vix_val < 30 else "극심한 공포"))
        except:
            pass

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
