#!/bin/bash
# install.sh: ORISON installation and deployment script for Raspberry Pi

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== ORISON Installation Script ==="

# 1. Check if running on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "ERROR: This script must be run on Linux (Raspberry Pi OS)." >&2
    exit 1
fi

USER_HOME="$HOME"
CURRENT_USER="$USER"
echo "Detected user: $CURRENT_USER (Home: $USER_HOME)"

# 2. Update paths if user is not 'host'
if [ "$CURRENT_USER" != "host" ]; then
    echo "Updating configuration paths for user: $CURRENT_USER..."
    # Replace /home/host with the actual home directory path
    ESCAPED_HOST_HOME="home\/host"
    ESCAPED_NEW_HOME="${USER_HOME//\//\\/}"
    
    # Modify files in-place using standard Linux sed syntax
    sed -i "s/$ESCAPED_HOST_HOME/$ESCAPED_NEW_HOME/g" web/app.py
    sed -i "s/$ESCAPED_HOST_HOME/$ESCAPED_NEW_HOME/g" scripts/orison
    sed -i "s/$ESCAPED_HOST_HOME/$ESCAPED_NEW_HOME/g" scripts/orison-broadcast
    sed -i "s/$ESCAPED_HOST_HOME/$ESCAPED_NEW_HOME/g" sudoers/orison
    sed -i "s/$ESCAPED_HOST_HOME/$ESCAPED_NEW_HOME/g" systemd/orison-web.service
    
    # Also replace systemd user execution context
    sed -i "s/User=host/User=$CURRENT_USER/g" systemd/orison-web.service
    # Replace sudoers authorization context
    sed -i "s/^host ALL/$CURRENT_USER ALL/g" sudoers/orison
    
    echo "Paths updated successfully."
fi

# 3. Install packages
echo "Installing system packages (Sox, espeak-ng, Flask)..."
sudo apt-get update
sudo apt-get install -y git sox libsox-fmt-all espeak-ng python3-pip python3-flask

# 4. Clone and compile PiFmRds if not exists
if [ ! -d "$USER_HOME/PiFmRds" ]; then
    echo "PiFmRds not found. Cloning and compiling..."
    git clone https://github.com/ChristopheJacquet/PiFmRds.git "$USER_HOME/PiFmRds"
    cd "$USER_HOME/PiFmRds/src"
    make
    cd -
else
    echo "PiFmRds already exists in $USER_HOME/PiFmRds."
fi

# 5. Install CLI scripts
echo "Installing CLI commands to /usr/local/bin..."
sudo cp scripts/orison /usr/local/bin/orison
sudo cp scripts/orison-broadcast /usr/local/bin/orison-broadcast
sudo chmod +x /usr/local/bin/orison /usr/local/bin/orison-broadcast

# 6. Install Sudoers configuration
echo "Installing sudoers policy..."
sudo cp sudoers/orison /tmp/orison-sudoers
sudo chmod 440 /tmp/orison-sudoers
sudo visudo -cf /tmp/orison-sudoers
sudo cp /tmp/orison-sudoers /etc/sudoers.d/orison
sudo chmod 440 /etc/sudoers.d/orison
sudo rm -f /tmp/orison-sudoers

# 7. Install Systemd service
echo "Installing systemd service..."
sudo cp systemd/orison-web.service /etc/systemd/system/orison-web.service
sudo systemctl daemon-reload
sudo systemctl enable orison-web.service
sudo systemctl restart orison-web.service

echo "=== ORISON Installation Completed Successfully! ==="
echo "Access the dashboard at http://station.local:8765 or http://<pi-ip>:8765"
