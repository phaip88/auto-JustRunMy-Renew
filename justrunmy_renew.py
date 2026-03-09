#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import socket
import signal
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, unquote
from seleniumbase import SB

LOGIN_URL = "https://justrunmy.app/id/Account/Login"
DOMAIN    = "justrunmy.app"

# ============================================================
#  环境变量与全局变量
# ============================================================
EMAIL        = os.environ.get("JUSTRUNMY_EMAIL")
PASSWORD     = os.environ.get("JUSTRUNMY_PASSWORD")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID")
HY2_PROXY_URL = os.environ.get("HY2_PROXY_URL", "").strip()
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "51080"))

if not EMAIL or not PASSWORD:
    print("❌ 致命错误：未找到 JUSTRUNMY_EMAIL 或 JUSTRUNMY_PASSWORD 环境变量！")
    print("💡 请检查 GitHub Repository Secrets 是否配置正确。")
    sys.exit(1)

# 全局变量，用于动态保存网页上抓取到的应用名称
DYNAMIC_APP_NAME = "未知应用"

# ============================================================
#  代理管理模块 (Hysteria2 -> SOCKS5)
# ============================================================
class Hy2Proxy:
    def __init__(self, url):
        self.url = url
        self.proc = None

    def start(self):
        if not self.url:
            return False
        print("📡 启动 Hysteria2...")
        print(f"📝 代理 URL: {self.url[:60]}...")

        try:
            u = self.url.replace("hysteria2://", "").replace("hy2://", "")
            parsed = urlparse("scheme://" + u)
            params = parse_qs(parsed.query)
            hostname = parsed.hostname
            port = parsed.port

            print(f"🔍 解析结果:")
            print(f"   hostname: {hostname}")
            print(f"   port: {port}")
            print(f"   username: {parsed.username[:20] if parsed.username else 'None'}...")

            if hostname and ':' in hostname:
                server = f"[{hostname}]:{port}"
            else:
                server = f"{hostname}:{port}"

            cfg = {
                "server": server,
                "auth": unquote(parsed.username) if parsed.username else "",
                "tls": {
                    "sni": params.get("sni", [hostname])[0],
                    "insecure": params.get("insecure", ["0"])[0] == "1",
                    "alpn": params.get("alpn", ["h3"])[0],
                },
                "socks5": {"listen": f"127.0.0.1:{SOCKS_PORT}"}
            }

            print(f"📋 配置内容:")
            print(f"   server: {cfg['server']}")
            print(f"   sni: {cfg['tls']['sni']}")
            print(f"   insecure: {cfg['tls']['insecure']}")
            print(f"   alpn: {cfg['tls']['alpn']}")
            print(f"   socks5: {cfg['socks5']['listen']}")

            path = "/tmp/hy2.json"
            with open(path, "w") as f:
                json.dump(cfg, f)
            print(f"✅ 配置文件已写入: {path}")

            print(f"🚀 启动 hysteria 进程...")
            self.proc = subprocess.Popen(
                ["hysteria", "client", "-c", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True
            )
            print(f"✅ 进程已启动，PID: {self.proc.pid}")

            for attempt in range(30):
                time.sleep(1)
                with socket.socket() as s:
                    result = s.connect_ex(("127.0.0.1", SOCKS_PORT))
                    if result == 0:
                        print(f"✅ HY2 已就绪 (第 {attempt+1} 秒)")
                        return True
                    if attempt % 5 == 0:
                        print(f"⏳ 等待连接... ({attempt+1}/30)")

            print("❌ 30 秒内未能连接到 SOCKS5 端口")
            # 打印进程输出用于调试
            if self.proc.stdout:
                try:
                    stdout, stderr = self.proc.communicate(timeout=2)
                    if stdout:
                        print(f"📤 stdout: {stdout[:200]}")
                    if stderr:
                        print(f"📥 stderr: {stderr[:200]}")
                except:
                    pass
            return False
        except Exception as e:
            print(f"❌ 启动代理异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def stop(self):
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                pass
        print("🛑 HY2 已停止")

    @property
    def proxy(self):
        return f"socks5://127.0.0.1:{SOCKS_PORT}"


def get_proxy_manager():
    if HY2_PROXY_URL:
        return Hy2Proxy(HY2_PROXY_URL)
    return None


def start_proxy_with_retry(max_retries=3):
    """启动代理，失败时重试"""
    proxy_manager = get_proxy_manager()
    proxy_url = None
    
    if not proxy_manager:
        return None, None
    
    for attempt in range(1, max_retries + 1):
        print(f"🔄 尝试启动代理 ({attempt}/{max_retries})...")
        if proxy_manager.start():
            proxy_url = proxy_manager.proxy
            print(f"✅ 代理已启动：{proxy_url}")
            return proxy_manager, proxy_url
        else:
            if attempt < max_retries:
                print(f"⏳ 等待 5 秒后重试...")
                time.sleep(5)
            else:
                print("⚠️ 代理启动失败，继续使用直连模式")
    
    return None, None

# ============================================================
#  Telegram 推送模块
# ============================================================
def send_tg_message(status_icon, status_text, time_left, ipinfo="未知", start_time=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    # 获取北京时间 (UTC+8)
    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    
    # 计算开始执行的时间
    start_time_str = ""
    if start_time:
        start_local_time = time.gmtime(start_time + 8 * 3600)
        start_time_str = time.strftime("%Y-%m-%d %H:%M:%S", start_local_time)

    # 按照格式拼接消息，动态注入抓取到的应用名称
    text = (
        f"justrunmy.app 续期报告\n🖥 {DYNAMIC_APP_NAME}\n"
        f"{status_icon} {status_text}\n"
        f"⏱️ 剩余: {time_left}\n"
        f"🌐 IP: {ipinfo}\n"
        f"开始时间: {start_time_str}\n"
        f"完成时间: {current_time_str}"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("  📩 Telegram 通知发送成功！")
        else:
            print(f"  ⚠️ Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"  ⚠️ Telegram 通知发送异常: {e}")

# ============================================================
#  页面注入脚本
# ============================================================
_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_COORDS_JS = """
(function(){
    var iframes = document.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var src = iframes[i].src || '';
        if (src.includes('cloudflare') || src.includes('turnstile') || src.includes('challenges')) {
            var r = iframes[i].getBoundingClientRect();
            if (r.width > 0 && r.height > 0)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
        }
    }
    var inp = document.querySelector('input[name="cf-turnstile-response"]');
    if (inp) {
        var p = inp.parentElement;
        for (var j = 0; j < 5; j++) {
            if (!p) break;
            var r = p.getBoundingClientRect();
            if (r.width > 100 && r.height > 30)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
            p = p.parentElement;
        }
    }
    return null;
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# ============================================================
#  底层输入工具
# ============================================================
def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)

def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")

# ============================================================
#  人机验证处理
# ============================================================
def _click_turnstile(sb):
    try:
        coords = sb.execute_script(_COORDS_JS)
    except Exception as e:
        print(f"  ⚠️ 获取 Turnstile 坐标失败: {e}")
        return
    if not coords:
        print("  ⚠️ 无法定位 Turnstile 坐标")
        return
    try:
        wi = sb.execute_script(_WININFO_JS)
    except Exception:
        wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
        
    bar = wi["oh"] - wi["ih"]
    ax  = coords["cx"] + wi["sx"]
    ay  = coords["cy"] + wi["sy"] + bar
    print(f"  🖱️ 物理级点击 Turnstile ({ax}, {ay})")
    _xdotool_click(ax, ay)

def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)
    
    if sb.execute_script(_SOLVED_JS):
        print("  ✅ 已静默通过")
        return True

    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    for attempt in range(6):
        if sb.execute_script(_SOLVED_JS):
            print(f"  ✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
            return True
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.3)
        
        _click_turnstile(sb)
        
        for _ in range(8):
            time.sleep(0.5)
            if sb.execute_script(_SOLVED_JS):
                print(f"  ✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True
        print(f"  ⚠️ 第 {attempt + 1} 次未通过，重试...")

    print("  ❌ Turnstile 6 次均失败")
    return False

# ============================================================
#  账户登录模块
# ============================================================
def login(sb) -> bool:
    print(f"🌐 打开登录页面: {LOGIN_URL}")
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)
    time.sleep(4)

    try:
        sb.wait_for_element('input[name="Email"]', timeout=15)
    except Exception:
        print("❌ 页面未加载出登录表单")
        sb.save_screenshot("login_load_fail.png")
        return False

    print("🍪 关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱...")
    js_fill_input(sb, 'input[name="Email"]', EMAIL)
    time.sleep(0.3)
    
    print("🔑 填写密码...")
    js_fill_input(sb, 'input[name="Password"]', PASSWORD)
    time.sleep(1)

    if sb.execute_script(_EXISTS_JS):
        if not handle_turnstile(sb):
            print("❌ 登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return False
    else:
        print("ℹ️ 未检测到 Turnstile")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys('input[name="Password"]', '\n')

    print("⏳ 等待登录跳转...")
    for _ in range(12):
        time.sleep(1)
        if sb.get_current_url().split('?')[0].lower() != LOGIN_URL.lower():
            break

    current_url = sb.get_current_url()
    print(f"🔗 当前 URL: {current_url}")
    
    if sb.get_current_url().split('?')[0].lower() != LOGIN_URL.lower():
        print("✅ 登录成功！")
        return True
        
    print("❌ 登录失败，页面没有跳转。")
    sb.save_screenshot("login_failed.png")
    return False

# ============================================================
#  自动续期模块 (动态抓取名称 + TG 通知)
# ============================================================
def renew(sb, ipinfo="未知", start_time=None) -> bool:
    global DYNAMIC_APP_NAME

    print("\n" + "="*50)
    print("   🚀 开始自动续期流程")
    print("="*50)

    print("🌐 进入控制面板: https://justrunmy.app/panel")
    sb.open("https://justrunmy.app/panel")
    time.sleep(3)

    print("🖱️ 检查应用卡片...")
    try:
        # 等待带有 font-semibold 的 h3 标签加载
        sb.wait_for_element('h3.font-semibold', timeout=10)
    except Exception as e:
        print(f"❌ 找不到应用卡片: {e}")
        sb.save_screenshot("renew_app_not_found.png")
        send_tg_message("❌", "续期失败(找不到应用)", "未知", ipinfo, start_time)
        return False

    # 获取所有 h3.font-semibold 元素的数量
    app_count = sb.execute_script("return document.querySelectorAll('h3.font-semibold').length")
    print(f"📊 检测到 {app_count} 个应用需要续期")

    if app_count == 0:
        print("❌ 没有找到任何应用")
        send_tg_message("❌", "续期失败(没有应用)", "未知", ipinfo, start_time)
        return False

    # 记录续期结果
    results = []

    # 逐个处理每个应用
    for i in range(app_count):
        print(f"\n{'='*50}")
        print(f"   处理第 {i+1}/{app_count} 个应用")
        print(f"{'='*50}")

        # 返回到控制面板
        if i > 0:
            print("🔙 返回控制面板...")
            sb.open("https://justrunmy.app/panel")
            time.sleep(3)

        try:
            # 重新获取所有元素并点击第 i 个
            app_elements = sb.execute_script("return document.querySelectorAll('h3.font-semibold')")
            if i >= len(app_elements):
                print(f"⚠️ 应用数量已变化，跳过第 {i+1} 个")
                continue

            # 获取应用名称
            DYNAMIC_APP_NAME = sb.get_text(f'h3.font-semibold:nth-of-type({i+1})')
            print(f"🎯 应用名称: {DYNAMIC_APP_NAME}")

            # 点击该应用
            sb.click(f'h3.font-semibold:nth-of-type({i+1})')
            time.sleep(3)
            print(f"📍 成功进入应用详情页: {sb.get_current_url()}")
        except Exception as e:
            print(f"❌ 无法点击第 {i+1} 个应用: {e}")
            sb.save_screenshot(f"renew_app_{i+1}_click_fail.png")
            results.append(f"❌ {DYNAMIC_APP_NAME}")
            continue

        # 点击 Reset Timer 按钮
        print("🖱️ 点击 Reset Timer 按钮...")
        try:
            sb.click('button:contains("Reset Timer")')
            time.sleep(3)
        except Exception as e:
            print(f"❌ 找不到 Reset Timer 按钮: {e}")
            sb.save_screenshot(f"renew_app_{i+1}_reset_btn_not_found.png")
            results.append(f"❌ {DYNAMIC_APP_NAME}")
            continue

        # 检查续期弹窗内是否需要 CF 验证
        print("🛡️ 检查续期弹窗内是否需要 CF 验证...")
        if sb.execute_script(_EXISTS_JS):
            if not handle_turnstile(sb):
                print("❌ 弹窗内的 Turnstile 验证失败")
                sb.save_screenshot(f"renew_app_{i+1}_turnstile_fail.png")
                results.append(f"❌ {DYNAMIC_APP_NAME}")
                continue
        else:
            print("ℹ️ 弹窗内未检测到 Turnstile")

        # 点击 Just Reset 确认续期
        print("🖱️ 点击 Just Reset 确认续期...")
        try:
            sb.click('button:contains("Just Reset")')
            print("⏳ 提交续期请求，等待服务器处理...")
            time.sleep(5)
        except Exception as e:
            print(f"❌ 找不到 Just Reset 按钮: {e}")
            sb.save_screenshot(f"renew_app_{i+1}_just_reset_not_found.png")
            results.append(f"❌ {DYNAMIC_APP_NAME}")
            continue

        # 验证最终倒计时状态
        print("🔍 验证最终倒计时状态...")
        try:
            sb.refresh()
            time.sleep(4)
            timer_text = sb.get_text('span.font-mono.text-xl')
            print(f"⏱️ 当前应用剩余时间: {timer_text}")

            if "2 days 23" in timer_text or "3 days" in timer_text:
                print(f"✅ 应用 {i+1} 续期完成！")
                sb.save_screenshot(f"renew_app_{i+1}_success.png")
                results.append(f"✅ {DYNAMIC_APP_NAME} - {timer_text}")
            else:
                print(f"⚠️ 应用 {i+1} 倒计时似乎没有重置到最高值")
                sb.save_screenshot(f"renew_app_{i+1}_warning.png")
                results.append(f"⚠️ {DYNAMIC_APP_NAME} - {timer_text}")
        except Exception as e:
            print(f"⚠️ 读取应用 {i+1} 倒计时失败: {e}")
            sb.save_screenshot(f"renew_app_{i+1}_timer_read_fail.png")
            results.append(f"⚠️ {DYNAMIC_APP_NAME}")

    print("\n" + "="*50)
    print("   ✅ 所有应用续期流程完成")
    print("="*50)

    # 发送总结消息
    if results:
        summary = "\n".join(results)
        send_tg_message("📋", "续期总结", summary, ipinfo, start_time)

    return True

# ============================================================
#  脚本执行入口
# ============================================================
def main():
    print("=" * 50)
    print("   JustRunMy.app 自动登录与续期脚本")
    print("=" * 50)

    # 记录开始时间
    start_time = time.time()

    # 检查代理配置
    if HY2_PROXY_URL:
        print(f"📡 检测到代理配置: {HY2_PROXY_URL[:50]}...")
        # 启动代理
        proxy_manager, proxy_url = start_proxy_with_retry(max_retries=3)
    else:
        print("ℹ️ 未配置 HY2_PROXY_URL，将使用直连模式")
        proxy_manager, proxy_url = None, None

    sb_kwargs = {"uc": True, "test": True, "headless": False}

    if proxy_url:
        print(f"🔗 挂载 Hysteria2 代理: {proxy_url}")
        sb_kwargs["proxy"] = proxy_url
    else:
        print("🌐 使用直连访问")

    with SB(**sb_kwargs) as sb:
        print("✅ 浏览器已启动")

        # 获取 IP 信息
        ipinfo = "未知"
        try:
            sb.open("https://api.ipify.org/?format=json")
            import json as json_lib
            ip_json = sb.get_text('body')
            ip_data = json_lib.loads(ip_json)
            ipinfo = ip_data.get('ip', '未知')
            print(f"🌐 当前出口真实 IP: {ipinfo}")
        except Exception as e:
            print(f"⚠️ 获取 IP 信息失败: {e}")

        if login(sb):
            renew(sb, ipinfo, start_time)
        else:
            print("\n❌ 登录环节失败，终止后续续期操作。")
            send_tg_message("❌", "登录失败", "未知", ipinfo, start_time)

    if proxy_manager:
        proxy_manager.stop()

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
