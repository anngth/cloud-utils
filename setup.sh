#!/bin/bash

# =============================================================================
# VPS Setup Script - Comprehensive VPS Configuration and Tool Installation
# =============================================================================
#
# For detailed documentation, usage examples, and troubleshooting, see README.md
#
# Quick Start: ./setup.sh
#
# =============================================================================

set -uo pipefail  # Exit on undefined vars, pipe failures (not -e for interactive mode)

export DEBIAN_FRONTEND=noninteractive

# =============================================================================
# CONSTANTS AND CONFIGURATION
# =============================================================================

# Default ports
readonly DEFAULT_PROXY_PORT="3128"
readonly DEFAULT_SSH_PORT="22"
readonly DEFAULT_XRDP_PORT="3389"

# Proxy credentials (load from environment variables with fallback defaults)
readonly PROXY_USER="${PROXY_USER:-your_proxy_username_here}"
readonly PROXY_PASS="${PROXY_PASS:-your_proxy_password_here}"

# User password (load from environment variable)
readonly USER_PASSWORD="${USER_PASSWORD:-}"

# Script directory
readonly SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
readonly CONFIG_DIR="$SCRIPT_DIR/fail2ban-configs"

# =============================================================================
# LOGGING AND ERROR HANDLING
# =============================================================================

log_info() {
    echo "[INFO] $*"
}

log_success() {
    echo "✅ $*"
}

log_error() {
    echo "❌ $*" >&2
}

log_warning() {
    echo "⚠️  $*"
}

# Error handler (disabled by default for interactive mode compatibility)
# Uncomment the trap below if you want strict error handling
# error_handler() {
#     local line_num=$1
#     log_error "Error occurred in script at line: $line_num"
#     log_error "Last command exit code: $?"
# }
# trap 'error_handler ${LINENO}' ERR

# =============================================================================
# FLAG AND VALUE MANAGEMENT
# =============================================================================

# Define arrays for flags and their default values
declare -A FLAGS=(
  ["fnm"]=false
  ["rclone"]=false
  ["docker"]=false
  ["xrdp"]=false
  ["proxy"]=false
  ["qbittorrent"]=false
  ["python"]=false
  ["zsh"]=false
  ["zimfw"]=false
  ["bash-it"]=false
  ["apt-update"]=false
  ["basic-tools"]=false
  ["uv"]=false
  ["firefox"]=false
  ["eza"]=false
  ["zoxide"]=false
  ["fastfetch"]=false
  ["set-password"]=false
  ["verify-xrdp"]=false
  ["firewall"]=false
  ["ssh-security"]=false
  ["fail2ban"]=false
  ["tailscale"]=false
)

declare -A VALUES=(
  ["proxy-port"]="$DEFAULT_PROXY_PORT"
  ["ssh-port"]="$DEFAULT_SSH_PORT"
  ["xrdp-port"]="$DEFAULT_XRDP_PORT"
)

# Parse args
for arg in "$@"; do
  if [[ "$arg" == -* ]]; then
    flag="${arg#-}"
    if [[ "$flag" == *"="* ]]; then
      # Handle key=value pairs
      key="${flag%%=*}"
      value="${flag#*=}"
      VALUES["$key"]="$value"
    elif [[ "$flag" == "proxy" && "$arg" == *":"* ]]; then
      # Special case for proxy with port
      VALUES["proxy-port"]="${arg#*:}"
      FLAGS["proxy"]=true
    else
      # Regular flags
      FLAGS["$flag"]=true
    fi
  fi
done

# Convert flags to variables for backwards compatibility
INSTALL_FNM=${FLAGS["fnm"]}
INSTALL_RCLONE=${FLAGS["rclone"]}
INSTALL_DOCKER=${FLAGS["docker"]}
INSTALL_XRDP=${FLAGS["xrdp"]}
INSTALL_PROXY=${FLAGS["proxy"]}
INSTALL_QBITTORRENT=${FLAGS["qbittorrent"]}
INSTALL_PYTHON=${FLAGS["python"]}
INSTALL_ZSH=${FLAGS["zsh"]}
INSTALL_ZIMFW=${FLAGS["zimfw"]}
INSTALL_BASH_IT=${FLAGS["bash-it"]}
UPDATE_APT=${FLAGS["apt-update"]}
INSTALL_BASIC_TOOLS=${FLAGS["basic-tools"]}
INSTALL_UV=${FLAGS["uv"]}
INSTALL_FIREFOX=${FLAGS["firefox"]}
INSTALL_EZA=${FLAGS["eza"]}
INSTALL_ZOXIDE=${FLAGS["zoxide"]}
INSTALL_FASTFETCH=${FLAGS["fastfetch"]}
SET_PASSWORD=${FLAGS["set-password"]}
VERIFY_XRDP=${FLAGS["verify-xrdp"]}
INSTALL_FIREWALL=${FLAGS["firewall"]}
PROXY_PORT=${VALUES["proxy-port"]}
SSH_PORT=${VALUES["ssh-port"]}

# =============================================================================
# PREREQUISITE CHECKS
# =============================================================================

# Check if running as root or with sudo
if [ "$EUID" -eq 0 ]; then
    log_error "This script should not be run as root. Please run as a regular user with sudo privileges."
    exit 1
fi

# Check if sudo is available
if ! command -v sudo >/dev/null 2>&1; then
    log_error "sudo is not available. Please install sudo first."
    exit 1
fi

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

# Function to change user password
change_password() {
    if [[ -z "$USER_PASSWORD" ]]; then
        log_error "USER_PASSWORD environment variable is not set"
        log_error "Please set USER_PASSWORD before running this script"
        return 1
    fi
    
    log_info "Changing user password..."
    echo "$USER:$USER_PASSWORD" | sudo chpasswd
    log_success "Password changed successfully"
}

# Validate port numbers
validate_port() {
    local port=$1
    local name=$2
    
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        log_error "Invalid $name port: $port. Must be between 1-65535"
        return 1
    fi
    return 0
}

# Validate ports if provided
if [ "${VALUES[proxy-port]}" != "3128" ]; then
    validate_port "${VALUES[proxy-port]}" "proxy"
fi

if [ "${VALUES[ssh-port]}" != "22" ]; then
    validate_port "${VALUES[ssh-port]}" "SSH"
fi

# Detect SSH service name
get_ssh_service() {
  if systemctl list-unit-files | grep -q "^ssh.service"; then
    echo "ssh"
  elif systemctl list-unit-files | grep -q "^sshd.service"; then
    echo "sshd"
  else
    echo "ssh"  # Default fallback
  fi
}

readonly SSH_SERVICE=$(get_ssh_service)

# =============================================================================
# INSTALLATION FUNCTIONS
# =============================================================================

update_apt() {
  log_info "Updating and upgrading apt packages..."
  sudo apt -y update
  sudo apt -y upgrade
  sudo apt -y full-upgrade
  sudo apt --fix-broken install -y
  sudo apt -y autoremove
  sudo apt -y autoclean
  log_success "System packages updated successfully."
}

install_firefox() {
  log_info "Installing Firefox browser..."
  sudo apt -y install firefox
  log_success "Firefox installed successfully."
}

install_xrdp() {
  log_info "Installing XRDP (Remote Desktop)..."
  
  # Check if XRDP is already installed
  if systemctl is-active --quiet xrdp; then
    log_success "XRDP is already installed and running. Skipping..."
    return 0
  fi
  
  # Install XRDP and XFCE4
  sudo DEBIAN_FRONTEND=noninteractive apt -y install xfce4
  sudo apt install -y xfce4-session
  sudo apt -y install xrdp
  
  # Enable and start XRDP service
  sudo systemctl enable xrdp
  sudo systemctl start xrdp
  
  # Configure XFCE4 as default session
  echo xfce4-session >~/.xsession
  
  # Verify installation
  if systemctl is-active --quiet xrdp; then
    log_success "XRDP installed and started successfully."
  else
    log_error "XRDP installation failed."
    return 1
  fi
}

change_xrdp_port() {
  local new_port=$1
  
  # Validate port number
  if ! validate_port "$new_port" "XRDP"; then
    return 1
  fi
  
  log_info "Changing XRDP port to $new_port..."
  
  # Check if XRDP is installed
  if ! systemctl is-active --quiet xrdp; then
    log_error "XRDP is not installed or not running. Please install XRDP first."
    return 1
  fi
  
  # Backup original config
  sudo cp /etc/xrdp/xrdp.ini /etc/xrdp/xrdp.ini.backup
  
  # Change port in configuration
  sudo sed -i "s/^port=.*/port=$new_port/" /etc/xrdp/xrdp.ini
  
  # Restart XRDP service
  sudo systemctl restart xrdp
  
  # Verify port change
  if grep -q "^port=$new_port" /etc/xrdp/xrdp.ini; then
    log_success "XRDP port changed to $new_port successfully."
  else
    log_error "Failed to change XRDP port. Restoring backup..."
    sudo cp /etc/xrdp/xrdp.ini.backup /etc/xrdp/xrdp.ini
    sudo systemctl restart xrdp
    return 1
  fi
}

verify_xrdp_setup() {
  echo "🔍 Verifying XRDP setup..."
  
  # Check if XRDP service is running
  if systemctl is-active --quiet xrdp; then
    echo "✅ XRDP service is running"
  else
    echo "❌ XRDP service is not running"
    return 1
  fi
  
  # Check if XRDP is enabled
  if systemctl is-enabled --quiet xrdp; then
    echo "✅ XRDP service is enabled (starts on boot)"
  else
    echo "❌ XRDP service is not enabled"
  fi
  
  # Check XRDP port
  local xrdp_port=$(grep "^port=" /etc/xrdp/xrdp.ini | cut -d'=' -f2)
  echo "✅ XRDP is configured on port: $xrdp_port"
  
  # Check if port is listening
  if netstat -tlnp | grep -q ":$xrdp_port "; then
    echo "✅ XRDP is listening on port $xrdp_port"
  else
    echo "❌ XRDP is not listening on port $xrdp_port"
    return 1
  fi
  
  # Check firewall status
  if command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -q "$xrdp_port"; then
      echo "✅ Firewall allows XRDP port $xrdp_port"
    else
      echo "⚠️  Firewall may block XRDP port $xrdp_port"
    fi
  fi
  
  # Check XFCE4 session configuration
  if [ -f ~/.xsession ] && grep -q "xfce4-session" ~/.xsession; then
    echo "✅ XFCE4 session is configured"
  else
    echo "❌ XFCE4 session is not configured"
  fi
  
  echo ""
  echo "🎯 XRDP Connection Information:"
  echo "  • Server IP: $(curl -s ifconfig.me 2>/dev/null || echo 'Check your VPS IP')"
  echo "  • Port: $xrdp_port"
  echo "  • Username: $USER"
  echo "  • Password: Set via USER_PASSWORD environment variable (if you used -set-password flag)"
  echo ""
  echo "📱 To connect:"
  echo "  1. Use Windows Remote Desktop Connection"
  echo "  2. Use macOS Screen Sharing"
  echo "  3. Use Linux: rdesktop or Remmina"
  echo "  4. Use mobile: Microsoft RDP app"
}

install_zsh() {
  # Check if zsh is already installed
  if command -v zsh >/dev/null 2>&1; then
    version=$(zsh --version)
    log_info "Zsh is already installed: $version"
    
    # Check if zsh is default shell
    if [[ "$SHELL" == "$(which zsh)" ]]; then
      log_info "Zsh is already your default shell."
      return 0
    else
      log_info "Configuring zsh as default shell..."
      chsh -s $(which zsh)
      log_success "Zsh set as default shell. Please log out and log back in for changes to take effect."
      return 0
    fi
  fi

  log_info "Installing Zsh..."
  sudo apt update -y
  sudo apt install -y git zsh

  # Set zsh as default shell
  if [[ "$SHELL" != "$(which zsh)" ]]; then
      chsh -s $(which zsh)
      log_success "Zsh set as default shell. You may need to logout and login again."
      log_info "Restarting Zsh shell..."
      exec zsh
  fi

  $SHELL --version
  log_success "Zsh installed successfully."
}

install_zimfw() {
  log_info "Installing zimfw (Zim Framework for Zsh)..."
  
  # Check if zsh is installed
  if ! command -v zsh >/dev/null 2>&1; then
    log_info "Zsh is required for zimfw. Installing zsh first..."
    install_zsh
  fi
  
  # Install zimfw
  curl -fsSL https://raw.githubusercontent.com/zimfw/install/master/install.zsh | zsh

  # Ensure skip_global_compinit=1 is added to ~/.zshenv
  if ! grep -q "skip_global_compinit=1" ~/.zshenv 2>/dev/null; then
    echo "skip_global_compinit=1" >> ~/.zshenv
  fi

  # Verify installation
  if [ -f "${ZDOTDIR:-${HOME}}/.zimrc" ]; then
    log_success "zimfw installed successfully."
  else
    log_error "zimfw installation failed."
    return 1
  fi
}

install_bash_it() {
  log_info "Installing bash-it (community bash framework)..."
  
  # Check if bash-it is already installed
  if [ -d ~/.bash_it ]; then
    log_warning "bash-it is already installed at ~/.bash_it"
    log_info "Skipping bash-it installation."
    return 0
  fi
  
  # Clone bash-it repository
  log_info "Cloning bash-it repository..."
  if git clone --depth=1 https://github.com/Bash-it/bash-it.git ~/.bash_it; then
    log_success "bash-it repository cloned successfully."
  else
    log_error "Failed to clone bash-it repository."
    return 1
  fi
  
  # Run installation script
  log_info "Running bash-it installation script..."
  if bash ~/.bash_it/install.sh --no-modify-config; then
    log_success "bash-it installed successfully."
    log_info "To enable bash-it, add the following to your ~/.bashrc:"
    log_info "  export BASH_IT=\"\$HOME/.bash_it\""
    log_info "  source \"\$BASH_IT/bash_it.sh\""
  else
    log_error "bash-it installation script failed."
    return 1
  fi
}

install_zoxide() {
  log_info "Installing zoxide (a smarter cd command)..."
  
  # Install required dependencies
  sudo apt update -y
  sudo apt install -y curl

  # Install zoxide using the official installation script
  curl -sSfL https://raw.githubusercontent.com/ajeetdsouza/zoxide/main/install.sh | sh

  # Add zoxide initialization to shell configuration
  if [[ -f "$HOME/.zshrc" ]]; then
    # Check if zoxide init is already in .zshrc
    if ! grep -q "zoxide init" "$HOME/.zshrc"; then
      echo "# Initialize zoxide" >> "$HOME/.zshrc"
      echo 'eval "$(zoxide init zsh)"' >> "$HOME/.zshrc"
    fi
  fi
  
  if [[ -f "$HOME/.bashrc" ]]; then
    # Check if zoxide init is already in .bashrc
    if ! grep -q "zoxide init" "$HOME/.bashrc"; then
      echo "# Initialize zoxide" >> "$HOME/.bashrc"
      echo 'eval "$(zoxide init bash)"' >> "$HOME/.bashrc"
    fi
  fi
  
  sudo mv ~/.local/bin/zoxide /usr/local/bin/ 2>/dev/null || true

  # Verify installation
  if command -v zoxide >/dev/null 2>&1; then
    log_success "zoxide installed successfully: $(zoxide --version)"
  else
    log_error "zoxide installation failed."
    return 1
  fi
}

install_eza() {
  log_info "Installing eza (a replacement for ls)..."
  sudo apt update -y
  sudo apt install -y gpg
  sudo mkdir -p /etc/apt/keyrings
  wget -qO- https://raw.githubusercontent.com/eza-community/eza/main/deb.asc | sudo gpg --dearmor -o /etc/apt/keyrings/gierens.gpg
  echo "deb [signed-by=/etc/apt/keyrings/gierens.gpg] http://deb.gierens.de stable main" | sudo tee /etc/apt/sources.list.d/gierens.list
  sudo chmod 644 /etc/apt/keyrings/gierens.gpg /etc/apt/sources.list.d/gierens.list
  sudo apt update -y
  sudo apt install -y eza
  
  # Verify installation
  if command -v eza >/dev/null 2>&1; then
    log_success "eza installed successfully: $(eza --version)"
  else
    log_error "eza installation failed."
    return 1
  fi
}

install_fastfetch() {
  log_info "Installing fastfetch (a fast system information tool)..."

  # Check if already installed
  if command -v fastfetch >/dev/null 2>&1; then
    log_success "fastfetch is already installed: $(fastfetch --version)"
    return 0
  fi

  # Try installing from PPA first (for Ubuntu 22.04 and earlier)
  if sudo add-apt-repository ppa:zhangsongcui3371/fastfetch -y 2>/dev/null; then
    sudo apt update -y
    if sudo apt install -y fastfetch 2>/dev/null; then
      log_success "fastfetch installed from PPA"
      return 0
    fi
  fi

  # If PPA fails, install from GitHub releases
  log_info "Installing fastfetch from GitHub releases..."
  
  # Detect architecture
  ARCH=$(uname -m)
  case $ARCH in
    x86_64)
      FASTFETCH_ARCH="amd64"
      ;;
    aarch64|arm64)
      FASTFETCH_ARCH="aarch64"
      ;;
    *)
      log_error "Unsupported architecture: $ARCH"
      return 1
      ;;
  esac

  # Download and install latest release
  FASTFETCH_VERSION=$(curl -s https://api.github.com/repos/fastfetch-cli/fastfetch/releases/latest | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')
  
  if [ -z "$FASTFETCH_VERSION" ]; then
    log_error "Failed to get fastfetch version"
    return 1
  fi

  FASTFETCH_URL="https://github.com/fastfetch-cli/fastfetch/releases/download/${FASTFETCH_VERSION}/fastfetch-linux-${FASTFETCH_ARCH}.deb"
  
  wget -q "$FASTFETCH_URL" -O /tmp/fastfetch.deb
  
  if [ -f /tmp/fastfetch.deb ]; then
    sudo dpkg -i /tmp/fastfetch.deb
    sudo apt install -f -y  # Fix any dependency issues
    rm /tmp/fastfetch.deb
  else
    log_error "Failed to download fastfetch"
    return 1
  fi

  # Verify installation
  if command -v fastfetch >/dev/null 2>&1; then
    log_success "fastfetch installed successfully: $(fastfetch --version)"
  else
    log_error "fastfetch installation failed."
    return 1
  fi
}

install_basic_tools() {
  log_info "Installing basic tools..."
  sudo apt -y update
  
  # Install essential tools (required)
  sudo apt -y install git tmux wget build-essential zip unzip net-tools curl htop
  
  # Install optional tools (may not be available in all repos)
  sudo apt -y install bat || log_warning "bat not available (may need manual installation)"
  sudo apt -y install uget || log_warning "uget not available (may need PPA on Ubuntu)"
  
  log_success "Basic tools installed successfully."
  log_info "Note: 'bat' executable is named 'batcat' on Ubuntu/Debian systems"
}

install_uv() {
  log_info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  log_success "uv installed successfully."
}

install_qbittorrent() {
  log_info "Installing qBittorrent..."
  sudo add-apt-repository ppa:qbittorrent-team/qbittorrent-stable -y
  sudo apt -y update
  sudo apt -y install qbittorrent
  log_success "qBittorrent installed successfully."
}

install_rclone() {
  log_info "Installing Rclone..."
  curl https://rclone.org/install.sh | sudo bash
  rclone config file
  log_success "Rclone installed successfully."
}

install_fnm() {
  # Check if fnm is already installed
  if command -v fnm >/dev/null 2>&1; then
    log_success "FNM is already installed: $(fnm --version)"
    return 0
  fi
  
  log_info "Installing FNM (Fast Node Manager)..."
  
  # Ensure curl and unzip are installed
  if ! command -v curl >/dev/null 2>&1; then
    log_info "curl is required for FNM. Installing curl..."
    sudo apt update -y
    sudo apt install -y curl
  fi
  
  if ! command -v unzip >/dev/null 2>&1; then
    log_info "unzip is required for FNM. Installing unzip..."
    sudo apt update -y
    sudo apt install -y unzip
  fi
  
  # Install FNM using the official installation script
  if curl -fsSL https://fnm.vercel.app/install | bash; then
    log_success "FNM installation script completed."
    
    # Source FNM in current shell session
    export PATH="$HOME/.local/share/fnm:$PATH"
    eval "$(fnm env --use-on-cd)"
    
    # Add FNM initialization to shell configuration files
    if [[ -f "$HOME/.bashrc" ]]; then
      if ! grep -q "fnm env" "$HOME/.bashrc"; then
        echo "" >> "$HOME/.bashrc"
        echo "# Initialize FNM (Fast Node Manager)" >> "$HOME/.bashrc"
        echo 'export PATH="$HOME/.local/share/fnm:$PATH"' >> "$HOME/.bashrc"
        echo 'eval "$(fnm env --use-on-cd)"' >> "$HOME/.bashrc"
      fi
    fi
    
    if [[ -f "$HOME/.zshrc" ]]; then
      if ! grep -q "fnm env" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# Initialize FNM (Fast Node Manager)" >> "$HOME/.zshrc"
        echo 'export PATH="$HOME/.local/share/fnm:$PATH"' >> "$HOME/.zshrc"
        echo 'eval "$(fnm env --use-on-cd)"' >> "$HOME/.zshrc"
      fi
    fi
    
    # Verify installation
    if command -v fnm >/dev/null 2>&1; then
      log_success "FNM installed successfully: $(fnm --version)"
      log_info "To use FNM, restart your shell or run: source ~/.bashrc (or ~/.zshrc)"
      log_info "Then install Node.js with: fnm install --lts"
    else
      log_warning "FNM installed but not available in current shell. Please restart your shell."
      log_info "After restart, install Node.js with: fnm install --lts"
    fi
  else
    log_error "FNM installation failed."
    return 1
  fi
}

install_proxy() {
  log_info "Installing Proxy..."
  
  # Check if Squid proxy is already installed
  if sudo netstat -lntp 2>/dev/null | grep -q squid; then
    log_success "Proxy is already installed. Skipping..."
    return 0
  fi

  sudo apt -y update
  sudo apt -y upgrade
  sudo wget https://raw.githubusercontent.com/serverok/squid-proxy-installer/master/squid3-install.sh
  sudo bash squid3-install.sh -y
  # squid-add-user
  sudo /usr/bin/htpasswd -b -c /etc/squid/passwd "$PROXY_USER" "$PROXY_PASS"
  sudo systemctl reload squid
  rm -rf squid3-install.sh
  sudo apt install net-tools -y
  log_success "Proxy installed successfully."
}

change_proxy_port() {
  local new_port=$1
  
  if ! validate_port "$new_port" "Proxy"; then
    return 1
  fi
  
  log_info "Configuring proxy port to $new_port..."
  sudo sed -i "s/^http_port .*/http_port $new_port/" /etc/squid/squid.conf
  sudo systemctl reload squid
  sudo netstat -lntp 2>/dev/null || true
  log_success "Proxy port configured to $new_port."
}

install_docker() {
  log_info "Installing Docker..."
  curl -fsSL https://get.docker.com -o get-docker.sh
  sudo sh get-docker.sh
  rm get-docker.sh
  sudo usermod -aG docker $USER
  
  # Restart the Docker service
  sudo systemctl restart docker
  
  # Use sudo here: group membership does not apply to this shell until a new login.
  # (newgrp + heredoc is unreliable; plain docker would hit permission denied on docker.sock.)
  log_info "$(sudo docker --version)"
  if sudo docker info &>/dev/null; then
    log_info "Docker daemon is reachable (server $(sudo docker version -f '{{.Server.Version}}' 2>/dev/null))."
  fi
  
  log_info "Ensuring traefik_network exists..."
  if sudo docker network ls --format '{{.Name}}' | grep -qx 'traefik_network'; then
    log_success "Docker network 'traefik_network' already exists"
  else
    if sudo docker network create traefik_network 2>/dev/null; then
      log_success "Docker network 'traefik_network' created"
    else
      log_info "⚠️  Could not create traefik_network (not critical). Create it later: sudo docker network create traefik_network"
    fi
  fi
  
  log_success "Docker installed successfully."
  log_info "Your user was added to the 'docker' group. Open a new SSH session (or run: newgrp docker) before using docker without sudo."
}

install_tailscale() {
  if ! command -v curl >/dev/null 2>&1; then
    log_info "curl not found; installing curl..."
    sudo apt update -y
    sudo apt install -y curl
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log_error "curl is required but could not be installed."
    return 1
  fi
  # Tailscale docs: curl -fsSL https://tailscale.com/install.sh | sh (here: sudo sh when not root)
  log_info "Installing Tailscale: curl -fsSL https://tailscale.com/install.sh | sh"
  curl -fsSL https://tailscale.com/install.sh | sudo sh || return 1
  if ! command -v tailscale >/dev/null 2>&1; then
    log_error "tailscale not found in PATH after install."
    return 1
  fi
  log_success "Tailscale install finished. Next: sudo tailscale up"
}

install_python() {
  log_info "Installing Python..."
  sudo apt -y update
  sudo apt -y install python3 python3-pip

  # config python
  sudo update-alternatives --install /usr/bin/python python /usr/bin/python3 1
  sudo update-alternatives --config python
  python -V
  log_success "Python installed successfully."
}

cleanup() {
  log_info "Removing unused packages and cleaning apt cache..."
  sudo apt autoremove -y
  sudo apt autoclean
  sudo apt clean
  log_success "Cleanup completed successfully."
}

configure_firewall() {
  log_info "Configuring firewall (UFW)..."
  
  # Install UFW if not already installed
  sudo apt install -y ufw
  
  # Set default policies - ALLOW ALL INCOMING
   sudo ufw default allow incoming
   sudo ufw default allow outgoing
  
  # Enable firewall
  sudo ufw --force enable
  
  log_success "Firewall configured successfully (all incoming traffic allowed)."
}

configure_ssh_security() {
  log_info "Configuring SSH security..."

  # Backup original SSH config
  sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup.$(date +%Y%m%d_%H%M%S)
  
  # Configure SSH security settings
  # Disable root login
  sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
  
  # Disable password authentication (KEY ONLY!)
  sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  
  # Enable public key authentication
  sudo sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
  
  # Ensure changes are applied (add if not exists)
  grep -q "^PermitRootLogin" /etc/ssh/sshd_config || echo "PermitRootLogin no" | sudo tee -a /etc/ssh/sshd_config
  grep -q "^PasswordAuthentication" /etc/ssh/sshd_config || echo "PasswordAuthentication no" | sudo tee -a /etc/ssh/sshd_config
  grep -q "^PubkeyAuthentication" /etc/ssh/sshd_config || echo "PubkeyAuthentication yes" | sudo tee -a /etc/ssh/sshd_config
  
  # Restart SSH service
  sudo systemctl restart $SSH_SERVICE
  
  log_success "SSH security configured successfully."
  log_warning "Password authentication is now DISABLED - SSH keys required!"
}

install_fail2ban() {
  log_info "Installing and configuring Fail2ban..."
  
  # Update package list
  sudo apt update -y
  
  # Install Fail2ban first
  log_info "Installing Fail2ban..."
  sudo apt install -y fail2ban
  
  # Check if fail2ban was installed successfully
  if ! command -v fail2ban-client >/dev/null 2>&1; then
    log_error "Failed to install Fail2ban. Please check your package manager."
    return 1
  fi
  
  log_success "Fail2ban installed successfully"
  
  # Check if config files exist
  if [ ! -f "$CONFIG_DIR/fail2ban-jail.local" ]; then
    log_warning "Warning: fail2ban-jail.local not found in $CONFIG_DIR"
    log_info "Using default Fail2ban configuration"
    
    # Enable and start with default config
    sudo systemctl enable fail2ban
    sudo systemctl start fail2ban
    
    if systemctl is-active --quiet fail2ban; then
      log_success "Fail2ban installed and running with default configuration"
      echo "📊 Active jails:"
      sudo fail2ban-client status
    else
      log_error "Fail2ban failed to start"
      return 1
    fi
    return 0
  fi
  
  # Backup existing jail.local if it exists
  if [ -f /etc/fail2ban/jail.local ]; then
    sudo cp /etc/fail2ban/jail.local /etc/fail2ban/jail.local.backup.$(date +%Y%m%d_%H%M%S)
    log_success "Existing jail.local backed up"
  fi
  
  # Copy configuration files
  log_info "Copying configuration files from fail2ban-configs/..."
  
  # Copy jail configuration
  if sudo cp "$CONFIG_DIR/fail2ban-jail.local" /etc/fail2ban/jail.local 2>/dev/null; then
    log_success "Copied jail.local"
  else
    log_error "Failed to copy jail.local"
    return 1
  fi
  
  # Start and enable Fail2ban
  sudo systemctl enable fail2ban
  sudo systemctl restart fail2ban
  
  # Wait for service to start
  sleep 2
  
  # Verify installation
  if systemctl is-active --quiet fail2ban; then
    echo ""
    log_success "Fail2ban installed and configured successfully."
    echo ""
    echo "📊 Active jails:"
    sudo fail2ban-client status
    echo ""
    
    echo "🔒 Security Configuration:"
    echo "  • SSH: 5 attempts → 1 day ban"
    echo "  • Recidive: 2 bans in 7 days → 30 day ban"
    echo ""
    echo "📁 Configuration loaded from: $CONFIG_DIR"
  else
    log_error "Fail2ban installation failed."
    sudo journalctl -xeu fail2ban.service --no-pager -n 20
    return 1
  fi
}

ask_install() {
  local component="$1"
  local response
  
  while true; do
    echo -e "\n❓ Install $component? (y/N): "
    read -r response
    
    # Default to 'n' if empty
    response=${response:-n}
    
    case "$response" in
      [yY][eE][sS]|[yY]) return 0 ;;
      [nN][oO]|[nN]) return 1 ;;
      *) echo "⚠️  Invalid input. Please enter 'y' for yes or 'n' for no." ;;
    esac
  done
}

new_vps_setup() {
  echo "🚀 Starting interactive VPS setup..."
  echo "Please answer all questions first, then installation will begin."
  echo ""
  
  # Declare associative array for user choices
  declare -A CHOICES
  
  # ========== ASK ALL QUESTIONS FIRST ==========
  
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  CONFIGURATION QUESTIONS"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  
  # Password
  echo "💾 Password:"
  if ask_install "Change password (requires USER_PASSWORD environment variable)"; then
    CHOICES["password"]="yes"
  else
    CHOICES["password"]="no"
  fi
  
  # System updates
  echo ""
  echo "📦 Step 1/8: System Updates & Essential Packages"
  if ask_install "system package upgrades"; then
    CHOICES["apt_update"]="yes"
  else
    CHOICES["apt_update"]="no"
  fi
  if ask_install "basic tools (git, tmux, wget, curl, htop)"; then
    CHOICES["basic_tools"]="yes"
  else
    CHOICES["basic_tools"]="no"
  fi
  
  # Security
  echo ""
  echo "🔒 Step 2/8: Security Configuration"
  if ask_install "UFW Firewall"; then
    CHOICES["firewall"]="yes"
  else
    CHOICES["firewall"]="no"
  fi
  if ask_install "SSH Security (key-only, no password)"; then
    CHOICES["ssh_security"]="yes"
  else
    CHOICES["ssh_security"]="no"
  fi
  if ask_install "Fail2ban (intrusion prevention)"; then
    CHOICES["fail2ban"]="yes"
  else
    CHOICES["fail2ban"]="no"
  fi
  
  # Development tools
  echo ""
  echo "🛠️ Step 3/8: Development Tools"
  if ask_install "FNM (Fast Node Manager)"; then
    CHOICES["fnm"]="yes"
  else
    CHOICES["fnm"]="no"
  fi
  if ask_install "Python"; then
    CHOICES["python"]="yes"
  else
    CHOICES["python"]="no"
  fi
  if ask_install "UV (Python package manager)"; then
    CHOICES["uv"]="yes"
  else
    CHOICES["uv"]="no"
  fi
  
  # Cloud and containers
  echo ""
  echo "☁️ Step 4/8: Cloud & File Management"
  if ask_install "Rclone (cloud storage sync)"; then
    CHOICES["rclone"]="yes"
  else
    CHOICES["rclone"]="no"
  fi
  if ask_install "Docker"; then
    CHOICES["docker"]="yes"
  else
    CHOICES["docker"]="no"
  fi
  if ask_install "Tailscale (mesh VPN)"; then
    CHOICES["tailscale"]="yes"
  else
    CHOICES["tailscale"]="no"
  fi
  
  # Proxy
  echo ""
  echo "🌐 Step 5/8: Proxy & Networking"
  if ask_install "Squid Proxy (port 31288)"; then
    CHOICES["proxy"]="yes"
  else
    CHOICES["proxy"]="no"
  fi
  
  # Shell improvements
  echo ""
  echo "🐚 Step 6/8: Shell & Terminal Improvements"
  if ask_install "Zsh shell"; then
    CHOICES["zsh"]="yes"
  else
    CHOICES["zsh"]="no"
  fi
  if ask_install "Zim framework (Zsh)"; then
    CHOICES["zimfw"]="yes"
  else
    CHOICES["zimfw"]="no"
  fi
  if ask_install "Bash-it framework (Bash)"; then
    CHOICES["bash-it"]="yes"
  else
    CHOICES["bash-it"]="no"
  fi
  if ask_install "Zoxide (smart cd)"; then
    CHOICES["zoxide"]="yes"
  else
    CHOICES["zoxide"]="no"
  fi
  if ask_install "Eza (modern ls)"; then
    CHOICES["eza"]="yes"
  else
    CHOICES["eza"]="no"
  fi
  if ask_install "Fastfetch (system info)"; then
    CHOICES["fastfetch"]="yes"
  else
    CHOICES["fastfetch"]="no"
  fi
  
  # Additional tools
  echo ""
  echo "📱 Step 7/8: Additional Tools"
  if ask_install "qBittorrent"; then
    CHOICES["qbittorrent"]="yes"
  else
    CHOICES["qbittorrent"]="no"
  fi
  if ask_install "XRDP (remote desktop on port 33899)"; then
    CHOICES["xrdp"]="yes"
  else
    CHOICES["xrdp"]="no"
  fi
  if ask_install "Firefox"; then
    CHOICES["firefox"]="yes"
  else
    CHOICES["firefox"]="no"
  fi
  
  # ========== INSTALLATION PHASE ==========

  local any_yes=false
  local choice_key
  for choice_key in "${!CHOICES[@]}"; do
    if [ "${CHOICES[$choice_key]}" = "yes" ]; then
      any_yes=true
      break
    fi
  done

  if [ "$any_yes" = false ]; then
    echo ""
    echo "No changes made."
    return 0
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  STARTING INSTALLATION"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  
  # Password
  if [ "${CHOICES[password]}" = "yes" ]; then
    change_password
  fi
  
  # System updates
  if [ "${CHOICES[apt_update]}" = "yes" ]; then
    echo ""
    echo "📦 Upgrading system packages..."
    update_apt
  fi
  if [ "${CHOICES[basic_tools]}" = "yes" ]; then
    echo ""
    echo "📦 Installing basic tools..."
    install_basic_tools
  fi
  
  # Security
  if [ "${CHOICES[firewall]}" = "yes" ]; then
    echo ""
    echo "🔒 Configuring UFW Firewall..."
    configure_firewall
  fi
  if [ "${CHOICES[ssh_security]}" = "yes" ]; then
    echo ""
    echo "🔒 Configuring SSH Security..."
    configure_ssh_security
  fi
  if [ "${CHOICES[fail2ban]}" = "yes" ]; then
    echo ""
    echo "🔒 Installing Fail2ban..."
    install_fail2ban
  fi
  
  # Development tools
  if [ "${CHOICES[fnm]}" = "yes" ]; then
    echo ""
    echo "🛠️ Installing FNM (Fast Node Manager)..."
    install_fnm
  fi
  if [ "${CHOICES[python]}" = "yes" ]; then
    echo ""
    echo "🛠️ Installing Python..."
    install_python
  fi
  if [ "${CHOICES[uv]}" = "yes" ]; then
    echo ""
    echo "🛠️ Installing UV..."
    install_uv
  fi
  
  # Cloud and containers
  if [ "${CHOICES[rclone]}" = "yes" ]; then
    echo ""
    echo "☁️ Installing Rclone..."
    install_rclone
  fi
  if [ "${CHOICES[docker]}" = "yes" ]; then
    echo ""
    echo "☁️ Installing Docker..."
    install_docker
  fi
  if [ "${CHOICES[tailscale]}" = "yes" ]; then
    echo ""
    echo "☁️ Installing Tailscale..."
    install_tailscale
  fi
  
  # Proxy
  if [ "${CHOICES[proxy]}" = "yes" ]; then
    echo ""
    echo "🌐 Installing Squid Proxy..."
    install_proxy
    change_proxy_port "31288"
  fi
  
  # Shell improvements
  if [ "${CHOICES[zsh]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Zsh..."
    install_zsh
  fi
  if [ "${CHOICES[zimfw]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Zim framework..."
    install_zimfw
  fi
  if [ "${CHOICES[bash-it]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Bash-it framework..."
    install_bash_it
  fi
  if [ "${CHOICES[zoxide]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Zoxide..."
    install_zoxide
  fi
  if [ "${CHOICES[eza]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Eza..."
    install_eza
  fi
  if [ "${CHOICES[fastfetch]}" = "yes" ]; then
    echo ""
    echo "🐚 Installing Fastfetch..."
    install_fastfetch
  fi
  
  # Additional tools
  if [ "${CHOICES[qbittorrent]}" = "yes" ]; then
    echo ""
    echo "📱 Installing qBittorrent..."
    install_qbittorrent
  fi
  if [ "${CHOICES[xrdp]}" = "yes" ]; then
    echo ""
    echo "📱 Installing XRDP..."
    install_xrdp
    change_xrdp_port 33899
  fi
  if [ "${CHOICES[firefox]}" = "yes" ]; then
    echo ""
    echo "📱 Installing Firefox..."
    install_firefox
  fi
  
  # Cleanup only after an upgrade or a package install
  local needs_cleanup=false
  for choice_key in apt_update basic_tools firewall fail2ban fnm python uv rclone docker tailscale proxy zsh zimfw bash-it zoxide eza fastfetch qbittorrent xrdp firefox; do
    if [ "${CHOICES[$choice_key]}" = "yes" ]; then
      needs_cleanup=true
      break
    fi
  done

  if [ "$needs_cleanup" = true ]; then
    echo ""
    echo "🧹 Step 8/8: Final Cleanup"
    cleanup
  fi
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "✅ Interactive VPS setup completed!"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
}

# Check if no parameters passed - run interactive VPS setup by default
if [ $# -eq 0 ]; then
    new_vps_setup
    exit 0
fi

# =============================================================================
# COMPONENT REGISTRY - Maps flags to installer functions
# =============================================================================

declare -A COMPONENT_REGISTRY=(
  ["apt-update"]="update_apt"
  ["basic-tools"]="install_basic_tools"
  ["set-password"]="change_password"
  ["firewall"]="configure_firewall"
  ["ssh-security"]="configure_ssh_security"
  ["fail2ban"]="install_fail2ban"
  ["fnm"]="install_fnm"
  ["python"]="install_python"
  ["uv"]="install_uv"
  ["rclone"]="install_rclone"
  ["docker"]="install_docker"
  ["tailscale"]="install_tailscale"
  ["proxy"]="install_proxy"
  ["zsh"]="install_zsh"
  ["zimfw"]="install_zimfw"
  ["bash-it"]="install_bash_it"
  ["zoxide"]="install_zoxide"
  ["eza"]="install_eza"
  ["fastfetch"]="install_fastfetch"
  ["qbittorrent"]="install_qbittorrent"
  ["xrdp"]="install_xrdp"
  ["firefox"]="install_firefox"
  ["verify-xrdp"]="verify_xrdp_setup"
)

declare -A COMPONENT_DESCRIPTIONS=(
  ["apt-update"]="System updates"
  ["basic-tools"]="Basic tools (git, tmux, wget, curl, etc.)"
  ["set-password"]="Password change"
  ["firewall"]="UFW Firewall"
  ["ssh-security"]="SSH Security (key-only)"
  ["fail2ban"]="Fail2ban intrusion prevention"
  ["fnm"]="Node.js (FNM - Fast Node Manager)"
  ["python"]="Python"
  ["uv"]="UV Python package manager"
  ["rclone"]="Rclone cloud storage sync"
  ["docker"]="Docker"
  ["tailscale"]="Tailscale mesh VPN"
  ["proxy"]="Squid proxy server"
  ["zsh"]="Zsh shell"
  ["zimfw"]="Zim framework"
  ["bash-it"]="Bash-it framework"
  ["zoxide"]="Zoxide (smart cd)"
  ["eza"]="Eza (ls replacement)"
  ["fastfetch"]="Fastfetch system info"
  ["qbittorrent"]="qBittorrent"
  ["xrdp"]="XRDP remote desktop"
  ["firefox"]="Firefox browser"
  ["verify-xrdp"]="XRDP verification"
)

# =============================================================================
# UNIFIED INSTALLATION RUNNER
# =============================================================================

run_installations() {
  local install_performed=false
  local needs_cleanup=false
  local -a installed_components=()
  
  # Run installations in order
  for component in apt-update basic-tools set-password firewall ssh-security fail2ban \
                   fnm python uv rclone docker tailscale proxy zsh zimfw bash-it zoxide eza fastfetch \
                   qbittorrent xrdp firefox verify-xrdp; do
    
    if [ "${FLAGS[$component]:-false}" = "true" ]; then
      local func="${COMPONENT_REGISTRY[$component]}"
      
      if [ -n "$func" ]; then
        log_info "Running: $component"
        
        # Execute the installer function
        if $func; then
          installed_components+=("${COMPONENT_DESCRIPTIONS[$component]}")
          install_performed=true
          case "$component" in
            set-password|ssh-security|verify-xrdp) ;;
            *) needs_cleanup=true ;;
          esac
        else
          log_warning "Failed to install: $component (continuing...)"
        fi
        
        # Handle post-installation configuration
        case "$component" in
          xrdp)
            if [ "${VALUES[xrdp-port]}" != "$DEFAULT_XRDP_PORT" ]; then
              change_xrdp_port "${VALUES[xrdp-port]}"
            else
              change_xrdp_port 33899
            fi
            ;;
          proxy)
            if [ "${VALUES[proxy-port]}" != "$DEFAULT_PROXY_PORT" ]; then
              change_proxy_port "${VALUES[proxy-port]}"
            fi
            ;;
        esac
      fi
    fi
  done
  
  # Clean package cache only after an upgrade or a package install
  if [ "$install_performed" = true ]; then
    if [ "$needs_cleanup" = true ]; then
      echo ""
      cleanup
    fi
    echo ""
    echo "🎉 Installation completed successfully!"
    echo "📋 Summary of what was installed:"
    for component in "${installed_components[@]}"; do
      echo "  • $component"
    done
    echo ""
  else
    log_info "No installation performed."
    echo ""
    echo "Available flags:"
    echo "  -fnm -python -uv -docker -rclone -proxy [-proxy-port=PORT]"
    echo "  -zsh -zimfw -zoxide -eza -fastfetch"
    echo "  -xrdp [-xrdp-port=PORT] -firefox -qbittorrent"
    echo "  -firewall -ssh-security -fail2ban"
    echo "  -tailscale"
    echo "  -basic-tools -apt-update -set-password -verify-xrdp"
    echo ""
    echo "Example: ./setup.sh -docker -python -zsh -firewall"
  fi
}

# --- Run Installs ---
run_installations


