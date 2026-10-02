#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with: sudo ./install.sh" >&2
  exit 1
fi

command -v ollama >/dev/null || { echo "Install Ollama first: https://ollama.com/download" >&2; exit 1; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv
ollama pull llama3

id metis &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin metis
chgrp metis /srv
chmod 775 /srv

mkdir -p /opt/metis
cp "$SRC"/main.py "$SRC"/model.py /opt/metis/
python3 -m venv /opt/metis/.venv
/opt/metis/.venv/bin/pip install -r "$SRC"/requirements.txt

cat > /etc/systemd/system/metis.service <<'EOF'
[Unit]
Description=Metis
After=network.target ollama.service

[Service]
User=metis
Group=metis
WorkingDirectory=/opt/metis
Environment=METIS_ROOT=/srv
Environment=HF_HOME=/var/cache/metis
CacheDirectory=metis
ExecStart=/opt/metis/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8040
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/srv
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable metis
systemctl restart metis

echo "Metis is running at http://127.0.0.1:8040"
