#!/bin/sh

#Config v2ray - VLESS + Trojan, both WebSocket, one tunnel
#VLESS: port $PORT, WS path /
#Trojan: port $TROJAN_PORT (default 8081), WS path /trojan, password $TROJAN_PASSWORD
#Tunnel ingress (1 tunnel, same hostname):
#  path /trojan -> http://localhost:8081
#  path /       -> http://localhost:8080

rm -rf /etc/xray/config.json

TROJAN_PORT=${TROJAN_PORT:-8081}

if [ -z "$TROJAN_PASSWORD" ]; then
  echo "TROJAN_PASSWORD not set, running VLESS only"
  cat << EOF > /etc/xray/config.json
{
  "inbounds": [
    {
      "port": $PORT,
      "protocol": "vless",
      "settings": {
        "decryption": "none",
        "clients": [
          {
            "id": "$UUID"
          }
        ]
      },
      "streamSettings": {
        "network": "ws",
        "wsSettings": {
          "path": "/"
        }
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom"
    }
  ]
}
EOF
else
  cat << EOF > /etc/xray/config.json
{
  "inbounds": [
    {
      "port": $PORT,
      "protocol": "vless",
      "settings": {
        "decryption": "none",
        "clients": [
          {
            "id": "$UUID"
          }
        ]
      },
      "streamSettings": {
        "network": "ws",
        "wsSettings": {
          "path": "/"
        }
      }
    },
    {
      "port": $TROJAN_PORT,
      "protocol": "trojan",
      "settings": {
        "clients": [
          {
            "password": "$TROJAN_PASSWORD"
          }
        ]
      },
      "streamSettings": {
        "network": "ws",
        "wsSettings": {
          "path": "/trojan"
        }
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom"
    }
  ]
}
EOF
fi

#run v2ray server

xray -c /etc/xray/config.json
