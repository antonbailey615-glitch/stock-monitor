import os, re, time, threading, traceback, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
from playwright.sync_api import sync_playwright
import requests

# ========= CONFIG =========
PRODUCT_URL      = os.getenv("PRODUCT_URL", "https://ubersaccount.selly.store/product/82c13699")
DISCORD_WEBHOOK  = os.getenv("DISCORD_WEBHOOK", "https://discordapp.com/api/webhooks/1403822999450423456/5umMV4migfB5Z4WqGVxzD-AJsEWno4AC-U_YmnEHJNGwLrPP9bpLvlrtQAh4mTGC20K7")
CHECK_INTERVAL   = float(os.getenv("CHECK_INTERVAL", "3"))     # seconds
ALERT_COOLDOWN   = float(os.getenv("ALERT_COOLDOWN", "60"))    # seconds
STOCK_SELECTOR   = os.getenv("STOCK_SELECTOR", ".tox9o4ajE28leI_5ZvBv")
COOKIE_HEADER    = os.getenv("COOKIE_HEADER", "").strip()      # usually not needed w/ Playwright
HEARTBEAT_SEC    = int(os.getenv("HEARTBEAT_SEC", "300"))      # 5 min heartbeat
RECYCLE_EVERY_S  = int(os.getenv("RECYCLE_EVERY_SEC", "21600"))# recycle browser every 6h (21600)

STRICT_STOCK = re.compile(r"\b(\d+)\s*(?:in\s*stock)\b", re.I)

# ========= HEALTH SERVER (keeps service “alive”) =========
def start_http_server():
    port = int(os.getenv("PORT", "8080"))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/plain")
            self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): return
    threading.Thread(target=HTTPServer(("0.0.0.0", port), H).serve_forever, daemon=True).start()
    print(f"[*] HTTP health server listening on :{port}")

# ========= DISCORD =========
def send_discord(msg: str):
    if not DISCORD_WEBHOOK:
        print("[!] DISCORD_WEBHOOK missing — skipping"); return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
        print(f"[*] Discord -> {r.status_code} | {msg[:140]}")
    except Exception as e:
        print(f"[!] Discord failed: {e}")

# ========= BADGE READERS =========
def read_badge_qty(page):
    el = page.query_selector(STOCK_SELECTOR)
    if not el:
        print("[i] Badge not found"); return None
    txt = (el.inner_text() or "").strip()
    m = STRICT_STOCK.search(txt)
    if m:
        qty = int(m.group(1))
    elif "out of stock" in txt.lower():
        qty = 0
    else:
        qty = None
    print(f"[*] BADGE -> {txt!r} => {qty}")
    return qty

def check_once(page, product_url):
    url = f"{product_url}?t={int(time.time())}"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(700)  # let React render

    # First read
    qty1 = read_badge_qty(page)

    # Confirm after 500ms
    page.wait_for_timeout(500)
    qty2 = read_badge_qty(page)

    return qty2 if qty2 is not None else qty1

# ========= BOT RUNNER (single browser lifecycle) =========
def run_bot_once():
    print("[*] Selly restock monitor (STRICT badge-only, debounced)")
    print(f"    URL: {PRODUCT_URL} | Interval: {CHECK_INTERVAL}s | Cooldown: {ALERT_COOLDOWN}s")
    if not DISCORD_WEBHOOK:
        print("[!] Set DISCORD_WEBHOOK to your webhook URL (in Railway → Variables).")
    send_discord(f"✅ Bot online. Watching: {PRODUCT_URL} (every {int(CHECK_INTERVAL)}s)")

    last_state = None           # "in" | "out"
    last_alert_ts = 0.0
    confirm_counter = 0         # require 2 consecutive in-stock reads
    last_heartbeat = 0.0
    started_at = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ))

        # Optional cookies (rare with Playwright)
        if COOKIE_HEADER:
            cookies = []
            for part in COOKIE_HEADER.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies.append({"name": k.strip(), "value": v.strip(), "url": "https://ubersaccount.selly.store"})
            if cookies:
                context.add_cookies(cookies)

        page = context.new_page()

        try:
            while True:
                loop_start = time.time()

                # Heartbeat (every HEARTBEAT_SEC)
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_SEC:
                    print(f"[♥] Heartbeat {dt.datetime.utcnow().isoformat()}Z | up {(now - started_at):.0f}s")
                    last_heartbeat = now

                # Recycle browser periodically to avoid leaks
                if now - started_at >= RECYCLE_EVERY_S:
                    print("[*] Recycling browser to prevent leaks")
                    page.close(); context.close(); browser.close()
                    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                    context = browser.new_context(user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                    ))
                    if COOKIE_HEADER and cookies:
                        context.add_cookies(cookies)
                    page = context.new_page()
                    started_at = time.time()

                # Perform check
                try:
                    qty = check_once(page, PRODUCT_URL)
                except Exception as e:
                    print(f"[!] Page check failed: {e}")
                    qty = None

                in_stock = (qty is not None and qty > 0)
                state = "in" if in_stock else "out"
                print(f"[*] DETECT -> qty={qty} state={state}")

                # Debounce: need 2 consecutive "in" reads to flip to IN
                if in_stock:
                    confirm_counter = min(confirm_counter + 1, 2)
                else:
                    confirm_counter = 0

                effective_state = "in" if confirm_counter >= 2 else "out"

                if effective_state != last_state:
                    if effective_state == "in" and (time.time() - last_alert_ts >= ALERT_COOLDOWN):
                        send_discord(f"🚨 Restock detected (qty: {qty})! {PRODUCT_URL}")
                        last_alert_ts = time.time()
                    last_state = effective_state

                # Exact-interval pacing
                time.sleep(max(0, CHECK_INTERVAL - (time.time() - loop_start)))

        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

# ========= SUPERVISOR (auto-restart on crash) =========
def supervisor():
    backoff = 5
    start_http_server()
    while True:
        try:
            run_bot_once()
        except SystemExit:
            raise
        except Exception:
            print("[!] Top-level crash:\n" + traceback.format_exc())
        print(f"[*] Restarting in {backoff}s...")
        time.sleep(backoff)
        backoff = min(backoff * 2, 300)  # cap at 5 minutes

if __name__ == "__main__":
    supervisor()

