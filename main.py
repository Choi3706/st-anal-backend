from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
from datetime import datetime
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import re
import html
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

# 야후 파이낸스 봇 차단 우회용 세션 생성
def get_yf_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    return session

def safe_translate(text: str) -> str:
    if not text or not text.strip() or text == "정보 없음":
        return text
    try:
        return ts.translate_text(text.strip(), translator='google', from_language='en', to_language='ko')
    except Exception:
        try:
            return ts.translate_text(text.strip(), translator='bing', from_language='en', to_language='ko')
        except Exception:
            return text

def process_single_news(item):
    title = item.find('title')
    link = item.find('link')
    source = item.find('source')
    description = item.find('description')
    
    title_en = html.unescape(title.text) if title is not None and title.text else "제목 없음"
    link_url = link.text if link is not None else ""
    publisher = source.text if source is not None else "Yahoo Finance"
    
    desc_text = html.unescape(description.text) if description is not None and description.text else ""
    # HTML 태그 및 불필요한 기사 출처 패턴 강력 제거
    desc_text = re.sub(r'<[^>]+>', '', desc_text)
    desc_text = re.sub(r'^\s*([A-Za-z0-9\s]+) \-\s*', '', desc_text)
    
    # 제목과 요약이 동일하면 숨김 처리, 다르면 3~4줄 길이(약 150자)로 커팅
    if not desc_text or desc_text[:20] == title_en[:20]:
        summary_en = "본문 내용이 제공되지 않았거나 제목과 동일합니다. 링크를 클릭하여 원문을 확인해 주세요."
    else:
        summary_en = desc_text[:150] + "..." if len(desc_text) > 150 else desc_text
    
    title_ko = safe_translate(title_en)
    summary_ko = safe_translate(summary_en) if "링크를 클릭하여" not in summary_en else summary_en
    
    return {
        "title_en": title_en,
        "title_ko": title_ko,
        "publisher": publisher,
        "link": link_url,
        "summary_en": summary_en,
        "summary_ko": summary_ko
    }

@app.get("/api/ticker/{ticker}")
def get_ticker_data(ticker: str, period: str = "3mo"):
    try:
        session = get_yf_session()
        stock = yf.Ticker(ticker, session=session)
        data = stock.info
        
        krw_ticker = yf.Ticker("KRW=X", session=session)
        exchange_rate = krw_ticker.info.get("regularMarketPrice", 1350.0)
        
        hist = stock.history(period=period)
        prices = hist['Close'].tolist() if not hist.empty else []
        
        if not hist.empty and len(hist) >= 50:
            ma50 = hist['Close'].rolling(window=50).mean().fillna(0).tolist()
        else:
            ma50 = [0] * len(prices)

        if not hist.empty and len(hist) >= 200:
            ma200 = hist['Close'].rolling(window=200).mean().fillna(0).tolist()
        else:
            ma200 = [0] * len(prices)
            
        if not hist.empty and len(hist) >= 20:
            ma20 = hist['Close'].rolling(window=20).mean()
            std20 = hist['Close'].rolling(window=20).std()
            upper_band = (ma20 + (std20 * 2)).fillna(0).tolist()
            lower_band = (ma20 - (std20 * 2)).fillna(0).tolist()
        else:
            upper_band = [0] * len(prices)
            lower_band = [0] * len(prices)

        # MACD 연산
        if not hist.empty and len(hist) >= 26:
            ema12 = hist['Close'].ewm(span=12, adjust=False).mean()
            ema26 = hist['Close'].ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_histogram = (macd_line - signal_line).fillna(0).tolist()
        else:
            macd_histogram = [0] * len(prices)

        current_rsi = "N/A"
        if not hist.empty and len(hist) >= 14:
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi_series = 100 - (100 / (1 + rs))
            if not pd.isna(rsi_series.iloc[-1]):
                current_rsi = round(rsi_series.iloc[-1], 2)
        
        short_ratio = data.get("shortRatio", "N/A")
        held_by_institutions = data.get("heldPercentInstitutions", "N/A")
        if isinstance(held_by_institutions, (int, float)):
            held_by_institutions = round(held_by_institutions * 100, 2)
        
        raw_pe = data.get("forwardPE", "N/A")
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

        def format_price(val):
            return round(val, 2) if isinstance(val, (int, float)) else "N/A"
            
        target_mean = format_price(data.get("targetMeanPrice", "N/A"))
        target_high = format_price(data.get("targetHighPrice", "N/A"))
        target_low = format_price(data.get("targetLowPrice", "N/A"))
        recommendation = data.get("recommendationKey", "N/A")
        
        if recommendation != "N/A":
            rec_map = {"buy": "매수", "strong_buy": "강력 매수", "hold": "보유", "sell": "매도", "strong_sell": "강력 매도"}
            recommendation = rec_map.get(recommendation.lower(), recommendation.upper())

        recent_earnings = "미정"
        next_earnings = "미정"
        try:
            earnings = stock.get_earnings_dates(limit=10)
            if earnings is not None and not earnings.empty:
                now = pd.Timestamp.now(tz=earnings.index.tz)
                past_dates = earnings[earnings.index < now]
                future_dates = earnings[earnings.index >= now]
                if not past_dates.empty: recent_earnings = past_dates.index[0].strftime('%Y-%m-%d')
                if not future_dates.empty: next_earnings = future_dates.index[-1].strftime('%Y-%m-%d')
        except:
            pass

        financials_data = []
        try:
            q_inc = stock.quarterly_income_stmt
            if q_inc is not None and not q_inc.empty:
                dates = q_inc.columns[:8].tolist()[::-1]
                for d in dates:
                    date_str = d.strftime('%y.%m')
                    rev = q_inc.loc['Total Revenue', d] if 'Total Revenue' in q_inc.index else 0
                    net = q_inc.loc['Net Income', d] if 'Net Income' in q_inc.index else 0
                    financials_data.append({
                        "date": date_str,
                        "revenue": float(rev) / 1000000000 if pd.notna(rev) else 0.0,
                        "net_income": float(net) / 1000000000 if pd.notna(net) else 0.0
                    })
        except Exception:
            pass

        # 기업 개요 핵심 비즈니스 모델만 추출 (3문장 제한)
        full_summary_en = html.unescape(data.get("longBusinessSummary", "정보 없음"))
        if full_summary_en != "정보 없음":
            sentences = full_summary_en.split('. ')
            core_business_en = '. '.join(sentences[:3]) + "."
            summary_ko = safe_translate(core_business_en)
            summary_en = core_business_en
        else:
            summary_en = "기업 개요를 불러올 수 없습니다."
            summary_ko = "기업 개요를 불러올 수 없습니다."

        news_data = []
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
            response = requests.get(rss_url, headers=headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                raw_items = root.findall('./channel/item')[:7]
                with ThreadPoolExecutor(max_workers=4) as executor:
                    news_data = list(executor.map(process_single_news, raw_items))
        except Exception:
            pass

        return {
            "status": "success",
            "ticker": ticker.upper(),
            "current_price": data.get("currentPrice", "N/A"), 
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
            "short_ratio": short_ratio, 
            "held_by_institutions": held_by_institutions, 
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
            "news": news_data,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": "데이터를 불러올 수 없습니다 (-)"}

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
            "economy": "economy interest rate inflation",
            "fed_wallstreet": "Federal Reserve Wall Street Powell",
            "geopolitics": "geopolitics crude oil war conflict",
            "science_tech": "artificial intelligence science technology",
            "society": "society employment labor housing",
            "politics": "US politics election Congress"
        }
        
        search_term = sector_topics.get(sector_query, "economy")
        news_data = []
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            rss_url = f"https://news.google.com/rss/search?q={search_term}&hl=en-US&gl=US&ceid=US:en"
            res = requests.get(rss_url, headers=headers, timeout=10)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                raw_items = root.findall('./channel/item')[:6]
                with ThreadPoolExecutor(max_workers=3) as executor:
                    news_data = list(executor.map(process_single_news, raw_items))
        except Exception:
            pass

        return {
            "status": "success",
            "indicators": macro_indicators,
            "history": history_data,
            "news": news_data,
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

    session = get_yf_session()
    ticker_list = [t.strip().upper() for t in tickers.split(",")]
    calendar_data = []

    for t in ticker_list:
        try:
            stock = yf.Ticker(t, session=session)
            info = stock.info
            next_earnings = "미정"
            try:
                earnings = stock.get_earnings_dates(limit=10)
                if earnings is not None and not earnings.empty:
                    now = pd.Timestamp.now(tz=earnings.index.tz)
                    future_dates = earnings[earnings.index >= now]
                    if not future_dates.empty:
                        next_earnings = future_dates.index[-1].strftime('%Y-%m-%d')
            except:
                pass
            
            div_date = info.get("exDividendDate")
            next_div = datetime.fromtimestamp(div_date).strftime('%Y-%m-%d') if div_date else "배당 없음"
            div_rate = info.get("dividendRate", "N/A")
            div_yield = info.get("dividendYield", "N/A")
            if isinstance(div_yield, (float, int)):
                div_yield = round(div_yield, 2)
            
            calendar_data.append({
                "ticker": t,
                "next_earnings": next_earnings,
                "next_dividend": next_div,
                "div_rate": div_rate,
                "div_yield": div_yield
            })
        except:
            pass
            
    return {"status": "success", "calendar": calendar_data}
