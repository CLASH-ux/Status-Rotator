# Status Rotator

A lightweight Discord custom status automation tool that rotates your status messages on a schedule. Keep your Discord profile fresh with automatically cycling status updates.

## Features

- **Auto-Rotation**: Automatically cycle through your custom status messages at configurable intervals
- **Per-Message Duration**: Set individual durations for each status or use a default interval
- **Rate Limiting**: Built-in rate limiting to respect Discord API limits and prevent throttling
- **Auto-Start**: Enable automatic rotation when the application launches
- **System Tray Integration**: Run silently in the background with a system tray icon
- **Hidden Startup**: Automatically start minimized in the system tray on Windows boot
- **Single Instance Protection**: Prevents multiple instances from running simultaneously
- **Token Validation**: Automatic Discord token verification with reconnection logic
- **Shuffle Mode**: Randomize the order of your status messages
- **Modern UI**: Dark theme with smooth animations and intuitive controls
- **Configuration Management**: Secure config storage in `%APPDATA%\StatusRotator`

## Requirements

- Python 3.8 or higher
- `requests` library
- `pystray` and `Pillow` (optional, for system tray support)

## Installation

### 1. Clone or Download
```bash
git clone https://github.com/yourusername/status-rotator.git
cd status-rotator
```

### 2. Install Dependencies
```bash
pip install requests pystray pillow
```

### 3. Run
```bash
python status_gui.py
```

## Usage

1. Paste your Discord user token in the token field
2. Add your custom status messages in the input field
3. Optionally set custom duration for each message: `"Your Status | 5"` (5 seconds)
4. Adjust default interval and rate limit settings as needed
5. Click **Start** to begin rotation
6. Enable **"Start when opened"** for automatic startup
7. Enable **"Launch with Windows"** to auto-start on system boot
8. Enable **"System Tray"** to run silently in the background

### System Tray Menu
- **Open**: Show the application window
- **Start/Stop Rotation**: Toggle rotation on or off
- **Quit**: Close the application

## Building an Executable

To create a standalone `.exe` file:

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --hidden-import pystray._win32 status_gui.py
```

The executable will be created in the `dist/` folder.

## Configuration

Settings are automatically saved in `%APPDATA%\StatusRotator\config.json`

```json
{
  "token": "your_discord_token",
  "default_interval": 3,
  "min_interval": 2,
  "shuffle": false,
  "auto_run": true,
  "start_with_windows": true,
  "tray": true,
  "statuses": [
    {"text": "Status 1", "seconds": null},
    {"text": "Status 2 | 5", "seconds": 5}
  ]
}
```

## ⚠️ Important Notice

**Using user account tokens with automation violates Discord's Terms of Service.** This tool is provided for educational purposes. Use at your own risk—Discord may suspend or permanently ban accounts that violate their policies.

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

---

**Note**: System tray features require Windows. On other platforms, the application will minimize to the taskbar instead.
