"""Arylic / Linkplay Wi-Fi provisioning utility (Python 3, standard library only)."""
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen


def decode_ssid(value):
    """Arylic scan results contain UTF-8 SSID bytes represented as hex."""
    try:
        return bytes.fromhex(str(value)).decode('utf-8', errors='replace')
    except ValueError:
        return str(value)


def hex_text(value):
    return value.encode('utf-8').hex().upper()


class ArylicApp:
    def __init__(self, root):
        self.root = root
        root.title('Arylic Wi-Fi Setup')
        root.geometry('720x475')
        root.minsize(590, 400)
        self.host = tk.StringVar(value='10.10.10.254')
        self.password = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='Enter the amplifier IP address, then scan.')
        self.networks = []
        self.events = queue.Queue()
        self.busy = False

        frame = ttk.Frame(root, padding=15)
        frame.pack(fill='both', expand=True)
        top = ttk.Frame(frame)
        top.pack(fill='x', pady=(0, 10))
        ttk.Label(top, text='Amplifier IP / hostname:').pack(side='left')
        ttk.Entry(top, textvariable=self.host, width=23).pack(side='left', padx=8)
        self.scan_button = ttk.Button(top, text='Scan Wi-Fi networks', command=self.scan)
        self.scan_button.pack(side='left')

        ttk.Label(frame, text='Nearby Wi-Fi networks (as seen by the amplifier):').pack(anchor='w')
        listing = ttk.Frame(frame)
        listing.pack(fill='both', expand=True, pady=(5, 10))
        self.tree = ttk.Treeview(listing, columns=('ssid', 'signal', 'security', 'channel'), show='headings', selectmode='browse')
        for col, title, width in [('ssid', 'SSID', 290), ('signal', 'Signal', 90), ('security', 'Security', 120), ('channel', 'Channel', 75)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=65, stretch=(col == 'ssid'))
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        pwrow = ttk.Frame(frame)
        pwrow.pack(fill='x', pady=(2, 10))
        ttk.Label(pwrow, text='Wi-Fi password:').pack(side='left')
        self.pw_entry = ttk.Entry(pwrow, textvariable=self.password, show='*', width=35)
        self.pw_entry.pack(side='left', padx=8, fill='x', expand=True)
        ttk.Checkbutton(pwrow, text='Show', variable=self.show_password, command=self.toggle_password).pack(side='left')

        action = ttk.Frame(frame)
        action.pack(fill='x')
        self.connect_button = ttk.Button(action, text='Connect amplifier', command=self.connect, state='disabled')
        self.connect_button.pack(side='left')
        self.check_button = ttk.Button(action, text='Check status', command=self.check_status)
        self.check_button.pack(side='left', padx=8)
        ttk.Label(frame, textvariable=self.status, wraplength=660).pack(anchor='w', pady=(16, 5))
        ttk.Label(frame, text='Note: The amplifier may disconnect from its hotspot when joining your Wi-Fi.', foreground='gray').pack(anchor='w')
        root.after(100, self.process_events)

    def toggle_password(self):
        self.pw_entry.configure(show='' if self.show_password.get() else '*')

    def on_select(self, _event=None):
        self.connect_button.configure(state='normal' if self.tree.selection() and not self.busy else 'disabled')

    def set_busy(self, busy):
        self.busy = busy
        self.scan_button.configure(state='disabled' if busy else 'normal')
        self.check_button.configure(state='disabled' if busy else 'normal')
        self.on_select()

    def request(self, host, command, timeout=5):
        host = host.strip().removeprefix('http://').removeprefix('https://').rstrip('/')
        if not host or '/' in host or '?' in host or '#' in host or '@' in host:
            raise ValueError('Enter only the device IP address or hostname, optionally with a port.')
        # Keep the API command's colon and equals signs; encode any other special characters.
        url = 'http://' + host + '/httpapi.asp?command=' + quote(command, safe=':=')
        with urlopen(url, timeout=timeout) as reply:
            return reply.read().decode('utf-8', errors='replace').strip()

    def start_task(self, function, *args):
        if self.busy:
            return
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(function, args), daemon=True).start()

    def worker(self, function, args):
        try:
            function(*args)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
            self.events.put(('error', str(error)))
        except Exception as error:
            self.events.put(('error', str(error)))
        finally:
            self.events.put(('done', None))

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'status':
                    self.status.set(value)
                elif kind == 'networks':
                    self.networks = value
                    self.tree.delete(*self.tree.get_children())
                    for i, ap in enumerate(value):
                        self.tree.insert('', 'end', iid=str(i), values=(
                            decode_ssid(ap.get('ssid', '')) or '(hidden SSID)',
                            str(ap.get('rssi', '?')) + '%',
                            ap.get('auth', '?') + (' / ' + ap['encry'] if ap.get('encry') else ''),
                            ap.get('channel', '?')))
                elif kind == 'error':
                    self.status.set('Error: ' + value)
                    messagebox.showerror('Arylic Wi-Fi Setup', value)
                elif kind == 'done':
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def scan(self):
        self.status.set('Requesting a Wi-Fi scan from the amplifier...')
        self.start_task(self.scan_worker, self.host.get())

    def scan_worker(self, host):
        result = json.loads(self.request(host, 'wlanGetApListEx', timeout=12))
        networks = result.get('aplist', [])
        if not isinstance(networks, list):
            raise ValueError('Unexpected access-point list format.')
        self.events.put(('networks', networks))
        self.events.put(('status', f'Found {len(networks)} Wi-Fi access point(s). Select one below.'))

    def connect(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning('Select Wi-Fi', 'Select a Wi-Fi network first.')
            return
        ap = self.networks[int(selection[0])]
        ssid = decode_ssid(ap.get('ssid', ''))
        if not ssid:
            messagebox.showerror('Hidden Wi-Fi', 'This network has no SSID. The hidden-network command is needed.')
            return
        auth_raw = str(ap.get('auth', '')).upper()
        encry_raw = str(ap.get('encry', '')).upper()
        if auth_raw in ('OPEN', 'NONE'):
            auth, encry = 'OPEN', 'NONE'
        elif auth_raw in ('WPA2PSK', 'WPA2-PSK', 'WPA2_PSK', 'AES'):
            auth, encry = 'WPA2PSK', 'AES' if not encry_raw else encry_raw
        else:
            messagebox.showerror('Unsupported Wi-Fi security',
                                 f'AP reports auth={auth_raw}, encryption={encry_raw}.\n'
                                 'This security mode is not documented for wlanConnectApEx.')
            return
        if encry not in ('AES', 'NONE'):
            messagebox.showerror('Unsupported encryption', f'Unsupported encryption: {encry}')
            return
        password = self.password.get()
        if auth != 'OPEN' and not (8 <= len(password.encode('utf-8')) <= 63):
            messagebox.showwarning('Password', 'Enter a Wi-Fi password of 8–63 UTF-8 bytes.')
            return
        try:
            channel = int(ap.get('channel', 0))
        except (TypeError, ValueError):
            channel = 0
        if not (1 <= channel <= 12):
            messagebox.showerror('Unsupported channel',
                                 'Arylic documents channels 1–12 for this command. '
                                 f'The selected AP reports channel {channel}.')
            return
        if not messagebox.askyesno('Confirm connection',
                                    f'Connect the amplifier to "{ssid}"?\n\n'
                                    'The amplifier may change its IP and become temporarily unreachable.'):
            return
        cmd = (f'wlanConnectApEx:ssid={ap["ssid"]}:ch={channel}:auth={auth}:'
               f'encry={encry}:pwd={hex_text(password) if auth != "OPEN" else ""}:chext=1')
        self.status.set(f'Connecting amplifier to {ssid}...')
        self.start_task(self.connect_worker, self.host.get(), cmd)

    def connect_worker(self, host, cmd):
        try:
            self.request(host, cmd, timeout=5)
        except (OSError, URLError):
            # The provisioning endpoint may close the connection as Wi-Fi reconfigures.
            self.events.put(('status', 'Command sent; the amplifier dropped its connection. Checking status...'))
        for _ in range(8):
            time.sleep(2)
            try:
                result = self.request(host, 'wlanGetConnectState', timeout=3).strip().upper()
            except (OSError, URLError):
                self.events.put(('status', 'Amplifier unreachable after setup. It may have switched to your Wi-Fi. Check your router for its new IP.'))
                return
            if result == 'OK':
                self.events.put(('status', 'Success: amplifier reports Wi-Fi connected (OK).'))
                return
            if result == 'PAIRFAIL':
                self.events.put(('status', 'Connection failed: amplifier reports incorrect password (PAIRFAIL).'))
                return
            if result == 'FAIL':
                self.events.put(('status', 'Amplifier reports FAIL (connection failure, or Ethernet is connected).'))
                return
            self.events.put(('status', f'Amplifier status: {result or "unknown"}. Waiting...'))
        self.events.put(('status', 'No final confirmation yet. Try Check status, or find the amplifier on your router.'))

    def check_status(self):
        self.start_task(self.status_worker, self.host.get())

    def status_worker(self, host):
        result = self.request(host, 'wlanGetConnectState')
        meanings = {'OK': 'Connected', 'PROCESS': 'Connecting',
                    'PAIRFAIL': 'Incorrect password', 'FAIL': 'Not connected (or connected by Ethernet)'}
        self.events.put(('status', f'Wi-Fi status: {result} — {meanings.get(result.upper(), "Unknown state")}'))


if __name__ == '__main__':
    main = tk.Tk()
    ArylicApp(main)
    main.mainloop()
