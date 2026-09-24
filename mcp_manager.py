#!/usr/bin/env python3
"""
MCP Server Manager - Modern dark-themed desktop tool
Theme: Forest Green (#16a34a accent, #0f172a surface, #1e293b elevated)
Supports Windows, Linux, macOS. Features:
- Auto-detect/create claude_desktop_config.json per OS
- Add/remove MCP server by pasting JSON
- Check MCP stdio JSON-RPC connection
- Restart Claude Desktop
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import platform
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

# === THEME COLORS (Forest Green Modern) ===
BG_COLOR       = "#0f172a"   # background gelap
SURFACE_COLOR  = "#1e293b"   # card/container
ELEVATED_COLOR = "#273449"   # elevated card
TEXT_COLOR     = "#f1f5f9"
MUTED_COLOR    = "#94a3b8"
ACCENT_COLOR   = "#16a34a"   # hijau toska
ACCENT_HOVER   = "#15803d"
ACCENT_LIGHT   = "#4ade80"
SUCCESS_COLOR  = "#22c55e"
ERROR_COLOR    = "#ef4444"
BORDER_COLOR   = "#334155"

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
    """Find claude_desktop_config.json on the system with deep search for Windows."""
    os_type = get_os_type()
    paths = KNOWN_CONFIG_PATHS.get(os_type, [])
    
    # 1. Check known paths first
    for path in paths:
        if os.path.isfile(path):
            return path
            
    # 2. Windows Deep Search: cover standard + MSIX (Store) install
    if os_type == "windows":
        local_appdata = os.path.expandvars(r"%LOCALAPPDATA%")
        appdata = os.path.expandvars(r"%APPDATA%")
        keywords = ["Claude", "AnthropicClaude", "claude-desktop"]
        
        # MSIX/Store path: Packages\Claude_xxx\LocalCache\Roaming\Claude\
        packages_dir = os.path.join(local_appdata, "Packages")
        if os.path.isdir(packages_dir):
            for folder in os.listdir(packages_dir):
                if folder.lower().startswith("claude"):
                    msix_path = os.path.join(
                        packages_dir, folder, "LocalCache", "Roaming", "Claude", CONFIG_FILENAME
                    )
                    if os.path.isfile(msix_path):
                        return msix_path
        
        # General deep search (max depth 4)
        for root in [appdata, local_appdata]:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath.replace(root, "").count(os.sep)
                if depth >= 4:
                    dirnames.clear()
                    continue
                if CONFIG_FILENAME in filenames:
                    return os.path.join(dirpath, CONFIG_FILENAME)
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


def install_nodejs_silent(status_callback=None):
    """Download and silently install Node.js (Windows/macOS/Linux). Returns (bool, message)."""
    os_type = get_os_type()

    def update_status(text):
        if status_callback:
            status_callback(text)

    if os_type == "windows":
        import urllib.request
        update_status("Mendownload Node.js installer (Windows)...")
        arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE") == "ARM64" else "x64"
        msi_url = f"https://nodejs.org/dist/v22.16.0/node-v22.16.0-win-{arch}.msi"
        msi_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "nodejs_install.msi")

        try:
            urllib.request.urlretrieve(msi_url, msi_path)
            update_status("Menginstall Node.js secara silent (mohon tunggu)...")
            cmd = f'msiexec.exe /i "{msi_path}" /quiet /norestart'
            res = subprocess.run(cmd, shell=True, capture_output=True, timeout=120)
            if os.path.exists(msi_path):
                os.remove(msi_path)
            if res.returncode == 0:
                return True, "Node.js berhasil terinstall secara otomatis!\nSilakan restart aplikasi jika command belum terbaca."
            # MSI failed — offer portable zip as fallback
            update_status("MSI gagal, coba portable Node.js...")
            return _install_node_portable_windows(update_status)
        except Exception as e:
            # Try portable fallback on error
            update_status(f"Fallback: portable Node.js...")
            return _install_node_portable_windows(update_status)

    elif os_type == "darwin":
        update_status("Mengecek Homebrew...")
        try:
            if shutil.which("brew"):
                update_status("Menginstall Node.js via Homebrew...")
                res = subprocess.run(["brew", "install", "node"], capture_output=True, text=True, timeout=180)
                if res.returncode == 0:
                    return True, "Node.js berhasil terinstall via Homebrew!"
                # brew install failed — offer direct download
                update_status("Homebrew gagal, coba portable Node.js...")
                return _install_node_portable_darwin(update_status)
            # No brew — try direct download
            update_status("Homebrew tidak ditemukan, coba portable Node.js...")
            return _install_node_portable_darwin(update_status)
        except Exception as e:
            return False, f"Error: {e}"

    else:
        update_status("Mendeteksi package manager Linux...")
        try:
            if shutil.which("apt-get"):
                pkg_mgr = "apt-get"
                cmd = "sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm"
            elif shutil.which("dnf"):
                pkg_mgr = "dnf"
                cmd = "sudo dnf install -y nodejs npm"
            elif shutil.which("pacman"):
                pkg_mgr = "pacman"
                cmd = "sudo pacman -Sy --noconfirm nodejs npm"
            elif shutil.which("zypper"):
                pkg_mgr = "zypper"
                cmd = "sudo zypper install -y nodejs npm"
            else:
                return False, "Package manager tidak terdeteksi. Install Node.js manual dari https://nodejs.org"

            update_status(f"Menginstall Node.js via {pkg_mgr}...")
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                return False, f"Instalasi {pkg_mgr} timeout. Coba install manual."

            if res.returncode == 0:
                return True, f"Node.js berhasil terinstall via {pkg_mgr}!"

            # If sudo failed (likely password prompt or no tty), offer pkexec path
            err_lower = (res.stderr + res.stdout).lower()
            if "password" in err_lower or "tty" in err_lower or "sudo" in err_lower:
                return False, (
                    f"Instalasi {pkg_mgr} memerlukan password sudo.\n\n"
                    f"Jalankan di terminal:\n{sudo_cmd_for(pkg_mgr)}\n\n"
                    f"Atau install Node.js manual: https://nodejs.org"
                )

            return False, f"Gagal install via {pkg_mgr}:\n{res.stderr.strip()}"
        except Exception as e:
            return False, f"Error: {e}"


def sudo_cmd_for(pkg_mgr):
    """Return the sudo command to run manually in a terminal."""
    if pkg_mgr == "apt-get":
        return "sudo apt-get update && sudo apt-get install -y nodejs npm"
    elif pkg_mgr == "dnf":
        return "sudo dnf install -y nodejs npm"
    elif pkg_mgr == "pacman":
        return "sudo pacman -Sy nodejs npm"
    elif pkg_mgr == "zypper":
        return "sudo zypper install -y nodejs npm"
    return "sudo apt-get install -y nodejs npm"


def install_nodejs_with_pkexec(pkg_mgr):
    """Try installing Node.js via pkexec (Linux, no password prompt in GUI).
    Returns (bool, message)."""
    cmd_map = {
        "apt-get": "sudo apt-get update -qq && sudo apt-get install -y -qq nodejs npm",
        "dnf": "sudo dnf install -y nodejs npm",
        "pacman": "sudo pacman -Sy --noconfirm nodejs npm",
        "zypper": "sudo zypper install -y nodejs npm",
    }
    if pkg_mgr not in cmd_map:
        return False, f"Package manager '{pkg_mgr}' tidak didukung oleh pkexec."
    try:
        res = subprocess.run(
            ["pkexec", "bash", "-c", cmd_map[pkg_mgr]],
            capture_output=True, text=True, timeout=180
        )
        if res.returncode == 0:
            return True, f"Node.js berhasil terinstall via pkexec!"
        return False, f"pkexec install gagal:\n{res.stderr.strip()}"
    except FileNotFoundError:
        return False, "pkexec tidak ditemukan. Install dengan: sudo apt-get install pkexec"
    except subprocess.TimeoutExpired:
        return False, "Instalasi pkexec timeout."
    except Exception as e:
        return False, f"pkexec error: {e}"


def _install_node_portable_windows(update_status=None):
    """Install portable Node.js in the user's temp directory if MSI fails."""
    import urllib.request

    arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE") == "ARM64" else "x64"
    version = "22.16.0"
    zip_url = f"https://nodejs.org/dist/v{version}/node-v{version}-win-{arch}.zip"
    zip_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), f"node-v{version}-win-{arch}.zip")
    extract_dir = os.path.join(os.environ.get("TEMP", "C:\\Temp"), f"node-v{version}-win-{arch}")

    try:
        update_status("Mendownload Node.js portable...")
        urllib.request.urlretrieve(zip_url, zip_path)
        import zipfile
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        os.remove(zip_path)
        return True, f"Node.js portable berhasil diinstal di: {extract_dir}\nTambahkan folder bin ke PATH."
    except Exception as e:
        return False, f"Portable Node.js gagal diinstal: {e}"


def _install_node_portable_darwin(update_status=None):
    """Install portable Node.js in the user's temp directory if Homebrew fails."""
    import urllib.request
    import tarfile

    machine = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    version = "22.16.0"
    url = f"https://nodejs.org/dist/v{version}/node-v{version}-darwin-{machine}.tar.xz"
    tar_path = os.path.join(os.path.expanduser("~"), f"node-v{version}-darwin-{machine}.tar.xz")

    try:
        update_status("Mendownload Node.js portable...")
        urllib.request.urlretrieve(url, tar_path)
        extract_dir = os.path.join(os.path.expanduser("~"), f"node-v{version}-darwin-{machine}")
        os.makedirs(extract_dir, exist_ok=True)
        with tarfile.open(tar_path, "r:xz") as archive:
            archive.extractall(extract_dir)
        os.remove(tar_path)
        return True, f"Node.js portable berhasil diinstal di: {extract_dir}\nTambahkan folder bin ke PATH."
    except Exception as e:
        return False, f"Portable Node.js gagal diinstal: {e}"


def refresh_path_for_new_node():
    """Clear cached PATH lookups so node/npx changes are picked up in this process."""
    import importlib
    if sys.platform == "win32":
        # Re-read PATH from parent registry (Windows resolves node path here)
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.advapi32.GetEnvironmentVariableW("PATH", buf, 32768)
            if size > 0 and size <= 32768:
                os.environ["PATH"] = buf.value
        except Exception:
            pass
    # On all platforms, re-expand env vars
    os.environ["PATH"] = os.path.expandvars(os.environ["PATH"])
    # bust any cached executable lookups (Python 3.10+)
    if hasattr(shutil, "which"):
        shutil.which.cache_clear() if hasattr(shutil.which, "cache_clear") else None


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
    node_missing = False
    if cmd[0] == "npx" and not shutil.which("npx"):
        node_missing = True
    elif cmd[0] == "node" and not shutil.which("node"):
        node_missing = True

    if node_missing:
        return False, "NODE_MISSING"

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

    # Set non-blocking on pipes so we never hang
    if sys.platform != "win32":
        import fcntl
        for pipe in (proc.stdout, proc.stderr):
            if pipe and hasattr(pipe, "fileno"):
                try:
                    flags = fcntl.fcntl(pipe.fileno(), fcntl.F_GETFL)
                    fcntl.fcntl(pipe.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
                except Exception:
                    pass

    try:
        while time.time() < deadline:
            # Check if process died
            if proc.poll() is not None and not request_sent:
                break

            # Read stderr (non-blocking) to catch server errors early
            try:
                if sys.platform != "win32":
                    ready, _, _ = select.select([proc.stderr], [], [], 0.05)
                    if ready:
                        try:
                            chunk = proc.stderr.read(4096)
                        except (BlockingIOError, IOError):
                            chunk = b""
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

            # Read stdout (non-blocking)
            try:
                if sys.platform != "win32":
                    ready, _, _ = select.select([proc.stdout], [], [], 0.05)
                    if ready:
                        try:
                            chunk = proc.stdout.read(4096)
                        except (BlockingIOError, IOError):
                            chunk = b""
                        if chunk:
                            response_data += chunk
                else:
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

            time.sleep(0.05)
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
        self.selected_server = None
        self.server_statuses = {}
        self.checking = False

        self.root = tk.Tk()
        self.root.title("MCP Server Manager")
        self.root.geometry("1100x700")
        self.root.minsize(760, 500)
        self.root.resizable(True, True)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        self.name_var = tk.StringVar()  # after root exists

        self.build_ui()
        self.status_var.set("Klik 'Auto-Detect' untuk mencari / membuat config.")

    def build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        self.root.configure(bg=BG_COLOR)
        style.configure("TLabel", font=("Poppins", 10), background=BG_COLOR, foreground=TEXT_COLOR)
        style.configure("TButton", font=("Poppins", 10, "bold"))
        style.configure("Accent.TButton", foreground="white", background=ACCENT_COLOR)
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Poppins", 10), padding=[10, 4])
        style.configure("Treeview", background=SURFACE_COLOR, foreground=TEXT_COLOR,
                        fieldbackground=SURFACE_COLOR, font=("Poppins", 10))
        style.configure("Treeview.Heading", background=ELEVATED_COLOR, foreground=ACCENT_LIGHT,
                        font=("Poppins", 10, "bold"))
        style.map("Treeview", background=[("selected", "#14532d")],
                  foreground=[("selected", "white")])

        # Split panes keep the server list and JSON editor usable at any window size.
        main_pane = tk.PanedWindow(self.root, orient="horizontal", bg=BG_COLOR,
                                    sashwidth=4, sashrelief="flat")
        main_pane.pack(fill="both", expand=True)

        # --- Server list pane ---
        left_frame = tk.Frame(main_pane, bg=BG_COLOR)
        main_pane.add(left_frame, width=390)

        top_bar = tk.Frame(left_frame, bg=ELEVATED_COLOR, relief="flat", height=50)
        top_bar.pack(fill="x", padx=0, pady=(0, 0))
        top_bar.pack_propagate(False)
        tk.Label(top_bar, text="MCP Server Manager", font=("Poppins", 13, "bold"),
                 fg=ACCENT_LIGHT, bg=ELEVATED_COLOR).pack(side="left", padx=14, pady=10)
        self.path_var = tk.StringVar()
        tk.Label(top_bar, textvariable=self.path_var, font=("Poppins", 8),
                 fg=MUTED_COLOR, bg=ELEVATED_COLOR, anchor="w").pack(side="left",
                                                                      padx=8, pady=8,
                                                                      fill="x", expand=True)
        self._btn(top_bar, "Auto-Detect", self.auto_detect, ACCENT_COLOR, "white",
                  pack_side="right", padx=(0, 5))
        self._btn(top_bar, "Browse", self.browse_config, "#0f172a", ACCENT_COLOR,
                  pack_side="right", padx=(0, 5))

        list_card = tk.Frame(left_frame, bg=ELEVATED_COLOR, relief="flat",
                             highlightbackground=BORDER_COLOR, highlightthickness=1)
        list_card.pack(fill="both", expand=True, padx=10, pady=10)

        list_header = tk.Frame(list_card, bg=ELEVATED_COLOR)
        list_header.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(list_header, text="Servers", font=("Poppins", 12, "bold"),
                 fg=TEXT_COLOR, bg=ELEVATED_COLOR).pack(side="left")
        self._btn(list_header, "+ Add", self._open_add_dialog, ACCENT_COLOR, "white",
                  pack_side="right")

        tree_container = tk.Frame(list_card, bg=ELEVATED_COLOR)
        tree_container.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        columns = ("name", "command", "type", "status")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="tree headings",
                                  height=10, selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.heading("name", text="Name")
        self.tree.heading("command", text="Command / URL")
        self.tree.heading("type", text="Type")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=0, minwidth=0, stretch=False)
        self.tree.column("name", width=115, minwidth=75)
        self.tree.column("command", width=190, minwidth=90)
        self.tree.column("type", width=65, minwidth=50, anchor="center")
        self.tree.column("status", width=75, minwidth=60, anchor="center")
        self.tree.tag_configure("remote", foreground=MUTED_COLOR)
        self.tree.tag_configure("stdio", foreground=TEXT_COLOR)
        self.tree.tag_configure("ok", foreground=SUCCESS_COLOR)
        self.tree.tag_configure("fail", foreground=ERROR_COLOR)

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        action_bar = tk.Frame(list_card, bg=ELEVATED_COLOR)
        action_bar.pack(fill="x", padx=12, pady=(0, 12))
        self._btn(action_bar, "Check Connection", self.check_selected, ACCENT_COLOR, "white",
                  pack_side="left", padx=(0, 6))
        self._btn(action_bar, "Refresh", self.refresh_statuses, "#475569", TEXT_COLOR,
                  pack_side="left", padx=(0, 6))
        self._btn(action_bar, "Remove", self.remove_selected, "#dc2626", "white", pack_side="right")

        # --- JSON editor pane ---
        right_frame = tk.Frame(main_pane, bg=BG_COLOR)
        main_pane.add(right_frame, width=710)

        editor_card = tk.Frame(right_frame, bg=ELEVATED_COLOR, relief="flat",
                                highlightbackground=BORDER_COLOR, highlightthickness=1)
        editor_card.pack(fill="both", expand=True, padx=10, pady=10)

        editor_header = tk.Frame(editor_card, bg=ELEVATED_COLOR)
        editor_header.pack(fill="x", padx=15, pady=(12, 6))
        tk.Label(editor_header, text="Server JSON", font=("Poppins", 12, "bold"),
                 fg=TEXT_COLOR, bg=ELEVATED_COLOR).pack(side="left")
        self.json_server_label = tk.Label(editor_header, text="(pilih server di kiri)",
                                          font=("Poppins", 9, "italic"),
                                          fg=MUTED_COLOR, bg=ELEVATED_COLOR)
        self.json_server_label.pack(side="right")

        json_frame = tk.Frame(editor_card, bg=ELEVATED_COLOR)
        json_frame.pack(fill="both", expand=True, padx=15, pady=(0, 6))
        self.json_text = tk.Text(json_frame, wrap="word",
                                  font=("Consolas" if sys.platform == "win32" else "Monospace", 10),
                                  bg=SURFACE_COLOR, fg=TEXT_COLOR, insertbackground=ACCENT_COLOR,
                                  relief="flat", borderwidth=1, highlightthickness=1,
                                  highlightbackground=BORDER_COLOR, padx=10, pady=10)
        self.json_text.pack(side="left", fill="both", expand=True)
        json_scroll = ttk.Scrollbar(json_frame, orient="vertical", command=self.json_text.yview)
        self.json_text.configure(yscrollcommand=json_scroll.set)
        json_scroll.pack(side="right", fill="y", padx=(5, 0))

        editor_footer = tk.Frame(editor_card, bg=ELEVATED_COLOR)
        editor_footer.pack(fill="x", padx=15, pady=(0, 15))
        self._btn(editor_footer, "Save Changes", self.save_json_edits, ACCENT_COLOR, "white",
                  pack_side="left", padx=(0, 8))
        self._btn(editor_footer, "Clear", self.clear_json, "#475569", TEXT_COLOR,
                  pack_side="left", padx=(0, 8))
        self._btn(editor_footer, "Check Connection", self.check_selected, "#0ea5e9", "white",
                  pack_side="left", padx=(0, 8))
        self._btn(editor_footer, "Restart Claude", self.restart_claude, "#7c3aed", "white",
                  pack_side="right")

        # --- Status bar ---
        status_bar = tk.Frame(self.root, bg=ELEVATED_COLOR, height=36,
                              highlightbackground=BORDER_COLOR, highlightthickness=1)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        self.progress_bar = ttk.Progressbar(status_bar, mode="indeterminate", length=100)
        self.progress_bar.pack(side="right", padx=10, pady=5)
        self.status_var = tk.StringVar(value="Klik 'Auto-Detect' untuk mencari / membuat config.")
        tk.Label(status_bar, textvariable=self.status_var, font=("Helvetica", 9),
                 fg=MUTED_COLOR, bg=ELEVATED_COLOR).pack(side="left", padx=15, pady=5)

    def _btn(self, parent, text, command, bg, fg, pack_side="left", padx=(0, 0)):
        btn = tk.Button(parent, text=f" {text}", command=command,
                        font=("Helvetica", 9, "bold"), bg=bg, fg=fg,
                        activebackground=ACCENT_HOVER if bg == ACCENT_COLOR else "#475569",
                        activeforeground="white", relief="flat", borderwidth=0,
                        cursor="hand2", padx=12, pady=5)
        btn.pack(side=pack_side, padx=padx, pady=2)
        return btn

    def _icon_btn(self, parent, text, command, bg, fg, pack_side="left", padx=(0, 0)):
        return self._btn(parent, text, command, bg, fg, pack_side, padx)

    def _on_tree_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.tree.item(selection[0])
        name = str(item["values"][0])
        entry = self.config_data.get("mcpServers", {}).get(name)
        if not entry:
            return
        self.selected_server = name
        self.json_server_label.config(text=name)
        self.json_text.delete("1.0", "end")
        self.json_text.insert("1.0", json.dumps(entry, indent=2, ensure_ascii=False))

    def refresh_server_list(self):
        self.tree.delete(*self.tree.get_children())
        for name, entry in self.config_data.get("mcpServers", {}).items():
            if "url" in entry:
                cmd, typ = entry["url"], "remote"
            else:
                cmd, typ = entry.get("command", ""), "stdio"
            status = self.server_statuses.get(name, "—")
            tag = "ok" if status == "TERSAMBUNG" else "fail" if status == "GAGAL" else "stdio"
            self.tree.insert("", "end", values=(name, cmd, typ, status), tags=(tag,))

    def _open_add_dialog(self):
        if not self.config_data:
            self.status_var.set("Load config dulu (Auto-Detect atau Browse).")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Add MCP Server")
        dialog.geometry("520x520")
        dialog.configure(bg=BG_COLOR)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Server Name (opsional)", bg=BG_COLOR, fg=TEXT_COLOR,
                 font=("Helvetica", 9)).pack(anchor="w", padx=15, pady=(15, 5))
        name_var = tk.StringVar()
        tk.Entry(dialog, textvariable=name_var, font=("Helvetica", 10),
                 bg=SURFACE_COLOR, fg=TEXT_COLOR, insertbackground=ACCENT_COLOR,
                 relief="flat", highlightthickness=1, highlightbackground=BORDER_COLOR
                 ).pack(fill="x", padx=15, pady=(0, 10))
        tk.Label(dialog, text="Server JSON", bg=BG_COLOR, fg=TEXT_COLOR,
                 font=("Helvetica", 9)).pack(anchor="w", padx=15, pady=(5, 5))
        json_text = tk.Text(dialog, height=15, wrap="word",
                            font=("Consolas", 10), bg=SURFACE_COLOR, fg=TEXT_COLOR,
                            insertbackground=ACCENT_COLOR, relief="flat",
                            borderwidth=1, highlightthickness=1, highlightbackground=BORDER_COLOR)
        json_text.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        def do_add():
            name = name_var.get().strip()
            json_str = json_text.get("1.0", "end").strip()
            if not json_str:
                messagebox.showwarning("Validation", "Server JSON wajib diisi.")
                return
            try:
                name, entry = parse_pasted_server(name, json_str)
            except ValueError as e:
                messagebox.showwarning("JSON Error", str(e))
                return
            success, msg = add_mcp_server(self.config_data, name, entry)
            if not success and not messagebox.askyesno("Duplicate", msg + "\n\nTimpa yang lama?"):
                return
            self.config_data["mcpServers"][name] = entry
            try:
                backup = save_config(self.config_path, self.config_data)
            except Exception as e:
                messagebox.showerror("Error", f"Gagal menyimpan config:\n{e}")
                return
            self.refresh_server_list()
            dialog.destroy()
            self.status_var.set(f"Added '{name}' | Backup: {os.path.basename(backup)}")
            if messagebox.askyesno("Sukses", f"MCP '{name}' ditambahkan!\n\nCek koneksi sekarang?"):
                self.check_server(name)

        self._btn(dialog, "Add MCP", do_add, ACCENT_COLOR, "white", pack_side="right", padx=(0, 10))
        self._btn(dialog, "Cancel", dialog.destroy, "#475569", TEXT_COLOR, pack_side="right")

    def save_json_edits(self):
        if not self.config_data:
            messagebox.showwarning("No Config", "Load config dulu (Auto-Detect atau Browse).")
            return
        json_str = self.json_text.get("1.0", "end").strip()
        if not json_str:
            messagebox.showwarning("Validation", "Server JSON wajib diisi.")
            return
        try:
            entry = json.loads(json_str)
        except json.JSONDecodeError as e:
            messagebox.showwarning("JSON Error", f"JSON tidak valid: {e}")
            return
        if not isinstance(entry, dict) or ("command" not in entry and "url" not in entry):
            messagebox.showwarning("Validation", 'Server config harus memiliki "command" atau "url".')
            return
        if self.selected_server:
            name = self.selected_server
            self.config_data["mcpServers"][name] = entry
        else:
            name = None
            try:
                name, entry = parse_pasted_server("", json_str)
            except ValueError as e:
                messagebox.showwarning("JSON Error", str(e))
                return
            if name in self.config_data.get("mcpServers", {}):
                if not messagebox.askyesno("Duplicate", f"MCP server '{name}' sudah ada!\n\nTimpa yang lama?"):
                    return
            self.config_data.setdefault("mcpServers", {})[name] = entry
            self.selected_server = name
        try:
            backup = save_config(self.config_path, self.config_data)
        except Exception as e:
            messagebox.showerror("Error", f"Gagal menyimpan config:\n{e}")
            return
        self.refresh_server_list()
        self.json_server_label.config(text=name)
        self.status_var.set(f"Saved '{name}' | Backup: {os.path.basename(backup)}")

    def clear_json(self):
        self.json_text.delete("1.0", "end")
        self.json_server_label.config(text="(pilih server di kiri)")
        self.selected_server = None

    # --- Config loading ---

    def refresh_statuses(self):
        """Clear and reload server list (used for refresh)."""
        if self.config_data:
            self.refresh_server_list()

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

    def _selected_name(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Pilih Server", "Pilih salah satu MCP server di daftar dulu.")
            return None
        return str(self.tree.item(selection[0])["values"][0])

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
        # Guard: only one check at a time, prevent resource exhaustion
        if getattr(self, "checking", False):
            messagebox.showwarning("Check in Progress", "Sedang checking satu server saja.\nTunggu sampai selesai dulu.")
            return
        entry = self.config_data.get("mcpServers", {}).get(name)
        if not entry:
            messagebox.showwarning("Not Found", f"Server '{name}' tidak ada di config.")
            return
        self.checking = True
        self.status_var.set(f"Checking '{name}' ... (mohon tunggu, first-run npx bisa lama)")
        self.root.config(cursor="watch")

        def worker():
            ok, detail = check_mcp_connection(entry)
            self.checking = False
            self.root.after(0, lambda: self._check_done(name, ok, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _check_done(self, name, ok, detail):
        self._install_retries = 0
        self.root.config(cursor="")
        if detail == "NODE_MISSING":
            result = messagebox.askyesno(
                "Node.js Belum Terinstal",
                f"MCP '{name}' membutuhkan Node.js, tetapi Node.js tidak ditemukan di komputer Anda.\n\n"
                f"Apakah ingin menginstall Node.js secara otomatis?\n\n"
                f"(Windows: download MSI installer\n"
                f"macOS: install via Homebrew\n"
                f"Linux: install via package manager)"
            )
            if not result:
                self.server_statuses[name] = "GAGAL"
                self.refresh_server_list()
                self.status_var.set(f"[{name}] GAGAL - Node.js tidak ada")
                return

            self.status_var.set("Menginstall Node.js... (mohon tunggu)")
            self.root.config(cursor="watch")

            def install_worker():
                success, msg = install_nodejs_silent(status_callback=self._update_install_status)
                self.root.after(0, lambda: self._install_done(name, success, msg))

            threading.Thread(target=install_worker, daemon=True).start()
            return

        status = "TERSAMBUNG" if ok else "GAGAL"
        self.server_statuses[name] = status
        self.refresh_server_list()
        self.status_var.set(f"[{name}] {status}")
        (messagebox.showinfo if ok else messagebox.showerror)(
            f"Connection Check: {status}", f"MCP: {name}\n\n{detail}")

    def _update_install_status(self, text):
        self.status_var.set(text)

    def _install_done(self, name, success, msg):
        self.root.config(cursor="")
        if success:
            from mcp_manager import refresh_path_for_new_node
            refresh_path_for_new_node()
            self.status_var.set(f"Node.js terinstal! Mengecek koneksi '{name}'...")
            self.root.after(1000, lambda: self.check_server(name))
            return

        # Linux fallback: try pkexec once if sudo failed in install_nodejs_silent
        os_type = get_os_type()
        if os_type == "linux" and shutil.which("pkexec"):
            pkg_mgr = (
                "apt-get" if shutil.which("apt-get") else
                "dnf" if shutil.which("dnf") else
                "pacman" if shutil.which("pacman") else
                "zypper" if shutil.which("zypper") else None
            )
            retries = getattr(self, "_install_retries", 0)
            if pkg_mgr and retries < 1:
                self._install_retries = retries + 1
                retry = messagebox.askyesno(
                    "Coba pkexec?",
                    f"Auto-install gagal (mungkin perlu password sudo):\n\n{msg}\n\n"
                    f"Coba lagi dengan pkexec (password prompt GUI)?"
                )
                if retry:
                    self.status_var.set("Menginstall Node.js via pkexec...")
                    self.root.config(cursor="watch")

                    def pkexec_worker():
                        ok, m = install_nodejs_with_pkexec(pkg_mgr)
                        self.root.after(0, lambda: self._install_done(name, ok, m))

                    threading.Thread(target=pkexec_worker, daemon=True).start()
                    return

        messagebox.showerror("Instalasi Gagal", f"{msg}\n\nSilakan install Node.js manual dari https://nodejs.org")
        self.status_var.set(f"[{name}] GAGAL - Node.js belum terinstal")

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
