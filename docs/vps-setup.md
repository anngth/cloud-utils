# VPS setup

Run on the Ubuntu VPS from the repo root:

```bash
./setup.sh
```

## Examples

```bash
./setup.sh -apt-update -basic-tools -firewall -ssh-security -fail2ban
./setup.sh -docker -fnm -python -uv
./setup.sh -zsh -zimfw -bash-it -zoxide -eza -fastfetch
./setup.sh -proxy -proxy-port=8080
./setup.sh -xrdp -xrdp-port=33899 -verify-xrdp
```

## Flags

`-apt-update`, `-basic-tools`, `-set-password`, `-firewall`, `-ssh-security`, `-fail2ban`, `-docker`, `-rclone`, `-proxy`, `-proxy-port=PORT`, `-fnm`, `-python`, `-uv`, `-zsh`, `-zimfw`, `-bash-it`, `-zoxide`, `-eza`, `-fastfetch`, `-qbittorrent`, `-xrdp`, `-xrdp-port=PORT`, `-firefox`, `-verify-xrdp`.

## Docker stack

On the VPS (repo root):

```bash
cp .env.example .env
docker network create traefik_network
touch acme.json && chmod 600 acme.json
docker compose up -d
```

Traefik, Portainer, Stirling PDF, and database services (`docker-compose.yml`).

## MTProto proxy

```bash
cd mtproto
bash save_random_hex.sh
docker compose up -d
```

## Rclone transfers

From your machine (via proxy to the VPS):

```bash
cd rclone
export PROXY_URL="http://user:pass@host:port"
bash transfer.sh
```

Windows:

```powershell
cd rclone
.\transfer.ps1
```
