import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import os
import time

# --- AYARLAR ---
TELEGRAM_TOKEN = os.environ.get("8398458068:AAGzILOlJojk6f5TB8LGYrXhCwmdVErZnYU")
CHAT_ID = os.environ.get("367379234")

# Mr_Rakun Stratejisine Yakın Optimize Ayarlar (Length=10, Multiplier=3 Standarttır, hassasiyet için 2 denenebilir)
SUPERTREND_LENGTH = 10
SUPERTREND_MULTIPLIER = 3

# RSI Sınırları
RSI_LONG_THRESHOLD = 55
RSI_SHORT_THRESHOLD = 45

# --- BINANCE BAĞLANTISI ---
exchange = ccxt.binance({
    'rateLimit': True,
    'options': {'defaultType': 'future'} # Vadeli işlemler (Futures)
})

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram token veya Chat ID eksik!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram hatası: {e}")

def get_top_symbols(limit=40):
    # Hacmi en yüksek USDT çiftlerini getirir
    try:
        tickers = exchange.fetch_tickers()
        sorted_tickers = sorted(tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
        # USDT ile bitenleri filtrele
        symbols = [symbol for symbol, data in sorted_tickers if symbol.endswith('/USDT')][:limit]
        return symbols
    except Exception as e:
        print(f"Sembol çekme hatası: {e}")
        return []

def get_data(symbol, timeframe, limit=150):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Veri hatası ({symbol} - {timeframe}): {e}")
        return None

def calculate_indicators(df):
    # --- EMA HESAPLAMALARI ---
    # 15m için: 9, 21, 55
    # 1H için: 13, 34, 89
    # 4H için: 50, 100
    df['EMA_9'] = ta.ema(df['close'], length=9)
    df['EMA_13'] = ta.ema(df['close'], length=13)
    df['EMA_21'] = ta.ema(df['close'], length=21)
    df['EMA_34'] = ta.ema(df['close'], length=34)
    df['EMA_50'] = ta.ema(df['close'], length=50)
    df['EMA_55'] = ta.ema(df['close'], length=55)
    df['EMA_89'] = ta.ema(df['close'], length=89)
    df['EMA_100'] = ta.ema(df['close'], length=100)

    # --- RSI ---
    df['RSI'] = ta.rsi(df['close'], length=14)

    # --- SUPERTREND (Mr_Rakun Opt.) ---
    supertrend = ta.supertrend(df['high'], df['low'], df['close'], length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER)
    # Supertrend dönüşü: SUPERT_10_3.0 (Trend Çizgisi), SUPERTd_10_3.0 (Yön: 1=Long, -1=Short)
    st_col = f'SUPERT_{SUPERTREND_LENGTH}_{SUPERTREND_MULTIPLIER}.0'
    st_dir_col = f'SUPERTd_{SUPERTREND_LENGTH}_{SUPERTREND_MULTIPLIER}.0'
    df['ST_LINE'] = supertrend[st_col]
    df['ST_DIR'] = supertrend[st_dir_col]

    # --- HACİM SPIKE ---
    # Son 20 mumun ortalama hacmi
    df['VOL_MA'] = ta.sma(df['volume'], length=20)
    
    return df

def check_funding_rate(symbol):
    # Funding oranı çok yüksekse veya çok düşükse (ters işlem için) risklidir.
    # Burada sadece aşırı durumları filtreliyoruz.
    try:
        funding = exchange.fetch_funding_rate(symbol)
        rate = funding['fundingRate']
        # Örnek: %0.1'den büyükse veya -%0.1'den küçükse girme (Risk Yönetimi)
        if abs(rate) > 0.001: 
            return False
        return True
    except:
        return True # Veri alınamazsa geç (agresif mod)

def analyze_market():
    symbols = get_top_symbols(limit=50) # En hacimli 50 coin
    print(f"{len(symbols)} coin taranıyor...")

    for symbol in symbols:
        try:
            # 1. ADIM: 15 Dakikalık Veri (Ana Sinyal Mekanizması)
            df_15m = get_data(symbol, '15m')
            if df_15m is None: continue
            df_15m = calculate_indicators(df_15m)
            
            last_row = df_15m.iloc[-1]
            prev_row = df_15m.iloc[-2]

            # --- SİNYAL KONTROLLERİ (15m) ---
            
            # Hacim Spike Kontrolü (1.4x)
            vol_cond = last_row['volume'] > (last_row['VOL_MA'] * 1.4)
            
            # RSI Kontrolü
            rsi_long = last_row['RSI'] > RSI_LONG_THRESHOLD
            rsi_short = last_row['RSI'] < RSI_SHORT_THRESHOLD

            # SuperTrend Yönü (1=Long, -1=Short)
            st_long = last_row['ST_DIR'] == 1
            st_short = last_row['ST_DIR'] == -1

            # 15m EMA Trend Filtresi (21 vs 55) & Momentum (9 vs 21)
            # Long: 21 > 55 VE 9 > 21
            ema_15m_long = (last_row['EMA_21'] > last_row['EMA_55']) and (last_row['EMA_9'] > last_row['EMA_21'])
            # Short: 21 < 55 VE 9 < 21
            ema_15m_short = (last_row['EMA_21'] < last_row['EMA_55']) and (last_row['EMA_9'] < last_row['EMA_21'])

            # Sinyal Adayı Var mı?
            signal = None
            if vol_cond and rsi_long and st_long and ema_15m_long:
                signal = "LONG"
            elif vol_cond and rsi_short and st_short and ema_15m_short:
                signal = "SHORT"
            
            if not signal:
                continue # Sinyal yoksa diğer coine geç

            # --- TEYİT MEKANİZMASI (1H ve 4H) ---
            # Sinyal varsa üst zaman dilimlerini kontrol et (API tasarrufu için buraya koyduk)
            
            # 1 Saatlik Kontrol
            df_1h = get_data(symbol, '1h', limit=50)
            df_1h = calculate_indicators(df_1h)
            last_1h = df_1h.iloc[-1]
            
            # 1H Kuralı: Trend (34 > 89) ve Momentum (13 > 34)
            h1_long_ok = (last_1h['EMA_34'] > last_1h['EMA_89']) and (last_1h['EMA_13'] > last_1h['EMA_34'])
            h1_short_ok = (last_1h['EMA_34'] < last_1h['EMA_89']) and (last_1h['EMA_13'] < last_1h['EMA_34'])

            if signal == "LONG" and not h1_long_ok: continue
            if signal == "SHORT" and not h1_short_ok: continue

            # 4 Saatlik Kontrol
            df_4h = get_data(symbol, '4h', limit=50)
            df_4h = calculate_indicators(df_4h)
            last_4h = df_4h.iloc[-1]

            # 4H Kuralı: Ana Trend (50 > 100)
            h4_long_ok = last_4h['EMA_50'] > last_4h['EMA_100']
            h4_short_ok = last_4h['EMA_50'] < last_4h['EMA_100']

            if signal == "LONG" and not h4_long_ok: continue
            if signal == "SHORT" and not h4_short_ok: continue

            # Funding Filtresi
            if not check_funding_rate(symbol):
                continue
            
            # Spam/Cooldown Kontrolü:
            # Sadece yeni bir kesişim veya taze sinyal mi? 
            # (SuperTrend'in son 2 mum içinde değişip değişmediğine bakarak spam engelliyoruz)
            is_fresh = prev_row['ST_DIR'] != last_row['ST_DIR']
            # Veya RSI yeni mi bölgeye girdi?
            # Botun her 15 dk'da bir çalışacağı varsayımıyla, sürekli aynı trendde mesaj atmaması için:
            # Burayı biraz esnek bırakıyorum, trend devam ediyorsa bile hacim patlaması varsa haber versin.
            
            # --- MESAJ GÖNDER ---
            price = last_row['close']
            trend_emoji = "🟢" if signal == "LONG" else "🔴"
            
            msg = f"""
{trend_emoji} **V2 SNIPER SİNYALİ** {trend_emoji}

💎 **Coin:** #{symbol.replace('/USDT', '')}
🚀 **Yön:** {signal}
💰 **Fiyat:** {price}
📊 **RSI:** {last_row['RSI']:.2f}
🌊 **Hacim:** Ortalamanın 1.4x katı!

✅ **Teyitler:**
- 15m Momentum & Trend: ONAYLI
- 1H Trend (34/89) & Mom (13/34): ONAYLI
- 4H Ana Trend (50/100): ONAYLI
- SuperTrend & Funding: UYGUN

_Bu bir yatırım tavsiyesi değildir._
            """
            send_telegram_message(msg)
            print(f"Sinyal gönderildi: {symbol} - {signal}")
            time.sleep(1) # Telegram rate limit yememek için bekleme

        except Exception as e:
            print(f"Hata ({symbol}): {e}")
            continue

if __name__ == "__main__":
    print("Bot V2 başlatılıyor...")
    analyze_market()
    print("Tarama bitti.")
