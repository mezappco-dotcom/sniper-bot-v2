import ccxt
import pandas as pd
import numpy as np
import requests
import os
import time

# --- AYARLAR ---
TELEGRAM_TOKEN = os.environ.get("8398458068:AAGzILOlJojk6f5TB8LGYrXhCwmdVErZnYU")
CHAT_ID = os.environ.get("367379234")

# --- PARAMETRELER ---
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
RSI_PERIOD = 14
RSI_LONG_THRESHOLD = 55
RSI_SHORT_THRESHOLD = 45

# --- BINANCE BAĞLANTISI ---
exchange = ccxt.binance({
    'rateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- MANUEL İNDİKATÖR FONKSİYONLARI (KÜTÜPHANESİZ) ---

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_supertrend(df, period=10, multiplier=3):
    # True Range Hesaplama
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    
    # ATR Hesaplama
    df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
    
    # Basic Bands
    hl2 = (df['high'] + df['low']) / 2
    df['upper_basic'] = hl2 + (multiplier * df['atr'])
    df['lower_basic'] = hl2 - (multiplier * df['atr'])
    
    # Final Bands
    df['upper_band'] = df['upper_basic']
    df['lower_band'] = df['lower_basic']
    df['supertrend'] = True  # True = Long, False = Short
    
    # SuperTrend Mantığı
    for i in range(1, len(df)):
        if df['close'].iloc[i-1] <= df['upper_band'].iloc[i-1]:
            df.loc[df.index[i], 'upper_band'] = min(df['upper_basic'].iloc[i], df['upper_band'].iloc[i-1])
        else:
            df.loc[df.index[i], 'upper_band'] = df['upper_basic'].iloc[i]
            
        if df['close'].iloc[i-1] >= df['lower_band'].iloc[i-1]:
            df.loc[df.index[i], 'lower_band'] = max(df['lower_basic'].iloc[i], df['lower_band'].iloc[i-1])
        else:
            df.loc[df.index[i], 'lower_band'] = df['lower_basic'].iloc[i]
            
    # Trend Yönü Belirleme
    df['st_dir'] = 1 # 1: Long, -1: Short
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['upper_band'].iloc[i-1]:
            df.loc[df.index[i], 'st_dir'] = 1
        elif df['close'].iloc[i] < df['lower_band'].iloc[i-1]:
            df.loc[df.index[i], 'st_dir'] = -1
        else:
            df.loc[df.index[i], 'st_dir'] = df['st_dir'].iloc[i-1] # Trend değişmedi
            
    return df

# --- ANA FONKSİYONLAR ---

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except:
        pass

def get_top_symbols(limit=50):
    try:
        tickers = exchange.fetch_tickers()
        sorted_tickers = sorted(tickers.items(), key=lambda x: x[1]['quoteVolume'], reverse=True)
        return [symbol for symbol, data in sorted_tickers if symbol.endswith('/USDT')][:limit]
    except:
        return []

def get_data(symbol, timeframe, limit=150):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        return None

def process_symbol(symbol):
    try:
        # 1. ADIM: 15 Dakikalık Veri Analizi
        df_15m = get_data(symbol, '15m')
        if df_15m is None: return

        # İndikatörleri Hesapla (Manuel Fonksiyonlar)
        df_15m['EMA_9'] = calculate_ema(df_15m['close'], 9)
        df_15m['EMA_21'] = calculate_ema(df_15m['close'], 21)
        df_15m['EMA_55'] = calculate_ema(df_15m['close'], 55)
        df_15m['RSI'] = calculate_rsi(df_15m['close'])
        df_15m['VOL_MA'] = df_15m['volume'].rolling(window=20).mean()
        df_15m = calculate_supertrend(df_15m, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)

        last = df_15m.iloc[-1]
        
        # Sinyal Kontrolleri
        vol_spike = last['volume'] > (last['VOL_MA'] * 1.4)
        
        # Long Sinyali
        ema_long = (last['EMA_21'] > last['EMA_55']) and (last['EMA_9'] > last['EMA_21'])
        rsi_long = last['RSI'] > RSI_LONG_THRESHOLD
        st_long = last['st_dir'] == 1
        
        # Short Sinyali
        ema_short = (last['EMA_21'] < last['EMA_55']) and (last['EMA_9'] < last['EMA_21'])
        rsi_short = last['RSI'] < RSI_SHORT_THRESHOLD
        st_short = last['st_dir'] == -1

        signal = None
        if vol_spike and ema_long and rsi_long and st_long:
            signal = "LONG"
        elif vol_spike and ema_short and rsi_short and st_short:
            signal = "SHORT"
        
        if not signal: return

        # --- TEYİT MEKANİZMASI (1H ve 4H) ---
        
        # 1 Saatlik Teyit
        df_1h = get_data(symbol, '1h', limit=100)
        df_1h['EMA_13'] = calculate_ema(df_1h['close'], 13)
        df_1h['EMA_34'] = calculate_ema(df_1h['close'], 34)
        df_1h['EMA_89'] = calculate_ema(df_1h['close'], 89)
        last_1h = df_1h.iloc[-1]
        
        h1_long = (last_1h['EMA_34'] > last_1h['EMA_89']) and (last_1h['EMA_13'] > last_1h['EMA_34'])
        h1_short = (last_1h['EMA_34'] < last_1h['EMA_89']) and (last_1h['EMA_13'] < last_1h['EMA_34'])
        
        if signal == "LONG" and not h1_long: return
        if signal == "SHORT" and not h1_short: return

        # 4 Saatlik Teyit
        df_4h = get_data(symbol, '4h', limit=120)
        df_4h['EMA_50'] = calculate_ema(df_4h['close'], 50)
        df_4h['EMA_100'] = calculate_ema(df_4h['close'], 100)
        last_4h = df_4h.iloc[-1]
        
        h4_long = last_4h['EMA_50'] > last_4h['EMA_100']
        h4_short = last_4h['EMA_50'] < last_4h['EMA_100']

        if signal == "LONG" and not h4_long: return
        if signal == "SHORT" and not h4_short: return

        # Funding Filtresi
        funding = exchange.fetch_funding_rate(symbol)
        if abs(funding['fundingRate']) > 0.001: return

        # Mesaj Gönder
        emoji = "🟢" if signal == "LONG" else "🔴"
        msg = f"{emoji} **V2 SİNYALİ**\n\n💎 #{symbol.replace('/USDT','')}\n🚀 {signal}\n💰 {last['close']}\n📊 RSI: {last['RSI']:.1f}"
        send_telegram_message(msg)
        print(f"Sinyal: {symbol} {signal}")

    except Exception as e:
        print(f"Hata {symbol}: {e}")

if __name__ == "__main__":
    print("Tarama başlıyor...")
    symbols = get_top_symbols()
    for sembol in symbols:
        process_symbol(sembol)
        time.sleep(0.5)
    print("Bitti.")
