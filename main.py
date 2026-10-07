from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from datetime import datetime
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor
import translators as ts

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FMP_KEY = "bqW2GNXRz0Kr1a02eNPaFNID6ASutzCU"
GNEWS_KEY = "651b77a31242ef76da2e1567a9975c7e"

def safe_translate(text: str) -> str:
    if not text or not text.strip() or text == "정보 없음" or text == "N/A":
        return text
    try:
        return ts.translate_text(text.strip(), translator='google', from_language='en', to_language='ko')
    except Exception:
        return text

def fetch_json(url: str):
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return None

def extract_three_sentences(text: str) -> str:
    if not text:
        return ""
    sentences = text.split('. ')
    return '. '.join(sentences[:3]) + ('.' if len(sentences) >= 3 else '')

# 🔥 API 쓰레기값 방어용 안전 추출 함수 추가
def get_first_item(data_item):
    if isinstance(data_item, list) and len(data_item) > 0:
        return data_item[0]
    return {}

@app.get("/api/ticker/{ticker}")
def get_ticker_data(ticker: str, period: str = "3mo"):
    try:
        ticker = ticker.upper()
        
        urls = {
            "profile": f"https://financialmodelingprep.com/api/v3/profile/{ticker}?apikey={FMP_KEY}",
            "quote": f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={FMP_KEY}",
            "metrics": f"https://financialmodelingprep.com/api/v3/key-metrics-ttm/{ticker}?apikey={FMP_KEY}",
            "income": f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}?period=quarter&limit=8&apikey={FMP_KEY}",
            "history": f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}?timeseries=300&apikey={FMP_KEY}",
            "target": f"https://financialmodelingprep.com/api/v4/price-target-consensus?symbol={ticker}&apikey={FMP_KEY}",
            "krw": f"https://financialmodelingprep.com/api/v3/quote/USDKRW?apikey={FMP_KEY}",
            "news": f"https://gnews.io/api/v4/search?q={ticker} stock&lang=en&country=us&max=7&apikey={GNEWS_KEY}"
        }

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(fetch_json, urls.values()))
        
        data = dict(zip(urls.keys(), results))
        
        # 🔥 예외 처리 강화: 쓰레기값(dict)이 오면 빈 딕셔너리 리턴
        profile = get_first_item(data.get("profile"))
        if not profile or "symbol" not in profile:
            return {"status": "error", "message": "티커를 찾을 수 없거나 일일 무료 API 한도를 초과했습니다."}
            
        quote = get_first_item(data.get("quote"))
        metrics = get_first_item(data.get("metrics"))
        targets = get_first_item(data.get("target"))
        krw_data = get_first_item(data.get("krw"))
        
        current_price = quote.get("price", "N/A")
        exchange_rate = krw_data.get("price", 1350.0)
        
        raw_pe = quote.get("pe")
        formatted_pe = round(raw_pe, 2) if isinstance(raw_pe, (int, float)) else "N/A"
        
        raw_pbr = metrics.get("pbRatioTTM")
        formatted_pbr = round(raw_pbr, 2) if isinstance(raw_pbr, (int, float)) else "N/A"
        
        raw_psr = metrics.get("priceToSalesRatioTTM")
        formatted_psr = round(raw_psr, 2) if isinstance(raw_psr, (int, float)) else "N/A"

        sector = profile.get("sector", "N/A")
        industry = profile.get("industry", "N/A")
        
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
            if raw_pe < 0: health_eval = "현재 적자 상태 (동종 업계 대비 펀더멘털 주의)"
            else:
                if sector in ["Technology", "Healthcare", "Communication Services"]:
                    if raw_pe <= 25: health_eval = "성장주 업계 평균 대비 저평가 (건전)"
                    elif raw_pe <= 40: health_eval = "성장주 업계 평균 수준 (보통)"
                    else: health_eval = "성장주 업계 대비 고평가 (과열 의심)"
                else:
                    if raw_pe <= 15: health_eval = "시장 평균 대비 저평가 (건전)"
                    elif raw_pe <= 25: health_eval = "시장 평균 수준 (보통)"
                    else: health_eval = "시장 평균 대비 고평가 (과열 의심)"

        target_mean = targets.get("targetConsensus", "N/A")
        target_high = targets.get("targetHigh", "N/A")
        target_low = targets.get("targetLow", "N/A")
        
        rec_val = targets.get("consensus", "N/A")
        rec_map = {"buy": "매수", "strong_buy": "강력 매수", "hold": "보유", "sell": "매도", "strong_sell": "강력 매도"}
        recommendation = rec_map.get(str(rec_val).lower(), str(rec_val).upper()) if rec_val != "N/A" else "N/A"

        next_earnings = quote.get("earningsAnnouncement")
        if next_earnings:
            next_earnings = next_earnings.split('T')[0]
        else:
            next_earnings = "미정"
        recent_earnings = "N/A"

        prices = []
        ma50, ma200, upper_band, lower_band, macd_histogram = [], [], [], [], []
        current_rsi = "N/A"
        
        # 🔥 예외 처리 강화: history가 dict 형태일 때만 처리
        if isinstance(data.get("history"), dict) and "historical" in data["history"]:
            hist_data = data["history"]["historical"]
            hist_data = hist_data[::-1]
            
            slice_map = {'1d': 1, '5d': 5, '3mo': 63, '1y': 252, '5y': 1260}
            limit = slice_map.get(period, 63)
            
            df = pd.DataFrame(hist_data)
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
                current_rsi = round(rsi_series.iloc[-1], 2) if not pd.isna(rsi_series.iloc[-1]) else "N/A"
                
            df_sliced = df.tail(limit)
            prices = df_sliced['close'].tolist()
            if 'ma50' in df.columns: ma50 = df_sliced['ma50'].tolist()
            if 'ma200' in df.columns: ma200 = df_sliced['ma200'].tolist()
            if 'upper' in df.columns: upper_band = df_sliced['upper'].tolist()
            if 'lower' in df.columns: lower_band = df_sliced['lower'].tolist()
            if 'macd_hist' in df.columns: macd_histogram = df_sliced['macd_hist'].tolist()

        financials_data = []
        # 🔥 예외 처리 강화: 리스트일 때만 처리 (에러 딕셔너리 무시)
        inc_data = data.get("income")
        if isinstance(inc_data, list):
            inc_list = inc_data[::-1]
            for inc in inc_list:
                date_str = inc.get("date", "").replace("-", ".")[2:7]
                rev = inc.get("revenue", 0)
                net = inc.get("netIncome", 0)
                financials_data.append({
                    "date": date_str,
                    "revenue": float(rev) / 1000000000 if rev else 0.0,
                    "net_income": float(net) / 1000000000 if net else 0.0
                })

        desc_full = profile.get("description", "정보 없음")
        if desc_full and desc_full != "정보 없음":
            summary_en = extract_three_sentences(desc_full)
            summary_ko = safe_translate(summary_en)
        else:
            summary_en = "기업 개요를 불러올 수 없습니다."
            summary_ko = "기업 개요를 불러올 수 없습니다."

        news_data_list = []
        if isinstance(data.get("news"), dict) and "articles" in data["news"]:
            for a in data["news"]["articles"]:
                title_en = a.get("title", "제목 없음")
                desc_en = a.get("description", "")
                
                desc_en_3lines = extract_three_sentences(desc_en)
                
                title_ko = safe_translate(title_en)
                desc_ko = safe_translate(desc_en_3lines) if desc_en_3lines else "본문 요약이 없습니다."
                
                news_data_list.append({
                    "title_en": title_en,
                    "title_ko": title_ko,
                    "publisher": a.get("source", {}).get("name", "GNews"),
                    "link": a.get("url", ""),
                    "summary_en": desc_en_3lines,
                    "summary_ko": desc_ko
                })

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
            "exchange_rate": round(exchange_rate, 2), 
            "chart_prices": prices,
            "ma50": ma50,    
            "ma200": ma200,
            "upper_band": upper_band, 
            "lower_band": lower_band, 
            "macd_histogram": macd_histogram,
            "financials": financials_data,
            "news": news_data_list,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": f"데이터 처리 중 오류: {str(e)}"}

@app.get("/api/macro")
def get_macro_data(sector_query: str = "economy"):
    try:
        macro_indicators = {}
        history_data = {"us10y": [], "vix": [], "krw": []}
        
        try:
            tnx_ticker = yf.Ticker("^TNX")
            vix_ticker = yf.Ticker("^VIX")
            krw_ticker = yf.Ticker("KRW=X")
            
            tnx = tnx_ticker.info.get("regularMarketPrice", 0.0)
            vix = vix_ticker.info.get("regularMarketPrice", 0.0)
            krw = krw_ticker.info.get("regularMarketPrice", 1350.0)
            
            history_data["us10y"] = tnx_ticker.history(period="6mo")['Close'].tolist() if not tnx_ticker.history(period="6mo").empty else []
            history_data["vix"] = vix_ticker.history(period="6mo")['Close'].tolist() if not vix_ticker.history(period="6mo").empty else []
            history_data["krw"] = krw_ticker.history(period="6mo")['Close'].tolist() if not krw_ticker.history(period="6mo").empty else []

            macro_indicators = {
                "us10y_yield": round(tnx, 2) if tnx else 4.25,
                "vix": round(vix, 2) if vix else 16.5,
                "exchange_rate": round(krw, 2),
                "market_sentiment": "과열 (탐욕)" if vix < 15 else ("안정" if vix < 20 else ("경계 (공포)" if vix < 30 else "극심한 공포"))
            }
        except Exception:
            macro_indicators = {"us10y_yield": 4.25, "vix": 16.5, "exchange_rate": 1350.0, "market_sentiment": "안정"}

        sector_topics = {
            "economy": "economy OR interest rate OR inflation",
            "fed_wallstreet": "Federal Reserve OR Wall Street OR Powell",
            "geopolitics": "geopolitics OR crude oil OR conflict",
            "science_tech": "artificial intelligence OR technology",
            "society": "employment OR labor OR housing",
            "politics": "US election OR Congress"
        }
        
        search_term = sector_topics.get(sector_query, "economy")
        news_data_list = []
        
        gnews_url = f"https://gnews.io/api/v4/search?q={search_term}&lang=en&country=us&max=6&apikey={GNEWS_KEY}"
        res = requests.get(gnews_url, timeout=5)
        
        if res.status_code == 200:
            articles = res.json().get("articles", [])
            for a in articles:
                title_en = a.get("title", "제목 없음")
                desc_en = a.get("description", "")
                
                desc_en_3lines = extract_three_sentences(desc_en)
                
                title_ko = safe_translate(title_en)
                desc_ko = safe_translate(desc_en_3lines) if desc_en_3lines else "본문 요약이 없습니다."
                
                news_data_list.append({
                    "title_en": title_en,
                    "title_ko": title_ko,
                    "publisher": a.get("source", {}).get("name", "GNews"),
                    "link": a.get("url", ""),
                    "summary_en": desc_en_3lines,
                    "summary_ko": desc_ko
                })

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
                "name": "워런 버핏 (Berkshire Hathaway) 🛡️ 방어/가치",
                "summary": "현금 비중을 역대 최대치로 늘리며 방어적 태세 유지. 주력 포트폴리오인 애플과 뱅크오브아메리카의 비중을 대폭 축소하고 단기 국채 매입에 집중.",
                "top_holdings": [
                    {"company": "Apple", "ticker": "AAPL", "percent": 28.5},
                    {"company": "American Express", "ticker": "AXP", "percent": 11.2},
                    {"company": "Bank of America", "ticker": "BAC", "percent": 10.8},
                    {"company": "Coca-Cola", "ticker": "KO", "percent": 8.5},
                    {"company": "Chevron", "ticker": "CVX", "percent": 5.4}
                ]
            },
            {
                "name": "스탠리 드루켄밀러 (Duquesne) ⚔️ 공격/매크로",
                "summary": "엔비디아 비중을 크게 줄인 뒤, 중소형 AI 인프라 및 전력망 관련주로 수익 실현 모델 전환.",
                "top_holdings": [
                    {"company": "Microsoft", "ticker": "MSFT", "percent": 14.1},
                    {"company": "Coupang", "ticker": "CPNG", "percent": 9.8},
                    {"company": "Vistra Corp", "ticker": "VST", "percent": 8.5},
                    {"company": "Coherent", "ticker": "COHR", "percent": 7.2},
                    {"company": "Seagate", "ticker": "STX", "percent": 5.9}
                ]
            },
            {
                "name": "캐시 우드 (ARK Invest) 🚀 초공격/혁신성장",
                "summary": "테슬라 지속 매수 및 코인베이스, 로블록스 등 혁신 파괴적 기술 섹터 저가 매수에 집중.",
                "top_holdings": [
                    {"company": "Tesla", "ticker": "TSLA", "percent": 9.5},
                    {"company": "Coinbase", "ticker": "COIN", "percent": 8.1},
                    {"company": "Roku", "ticker": "ROKU", "percent": 7.3},
                    {"company": "Block", "ticker": "SQ", "percent": 6.2},
                    {"company": "Roblox", "ticker": "RBLX", "percent": 5.0}
                ]
            },
            {
                "name": "레이 달리오 (Bridgewater) 🛡️ 방어/올웨더",
                "summary": "거시 경제 사이클에 맞춘 인덱스 및 ETF 중심의 분산 투자. 최근 신흥국 ETF 비중 확대.",
                "top_holdings": [
                    {"company": "iShares Core S&P 500", "ticker": "IVV", "percent": 5.8},
                    {"company": "Emerging Markets ETF", "ticker": "IEMG", "percent": 5.2},
                    {"company": "Alphabet", "ticker": "GOOGL", "percent": 3.1},
                    {"company": "Meta Platforms", "ticker": "META", "percent": 2.9},
                    {"company": "Procter & Gamble", "ticker": "PG", "percent": 2.5}
                ]
            },
            {
                "name": "빌 애크먼 (Pershing Square) ⚔️ 집중/행동주의",
                "summary": "극소수의 고품질 우량 기업에 자본을 집중하는 전략. 치폴레와 알파벳의 지분 다수 보유.",
                "top_holdings": [
                    {"company": "Chipotle", "ticker": "CMG", "percent": 20.5},
                    {"company": "Hilton", "ticker": "HLT", "percent": 18.2},
                    {"company": "Restaurant Brands", "ticker": "QSR", "percent": 17.1},
                    {"company": "Alphabet (Class C)", "ticker": "GOOG", "percent": 13.5},
                    {"company": "Canadian Pacific", "ticker": "CP", "percent": 12.0}
                ]
            },
            {
                "name": "마이클 버리 (Scion Asset) 🔄 역발상/가치",
                "summary": "중국 거대 테크 기업에 대한 강력한 역발상 배팅 유지 및 결제 인프라 등 저평가 섹터 집중.",
                "top_holdings": [
                    {"company": "Alibaba", "ticker": "BABA", "percent": 21.3},
                    {"company": "JD.com", "ticker": "JD", "percent": 15.5},
                    {"company": "Baidu", "ticker": "BIDU", "percent": 12.0},
                    {"company": "HCA Healthcare", "ticker": "HCA", "percent": 8.5},
                    {"company": "Citigroup", "ticker": "C", "percent": 7.2}
                ]
            },
            {
                "name": "켄 그리핀 (Citadel) 🧮 퀀트/초분산",
                "summary": "수천 개의 주식을 초분산하여 리스크를 극도로 통제하며 빅테크 중심의 콜/풋 옵션 양방향 헷징.",
                "top_holdings": [
                    {"company": "NVIDIA", "ticker": "NVDA", "percent": 1.5},
                    {"company": "Microsoft", "ticker": "MSFT", "percent": 1.2},
                    {"company": "Apple", "ticker": "AAPL", "percent": 1.1},
                    {"company": "Amazon", "ticker": "AMZN", "percent": 0.9},
                    {"company": "Meta Platforms", "ticker": "META", "percent": 0.8}
                ]
            }
        ]
    }

@app.get("/api/calendar")
def get_calendar(tickers: str = ""):
    if not tickers:
        return {"status": "success", "calendar": []}

    ticker_list = [t.strip().upper() for t in tickers.split(",")]
    calendar_data = []

    for t in ticker_list:
        try:
            profile_url = f"https://financialmodelingprep.com/api/v3/profile/{t}?apikey={FMP_KEY}"
            quote_url = f"https://financialmodelingprep.com/api/v3/quote/{t}?apikey={FMP_KEY}"
            
            profile_res = fetch_json(profile_url)
            quote_res = fetch_json(quote_url)
            
            profile = get_first_item(profile_res)
            quote = get_first_item(quote_res)
            
            if profile and quote:
                next_earnings = quote.get("earningsAnnouncement", "미정")
                if next_earnings and next_earnings != "미정":
                    next_earnings = next_earnings.split('T')[0]
                
                div_rate = profile.get("lastDiv", 0.0)
                price = quote.get("price", 1.0)
                div_yield = round((div_rate / price) * 100, 2) if price and div_rate else "N/A"
                
                calendar_data.append({
                    "ticker": t,
                    "next_earnings": next_earnings,
                    "next_dividend": "기업 공식 확인 요망",
                    "div_rate": div_rate if div_rate else "N/A",
                    "div_yield": div_yield
                })
            else:
                raise Exception()
        except:
            calendar_data.append({
                "ticker": t,
                "next_earnings": "조회 실패",
                "next_dividend": "조회 실패",
                "div_rate": "N/A",
                "div_yield": "N/A"
            })
            
    return {"status": "success", "calendar": calendar_data}
