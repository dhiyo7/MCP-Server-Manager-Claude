#!/usr/bin/env python3
"""
MCP Server Manager - Cross-platform desktop tool for managing claude_desktop_config.json
Supports Windows, Linux, and macOS.

Features:
- Auto-detect (and auto-create) claude_desktop_config.json per OS
- Add MCP by pasting JSON (single server config or full {"mcpServers": {...}} block)
- Remove MCP server
- Check MCP connection via stdio JSON-RPC initialize handshake
- Restart Claude Desktop so config reloads
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# === CONFIGURATION ===
CONFIG_FILENAME = "claude_desktop_config.json"

# Known paths per OS (in order of priority)
KNOWN_CONFIG_PATHS = {
    "windows": [
        os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"),
        os.path.expandvars(r"%LOCALAPPDATA%\AnthropicClaude\claude_desktop_config.json"),
    ],
    "linux": [
        os.path.expanduser("~/.config/Claude/claude_desktop_config.json"),  # official beta
        os.path.expanduser("~/.config/claude/claude_desktop_config.json"),  # community builds
        os.path.expanduser("~/.claude/claude_desktop_config.json"),
    ],
    "darwin": [
        os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"),
        os.path.expanduser("~/.claude/claude_desktop_config.json"),
    ],
}

# Path used when we must CREATE the config file (per OS)
DEFAULT_CREATE_PATH = {
    "windows": os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"),
    "linux": os.path.expanduser("~/.config/Claude/claude_desktop_config.json"),
    "darwin": os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json"),
}


def get_os_type():
    """Detect current operating system."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "darwin"
    return "linux"


def find_config_file():
    """Find claude_desktop_config.json on the system."""
    paths = KNOWN_CONFIG_PATHS.get(get_os_type(), [])
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def default_config_path():
    """Default path for this OS (used for creation)."""
    return DEFAULT_CREATE_PATH[get_os_type()]


def create_default_config(path=None):
    """Create a fresh config file (with empty mcpServers) at path."""
    path = path or default_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {}}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(path, data):
    """Save data to config file with timestamped backup."""
    backup_path = f"{path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if os.path.isfile(path):
        shutil.copy2(path, backup_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return backup_path


def parse_pasted_server(name, json_str):
    """Parse pasted JSON. Accepts:
    1. Single server config: {"command": "npx", "args": [...], "env": {...}}
    2. Full block: {"mcpServers": {"my-server": {...}}}
    Returns (name, entry) or raises ValueError with a friendly message."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON tidak valid: {e}")

    if not isinstance(data, dict):
        raise ValueError("JSON harus berupa object {}")

    if "mcpServers" in data:
        servers = data["mcpServers"]
        if not isinstance(servers, dict) or not servers:
            raise ValueError('"mcpServers" harus berisi minimal 1 server')
        if name and name in servers:
            entry = servers[name]
        elif len(servers) == 1:
            name, entry = next(iter(servers.items()))
        else:
            raise ValueError("Ada beberapa server dalam JSON. Isi kolom Name untuk memilih salah satu.")
    else:
        if not name:
            raise ValueError("Kolom Name wajib diisi untuk JSON single-server.")
        entry = data

    if not isinstance(entry, dict) or ("command" not in entry and "url" not in entry):
        raise ValueError('Server config harus memiliki "command" atau "url".')

    # Clean entry: keep only known fields
    clean = {}
    for key in ("command", "args", "env", "url", "headers", "type"):
        if key in entry:
            clean[key] = entry[key]
    return name, clean


def add_mcp_server(config, name, entry):
    mcp_servers = config.setdefault("mcpServers", {})
    if name in mcp_servers:
        return False, f"MCP server '{name}' sudah ada!"
    mcp_servers[name] = entry
    return True, f"MCP server '{name}' berhasil ditambahkan!"


def remove_mcp_server(config, name):
    mcp_servers = config.get("mcpServers", {})
    if name in mcp_servers:
        del mcp_servers[name]
        return True, f"MCP server '{name}' dihapus!"
    return False, f"MCP server '{name}' tidak ditemukan!"


# === MCP CONNECTION CHECK ===
INIT_REQUEST = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mcp-manager-check", "version": "1.0"},
    },
})


def check_mcp_connection(entry, timeout=60):
    """Run stdio initialize handshake against an MCP server. Returns (ok, detail)."""
    if "url" in entry:
        return False, (
            "Server remote (url) tidak bisa dicek langsung dari sini.\n"
            "Claude Desktop akan menghubunginya saat restart."
        )

    cmd = [entry["command"]] + list(entry.get("args", []))
    env = {**os.environ, **{str(k): str(v) for k, v in entry.get("env", {}).items()}}

    # 1. Pre-check: node/npx availability
    if cmd[0] == "npx" and not shutil.which("npx"):
        return False, (
            "npx tidak ditemukan.\n"
            "Pastikan Node.js (>=18) sudah terinstall.\n"
            "Download: https://nodejs.org"
        )
    if cmd[0] == "node" and not shutil.which("node"):
        return False, (
            "node tidak ditemukan.\n"
            "Pastikan Node.js (>=18) sudah terinstall.\n"
            "Download: https://nodejs.org"
        )
    if shutil.which("node") is None and not shutil.which("npx"):
        return False, (
            "Node.js tidak ditemukan.\n"
            "Pastikan Node.js (>=18) sudah terinstall.\n"
            "Download: https://nodejs.org"
        )

    # 2. Add -y to npx commands if missing to avoid interactive prompts
    final_cmd = list(cmd)
    if final_cmd[0] == "npx" and "-y" not in final_cmd:
        final_cmd.insert(1, "-y")

    try:
        # ponytail: Use shell=True for better command discovery (npx, node, etc.)
        kwargs = {"shell": True} if sys.platform == "win32" else {"shell": False}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            # On windows, join cmd list into a string for shell=True
            cmd_str = subprocess.list2cmdline(final_cmd)
        else:
            cmd_str = final_cmd

        proc = subprocess.Popen(
            cmd_str, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, **kwargs
        )
    except FileNotFoundError:
        return False, f"Command tidak ditemukan: {final_cmd[0]}\n(Install dulu / cek path)"
    except Exception as e:
        return False, f"Gagal menjalankan: {e}"

    # 3. Polling loop: monitor stderr early for server errors, then send init
    import time
    import select
    response_data = b""
    err_data = b""
    request_sent = False
    start = time.time()
    deadline = start + timeout

    try:
        while time.time() < deadline:
            # Check if process died
            if proc.poll() is not None and not request_sent:
                break

            # Read stderr continuously to catch server errors early
            try:
                if sys.platform != "win32":
                    ready, _, _ = select.select([proc.stderr], [], [], 0.1)
                    if ready:
                        chunk = proc.stderr.read1(4096)
                        if chunk:
                            err_data += chunk
                            err_peek = err_data.decode(errors="replace").lower()
                            if any(p in err_peek for p in ("sdkhttperror", "internal server error",
                                                           "500", "connection refused", "econnrefused",
                                                           "unauthorized", "401", "403", "handshake failed")):
                                proc.kill()
                                err_full = err_data.decode(errors="replace")
                                return False, (
                                    f"Server remote mengembalikan error:\n\n{err_full[-2000:]}\n\n"
                                    f"Ini masalah server/token/authorization, bukan koneksi lokal."
                                )
                else:
                    try:
                        chunk = proc.stderr.read(4096)
                        if chunk:
                            err_data += chunk
                    except Exception:
                        pass
            except Exception:
                pass

            # Try to send request after a short delay (let npx start up)
            if not request_sent:
                time.sleep(0.3)
                try:
                    proc.stdin.write((INIT_REQUEST + "\n").encode())
                    proc.stdin.flush()
                    request_sent = True
                except (BrokenPipeError, OSError):
                    break

            # Read stdout (non-blocking on POSIX, best-effort on Windows)
            try:
                if sys.platform != "win32":
                    ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                    if ready:
                        chunk = proc.stdout.read1(4096)
                        if chunk:
                            response_data += chunk
                else:
                    # Windows: read with timeout via communicate-like approach
                    try:
                        chunk = proc.stdout.read(4096)
                        if chunk:
                            response_data += chunk
                    except Exception:
                        pass
            except Exception:
                pass

            # Check for complete JSON-RPC response
            output = response_data.decode(errors="replace")
            for line in output.splitlines():
                line = line.strip()
                if not (line.startswith("{") and line.endswith("}")):
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == 1:
                    if "result" in resp:
                        info = resp["result"].get("serverInfo", {})
                        name_ver = f"{info.get('name', '?')} v{info.get('version', '?')}"
                        proc.kill()
                        return True, f"TERHUBUNG — server: {name_ver}"
                    if "error" in resp:
                        proc.kill()
                        return False, f"Server menolak handshake: {resp['error'].get('message', resp['error'])}"

            time.sleep(0.2)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    # If we got here, no valid response received
    try:
        stdout, stderr = proc.communicate(timeout=5)
        response_data += stdout
        err_data += stderr
    except Exception:
        pass

    err = err_data.decode(errors="replace").strip()
    output = response_data.decode(errors="replace")
    detail = f"Tidak ada respons JSON-RPC.\nExit code: {proc.returncode}"
    if err:
        detail += f"\n\nStderr (last 2000 chars):\n{err[-2000:]}"

    err_lower = err.lower()
    if any(p in err_lower for p in ("sdkhttperror", "internal server error", "500",
                                    "connection refused", "econnrefused",
                                    "unauthorized", "401", "403", "handshake failed")):
        detail += "\n\n⚠ MASALAH SERVER REMOTE: Server/token/authorization bermasalah.\n" \
                  "Cek token di env var, atau coba akses URL manual di browser."
    elif "npm" in err_lower or "enoent" in err_lower or "download" in err_lower:
        detail += "\n\nHint: package sedang di-download (first run). Coba lagi dalam 10-30 detik."
    elif "npx" in output.lower():
        detail += "\n\nHint: npx mungkin masih download package. Tunggu 10-30 detik lalu coba lagi."
    elif proc.returncode != 0:
        detail += "\n\nProses exit dengan error. Cek command/args/env di config."
    return False, detail


# === CLAUDE DESKTOP RESTART ===
def find_claude_executable():
    if sys.platform == "win32":
        for p in (
            os.path.expandvars(r"%LOCALAPPDATA%\AnthropicClaude\claude.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Claude\Claude.exe"),
            r"C:\Program Files\Claude\Claude.exe",
        ):
            if os.path.isfile(p):
                return p
        return None
    if sys.platform == "darwin":
        return "/Applications/Claude.app"
    return shutil.which("claude-desktop") or shutil.which("claude")


def quit_claude_desktop():
    """Ask Claude Desktop to quit gracefully. Returns True if a quit command ran."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/IM", "Claude.exe"], capture_output=True, timeout=15)
            return True
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e", 'tell application "Claude" to quit'],
                           capture_output=True, timeout=15)
            return True
        # Linux: try graceful TERM on known process names
        for proc_name in ("claude-desktop", "Claude"):
            r = subprocess.run(["pgrep", "-f", proc_name], capture_output=True)
            if r.returncode == 0:
                subprocess.run(["pkill", "-f", proc_name], capture_output=True, timeout=15)
                return True
        return False
    except Exception:
        return False


def launch_claude_desktop():
    exe = find_claude_executable()
    if not exe:
        return False, "Executable Claude Desktop tidak ditemukan. Silakan buka manual."
    try:
        if sys.platform == "win32":
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", exe])
        else:
            subprocess.Popen([exe], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, f"Claude Desktop dijalankan: {exe}"
    except Exception as e:
        return False, f"Gagal menjalankan: {e}"


# === GUI ===
class MCPManagerApp:
    def __init__(self):
        self.config_path = None
        self.config_data = None

        self.root = tk.Tk()
        self.root.title("MCP Server Manager")
        self.root.geometry("780x720")
        self.root.resizable(True, True)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        self.build_ui()
        self.status_var.set("Klik 'Auto-Detect' untuk mencari / membuat config.")

    def build_ui(self):
        # === Top: Config Path ===
        top = ttk.Frame(self.root, padding=(10, 5))
        top.pack(fill="x")

        ttk.Label(top, text="Config File:", font=("Arial", 10, "bold")).pack(side="left")
        self.path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.path_var).pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(top, text="Auto-Detect", command=self.auto_detect).pack(side="left")
        ttk.Button(top, text="Browse...", command=self.browse_config).pack(side="left", padx=5)

        # === Server List ===
        list_frame = ttk.LabelFrame(self.root, text="Existing MCP Servers", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("name", "command", "type")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=7)
        self.tree.heading("name", text="Name")
        self.tree.heading("command", text="Command / URL")
        self.tree.heading("type", text="Type")
        self.tree.column("name", width=180)
        self.tree.column("command", width=360)
        self.tree.column("type", width=90)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        tree_btn = ttk.Frame(list_frame)
        tree_btn.pack(fill="x", pady=(8, 0))
        ttk.Button(tree_btn, text="Check Connection", command=self.check_selected).pack(side="left")
        ttk.Button(tree_btn, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=5)

        # === Add Form: Name + JSON ===
        add_frame = ttk.LabelFrame(
            self.root, text="Add MCP Server (paste JSON dari dokumentasi, ganti token)", padding=10)
        add_frame.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Label(add_frame, text="Name:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.name_var = tk.StringVar()
        ttk.Entry(add_frame, textvariable=self.name_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=5, pady=2)
        ttk.Label(add_frame, text="(opsional jika JSON sudah berisi nama)").grid(
            row=0, column=2, sticky="w")

        ttk.Label(add_frame, text="Server JSON:").grid(row=1, column=0, sticky="nw", padx=5, pady=2)
        self.json_text = tk.Text(add_frame, height=8, width=60, wrap="word",
                                 font=("Consolas" if sys.platform == "win32" else "Monospace", 10))
        self.json_text.grid(row=1, column=1, columnspan=2, sticky="nsew", padx=5, pady=2)

        add_frame.columnconfigure(1, weight=1)
        add_frame.rowconfigure(1, weight=1)

        btn_frame = ttk.Frame(add_frame)
        btn_frame.grid(row=2, column=0, columnspan=3, pady=8)
        ttk.Button(btn_frame, text="Add MCP", command=self.add_mcp).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Clear Form", command=self.clear_form).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Restart Claude Desktop", command=self.restart_claude).pack(
            side="left", padx=25)

        # === Status Bar ===
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom", padx=10, pady=5)

    # --- Config loading ---
    def load_config_from_path(self, path):
        if not path:
            return False
        self.config_path = path
        self.path_var.set(path)
        try:
            self.config_data = load_config(path)
            self.refresh_server_list()
            self.status_var.set(f"Loaded: {path}")
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Gagal memuat config:\n{e}")
            return False

    def browse_config(self):
        path = filedialog.askopenfilename(
            title="Select claude_desktop_config.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.load_config_from_path(path)

    def auto_detect(self):
        path = find_config_file()
        if path:
            self.load_config_from_path(path)
            return
        # Not found: offer to create at default location
        default = default_config_path()
        if messagebox.askyesno(
                "Config Tidak Ditemukan",
                f"claude_desktop_config.json tidak ditemukan.\n\n"
                f"Buat file baru di:\n{default}\n\n(Claude Desktop harus sudah ter-install)"):
            try:
                created = create_default_config(default)
                self.load_config_from_path(created)
                messagebox.showinfo("Sukses", f"Config dibuat:\n{created}")
            except Exception as e:
                messagebox.showerror("Error", f"Gagal membuat config:\n{e}")

    # --- Server list ---
    def refresh_server_list(self):
        self.tree.delete(*self.tree.get_children())
        for name, entry in self.config_data.get("mcpServers", {}).items():
            if "url" in entry:
                cmd, typ = entry["url"], "remote"
            else:
                cmd, typ = entry.get("command", ""), "stdio"
            self.tree.insert("", "end", values=(name, cmd, typ))

    def _selected_name(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Pilih Server", "Pilih salah satu MCP server di daftar dulu.")
            return None
        return str(self.tree.item(selection[0])["values"][0])

    # --- Add / Remove ---
    def add_mcp(self):
        if not self.config_data:
            messagebox.showwarning("No Config", "Load config dulu (Auto-Detect atau Browse).")
            return
        name = self.name_var.get().strip()
        json_str = self.json_text.get("1.0", "end").strip()
        if not json_str:
            messagebox.showwarning("Validation", "Server JSON wajib diisi.")
            return
        try:
            name, entry = parse_pasted_server(name, json_str)
        except ValueError as e:
            messagebox.showwarning("JSON Error", str(e))
            return

        success, msg = add_mcp_server(self.config_data, name, entry)
        if not success:
            if not messagebox.askyesno("Duplicate", msg + "\n\nTimpa yang lama?"):
                return
            self.config_data["mcpServers"][name] = entry
        try:
            backup = save_config(self.config_path, self.config_data)
        except Exception as e:
            messagebox.showerror("Error", f"Gagal menyimpan config:\n{e}")
            return
        self.refresh_server_list()
        self.clear_form()
        self.status_var.set(f"Added '{name}' | Backup: {os.path.basename(backup)}")
        if messagebox.askyesno(
                "Sukses",
                f"MCP '{name}' ditambahkan!\n\n"
                f"Backup: {backup}\n\n"
                f"Cek koneksi sekarang?"):
            self.check_server(name)

    def remove_selected(self):
        if not self.config_data:
            return
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno("Confirm", f"Hapus MCP server '{name}'?"):
            success, msg = remove_mcp_server(self.config_data, name)
            if success:
                try:
                    backup = save_config(self.config_path, self.config_data)
                    self.refresh_server_list()
                    self.status_var.set(f"Removed '{name}' | Backup: {os.path.basename(backup)}")
                except Exception as e:
                    messagebox.showerror("Error", f"Gagal menyimpan config:\n{e}")

    # --- Connection check ---
    def check_selected(self):
        if not self.config_data:
            return
        name = self._selected_name()
        if name:
            self.check_server(name)

    def check_server(self, name):
        entry = self.config_data.get("mcpServers", {}).get(name)
        if not entry:
            messagebox.showwarning("Not Found", f"Server '{name}' tidak ada di config.")
            return
        self.status_var.set(f"Checking '{name}' ... (mohon tunggu, first-run npx bisa lama)")
        self.root.config(cursor="watch")

        def worker():
            ok, detail = check_mcp_connection(entry)
            self.root.after(0, lambda: self._check_done(name, ok, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _check_done(self, name, ok, detail):
        self.root.config(cursor="")
        status = "TERSAMBUNG" if ok else "GAGAL"
        self.status_var.set(f"[{name}] {status}")
        (messagebox.showinfo if ok else messagebox.showerror)(
            f"Connection Check: {status}", f"MCP: {name}\n\n{detail}")

    # --- Restart Claude Desktop ---
    def restart_claude(self):
        if not messagebox.askyesno(
                "Restart Claude Desktop",
                "Config hanya dimuat ulang setelah Claude Desktop direstart.\n\n"
                "Quit dan jalankan ulang Claude Desktop sekarang?"):
            return
        quit_cmd_ran = quit_claude_desktop()
        if quit_cmd_ran:
            self.root.after(3000, self._launch_claude)  # give it time to fully quit
        else:
            self._launch_claude()

    def _launch_claude(self):
        ok, msg = launch_claude_desktop()
        self.status_var.set(msg)
        if not ok:
            messagebox.showwarning("Restart", msg)

    def clear_form(self):
        self.name_var.set("")
        self.json_text.delete("1.0", "end")

    def run(self):
        self.root.mainloop()


def main():
    try:
        app = MCPManagerApp()
        app.run()
    except tk.TclError as e:
        print(f"GUI error: {e}\nFalling back to CLI mode...")
        cli_mode()


def cli_mode():
    print("\nCLI Mode - MCP Server Manager")
    config_path = find_config_file()
    if not config_path:
        ans = input(f"Config tidak ditemukan. Buat baru di {default_config_path()}? [y/N] ").strip().lower()
        if ans == "y":
            config_path = create_default_config()
        else:
            sys.exit(1)
    config = load_config(config_path)
    print(f"Loaded: {config_path}")

    while True:
        print(f"\nServers: {list(config.get('mcpServers', {}).keys())}")
        print("1. Add MCP (paste JSON)  2. Remove MCP  3. Check connection  4. Exit")
        choice = input("Choice: ").strip()

        if choice == "1":
            name = input("Name (opsional): ").strip()
            print("Paste server JSON (lalu Enter, Ctrl-D untuk selesai):")
            lines = []
            try:
                while True:
                    lines.append(input())
            except EOFError:
                pass
            try:
                name, entry = parse_pasted_server(name, "\n".join(lines))
                ok, msg = add_mcp_server(config, name, entry)
                print(msg)
                if ok or input("Timpa? [y/N] ").strip().lower() == "y":
                    config.setdefault("mcpServers", {})[name] = entry
                    print("Backup:", save_config(config_path, config))
            except ValueError as e:
                print(f"Error: {e}")

        elif choice == "2":
            name = input("Name to remove: ").strip()
            ok, msg = remove_mcp_server(config, name)
            print(msg)
            if ok:
                print("Backup:", save_config(config_path, config))

        elif choice == "3":
            name = input("Name to check: ").strip()
            entry = config.get("mcpServers", {}).get(name)
            if not entry:
                print("Server tidak ditemukan.")
            else:
                ok, detail = check_mcp_connection(entry)
                print(("TERSAMBUNG" if ok else "GAGAL"), "-", detail)

        elif choice == "4":
            break


if __name__ == "__main__":
    main()
