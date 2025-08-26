import os, re, time, threading, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

# ========= CONFIG =========
PRODUCT_URL = os.getenv("PRODUCT_URL", "https://ubersaccount.selly.store/product/82c13699")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "https://discordapp.com/api/webhooks/1403822999450423456/5umMV4migfB5Z4WqGVxzD-AJsEWno4AC-U_YmnEHJNGwLrPP9bpLvrtQAh4mTGC20K7")
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", "3"))
ALERT_COOLDOWN = float(os.getenv("ALERT_COOLDOWN", "60"))
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "300"))

STRICT_STOCK = re.compile(r"\b(\d+)\s*(?:in\s*stock)\b", re.I)

# ========= HEALTH SERVER =========
def start_http_server():
    port = int(os.getenv("PORT", "8080"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Railway bot is running!")
        def log_message(self, *a): return
    
    server = HTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[*] HTTP health server listening on :{port}")

# ========= DISCORD =========
def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        print("[!] DISCORD_WEBHOOK missing")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        print(f"[*] Discord -> {r.status_code} | {msg[:100]}")
    except Exception as e:
        print(f"[!] Discord failed: {e}")

# ========= STOCK CHECKER =========
def check_stock():
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        url = f"{PRODUCT_URL}?t={int(time.time())}"
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        html = response.text.lower()
        
        if "out of stock" in html or "sold out" in html:
            return 0
        elif re.search(STRICT_STOCK, response.text):
            match = re.search(STRICT_STOCK, response.text)
            return int(match.group(1))
        elif "add to cart" in html:
            return 1
        return None
        
    except Exception as e:
        print(f"[!] Stock check failed: {e}")
        return None

# ========= MAIN =========
def main():
    start_http_server()
    print("[*] Stock monitor starting...")
    
    send_discord("✅ Bot online on Railway!")
    
    last_state = None
    last_alert_ts = 0.0
    confirm_counter = 0
    last_heartbeat = 0.0
    started_at = time.time()
    
    try:
        while True:
            cycle_start = time.time()
            
            try:
                qty = check_stock()
                now = time.time()
                current_state = "in" if (qty and qty > 0) else "out"
                
                if current_state == "in" and last_state != "in":
                    confirm_counter += 1
                    print(f"[*] Stock detected! Confirmation #{confirm_counter}/2")
                    
                    if confirm_counter >= 2:
                        if now - last_alert_ts >= ALERT_COOLDOWN:
                            send_discord(f"🚨 **RESTOCK!** {qty} in stock at {PRODUCT_URL}")
                            last_alert_ts = now
                            print(f"[!] ALERT SENT: {qty} in stock")
                        
                        last_state = "in"
                        confirm_counter = 0
                elif current_state == "out":
                    last_state = "out"
                    confirm_counter = 0
                
                if now - last_heartbeat >= HEARTBEAT_SEC:
                    uptime_mins = int((now - started_at) / 60)
                    send_discord(f"💓 Bot alive ({uptime_mins}m). Status: {current_state}")
                    last_heartbeat = now
                    
            except Exception as e:
                print(f"[!] Check failed: {e}")
            
            elapsed = time.time() - cycle_start
            sleep_time = max(0, CHECK_INTERVAL - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        send_discord("🛑 Bot stopped")

if __name__ == "__main__":
    main()
