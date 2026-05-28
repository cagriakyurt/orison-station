# ORISON // FM RDS Numbers Station & Tactical Radio Terminal

![ORISON Dashboard](web/static/dashboard_en.png)

ORISON is a modern, PWA-supported web control terminal that gives users the opportunity to set up their own personal numbers station over FM, enabling a Raspberry Pi to broadcast FM signals and transmit dynamic RDS (Radio Data System) data directly via its GPIO pins using hardware DMA (Direct Memory Access).

Based on the **PiFmRds** core, this project adds text-to-speech voice generation, a Morse code generator, digital bandpass audio filter effects, and background interference/noise simulations.

> [!WARNING]
> **LEGAL NOTICE:** Broadcasting on FM frequencies may be illegal in your country or region without an official license or authorization. This software is designed for educational, laboratory, and simulation purposes only. The user assumes all legal responsibility for any interference caused or regulatory compliance issues arising from the usage of this software.

> [!CAUTION]
> **SECURITY NOTICE:** The web interface and REST API endpoints (e.g. `/log`, `/download`, `/api/schedules`, `/api/otp/encrypt`, `/action`, `/action_sequence`) do NOT include authentication. This software is designed strictly for local area network (LAN) usage. Do NOT expose this web interface or its port (`8765`) to the public internet.

---

## 📡 Core Features

- **Dynamic Frequency Tuning (MHz):** Real-time frequency updates in the range of `87.5 - 108.0 MHz` using the slider on the interface.
- **Dynamic RDS Control:** Real-time updates to the station name (PS - Program Service, max 8 chars) and detail text (RT - Radio Text, max 64 chars) during active broadcast.
- **Broadcast Preview Mode:** Listen to any generated sequence or audio directly inside the browser before transmitting it over-the-air on FM.
- **Espionage-Grade OTP Cipher:** Modulo-10 encryption to secure voice numbers station broadcasts with a downloadable key sheet.
- **Automated Broadcast Scheduler:** Embedded SQLite-backed scheduler that runs pre-planned transmissions at specified dates/times.
- **Tactical Audio Filters:**
  - *AM Radio Mode:* Standard military radio band simulation (bandpass filter).
  - *Bunker Echo:* Underground bunker simulation using reverb effects.
  - *Tape Drift:* Vintage tape deck simulation using tremolo effects.
  - *Raw Clean Audio:* Unfiltered clean audio output.
- **Shortwave Interference (Noise):** Tactical background noise mixing white noise and 50Hz power grid hum.
- **Morse Code Transmitter:** Dynamically converts text to Morse code and broadcasts it using custom beep frequencies and speed options.
- **Broadcast Queue Sequencer:** Chain station IDs, Morse code sequences, and synthetic voice text blocks to build seamless and automated playlists.
- **PWA (Progressive Web App) Support:** Can be installed on Android/iOS via Chrome or Safari as a standalone full-screen tactical application.

---

## 📁 Project Structure

```text
orison/
├── scripts/
│   ├── orison                  # Main Python CLI management script
│   ├── orison-broadcast        # Bash script handling PiFmRds and DMA management
│   └── orison-stop             # Bash script to gracefully stop broadcasts
├── sudoers/
│   └── orison.template         # Sudoers policy template for passwordless PiFmRds/orison-stop access
├── systemd/
│   └── orison-web.service.template # Systemd service template automatically starting the web app
├── web/
│   ├── app.py                  # Flask backend service and REST API endpoints
│   ├── static/                 # Manifest, Service Worker, and PWA icon assets
│   └── templates/
│       └── index.html          # HTML5/JS frontend dashboard with a retro CRT console design
├── orison.db                   # Runtime SQLite database for scheduled tasks, created automatically
└── .gitignore                  # Gitignore file filtering temporary audio and log files
```

---

## ⚙️ Installation and Deployment (on Raspberry Pi)

### Method A: Automatic Installation (Recommended)

You can automatically install the project using a single command. The installation script (`install.sh`) installs system dependencies, compiles the `PiFmRds` module, detects the current user and generates the systemd and sudoers configurations from templates, while the CLI scripts resolve the project directory dynamically through symlinks.

1. Clone the repository onto the Pi:
   ```bash
   git clone https://github.com/cagriakyurt/orison-station.git ~/station
   ```
2. Navigate to the project directory and run the installation script:
   ```bash
   cd ~/station
   chmod +x install.sh
   ./install.sh
   ```

---

### Method B: Manual Installation (Reference)

If you prefer to run the setup steps manually instead of using the automated script:

> [!IMPORTANT]
> **Username Note:** ORISON no longer requires manual username/path replacement. The installer and manual setup commands generate systemd and sudoers files from templates using your current `$USER`, `$HOME`, and `$PWD` values.

#### Step 1: Install Basic Dependencies
Open a terminal on your Pi and install voice synthesis, audio processing, and web server tools:
```bash
sudo apt-get update
sudo apt-get install git sox libsox-fmt-all espeak-ng python3-pip python3-flask -y
```

#### Step 2: Compile the PiFmRds Core
Clone and build the core library that generates the FM radio carrier waves:
```bash
# Clone repository to home directory
cd ~
git clone https://github.com/ChristopheJacquet/PiFmRds.git

# Start the compilation
cd PiFmRds/src
make
```
*This will generate an executable binary file at `/home/YOUR_USERNAME/PiFmRds/src/pi_fm_rds`.*

#### Step 3: Clone the ORISON Repository
Clone the project repository to your Pi:
```bash
cd ~
git clone https://github.com/cagriakyurt/orison-station.git station
```

#### Step 4: Username and Path Handling
No manual path replacement is required. ORISON resolves its project directory dynamically via symlinks, and the sudoers/systemd files are generated from templates using the current `$USER`, `$HOME`, and `$PWD` values.

#### Step 5: Install System Configs and Services
Set up CLI scripts globally, set permissions, and configure the web console service to start on boot:

```bash
cd ~/station

# 1. Set script execution permissions and create symbolic links
chmod +x scripts/orison scripts/orison-broadcast scripts/orison-stop
sudo ln -sf "$PWD/scripts/orison" /usr/local/bin/orison
sudo ln -sf "$PWD/scripts/orison-broadcast" /usr/local/bin/orison-broadcast
sudo ln -sf "$PWD/scripts/orison-stop" /usr/local/bin/orison-stop

# 2. Configure passwordless transmitter rights via sudoers
sed "s|{{USER}}|$USER|g; s|{{HOME}}|$HOME|g" sudoers/orison.template > /tmp/orison-sudoers
sudo chmod 440 /tmp/orison-sudoers
sudo visudo -cf /tmp/orison-sudoers
sudo cp /tmp/orison-sudoers /etc/sudoers.d/orison
sudo chmod 440 /etc/sudoers.d/orison
sudo rm -f /tmp/orison-sudoers

# 3. Create and enable the Systemd service from the template
sed "s|{{USER}}|$USER|g; s|{{BASE_DIR}}|$PWD|g" systemd/orison-web.service.template > /tmp/orison-web.service
sudo cp /tmp/orison-web.service /etc/systemd/system/orison-web.service
sudo rm -f /tmp/orison-web.service
sudo systemctl daemon-reload
sudo systemctl enable orison-web.service
sudo systemctl start orison-web.service
```

---

### Hardware Setup and UI Access

1. Attach a simple 20–30 cm wire antenna to **GPIO 4** (physical Pin 7) on the Raspberry Pi.
2. Access the control panel via a browser at `http://your-pi-ip-address:8765` (or `http://station.local:8765`).

---

## 🛠️ Local Development, Syncing, and Git Commands

Use the following commands to test local updates, push to GitHub, or pull changes onto the Pi:

### 1. Sync Changes with the Raspberry Pi
To upload your local developments to the Pi and automatically restart the service in a single command, run the sync helper script:
```bash
python3 path/to/ssh_sync.py
```

### 2. Push Changes to GitHub
To push local changes up to the GitHub repository:
```bash
# Go to project directory
cd path/to/orison

# Stage files (.gitignore automatically filters temporary audio and logs)
git add .

# Commit with a description
git commit -m "Description of changes made"

# Push to GitHub
git push
```

### 3. Pull Changes on the Raspberry Pi
To pull the latest commits on the Pi:
```bash
cd ~/station
git pull
```
*Note: After pulling, you can re-run `./install.sh` or run `sudo systemctl restart orison-web.service` to apply changes.*

---

## 👨‍💻 Developer Information

This project was built and developed by [@lunchweek](https://x.com/lunchweek) ([itch.io](https://lunchweek.itch.io)) using **Google Antigravity**, an agentic AI coding assistant designed by Google DeepMind.


