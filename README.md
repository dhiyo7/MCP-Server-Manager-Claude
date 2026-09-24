# MCP Server Manager

Tool desktop sederhana untuk menambahkan konfigurasi MCP Server ke file `claude_desktop_config.json` tanpa perlu edit JSON manual. Cocok untuk tim non-teknis.

## Fitur
- **Cross-Platform:** Windows, macOS, dan Linux.
- **Auto-Detect & Auto-Create:** Mendeteksi lokasi file config per OS secara otomatis. Jika belum ada, otomatis dibuatkan format dasarnya.
- **Paste Form:** Tinggal paste potongan konfigurasi JSON dari dokumentasi MCP (mendukung format satu server atau seluruh object `mcpServers`).
- **Connection Handshake Check:** Mengetes koneksi stdio JSON-RPC sebelum Claude Desktop dijalankan.
- **Graceful Restart:** Opsi untuk merestart Claude Desktop langsung dari aplikasi agar config baru termuat.

## Cara Menjalankan

### Menggunakan Python
```bash
python3 mcp_manager.py
```

### Menggunakan Executable
Jika mendownload binary dari GitHub Release:
- **Linux:** `chmod +x mcp_manager && ./mcp_manager`
- **Windows:** Double-click `mcp_manager.exe`
- **macOS:**
  - Double-click `mcp_manager` (Izinkan di System Settings jika ada peringatan keamanan)
  - Jika muncul *"app is damaged"* atau file tidak dapat dibuka karena quarantine:
    - Buka **Terminal** dan jalankan:
      ```bash
      xattr -cr /path/to/mcp_manager
      ```
    - Kemudian jalankan lagi dengan double-click.
  - Jika muncul *"cannot be opened because the developer cannot be verified"*:
    - Buka **System Settings > Privacy & Security**, lalu klik **Open Anyway**.
  - Jika muncul *"permission denied"*:
    - Buka **Terminal** dan jalankan:
      ```bash
      chmod +x /path/to/mcp_manager
      ```
