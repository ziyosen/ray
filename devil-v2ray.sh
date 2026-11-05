#!/bin/sh

#Config v2ray - VLESS + Trojan, both WebSocket, one tunnel
#FREE paths (bansos, anti-jual-ulang): VLESS 8082 /vless/free/kontak-telegram-@Bleszh, Trojan 8083 /trojan/free/kontak-telegram-@Bleszh
#VLESS: port $PORT, WS path /
#Trojan: port $TROJAN_PORT (default 8081), WS path /trojan, password $TROJAN_PASSWORD
#Tunnel ingress (1 tunnel, same hostname):
#  path /trojan -> http://localhost:8081
#  path /       -> http://localhost:8080
#DNS fix: explicit DNS 1.1.1.1/8.8.8.8, IPv4 only (avoid IPv6 AAAA breaking browsing)
#Sniffing: enabled on both inbounds so Xray can route by domain from TLS/HTTP

rm -rf /etc/xray/config.json

TROJAN_PORT=${TROJAN_PORT:-8081}

if [ -z "$TROJAN_PASSWORD" ]; then
  echo "TROJAN_PASSWORD not set, running VLESS only"
  cat << EOF > /etc/xray/config.json
{
  "dns": {
    "servers": ["1.1.1.1", "8.8.8.8"],
    "queryStrategy": "UseIPv4"
  },
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
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
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
  "dns": {
    "servers": ["1.1.1.1", "8.8.8.8"],
    "queryStrategy": "UseIPv4"
  },
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
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
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
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
      }
    },
    {
      "port": 8082,
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
          "path": "/vless/free/kontak-telegram-@Bleszh"
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
      }
    },
    {
      "port": 8083,
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
          "path": "/trojan/free/kontak-telegram-@Bleszh"
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
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
