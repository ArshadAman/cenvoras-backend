#!/bin/bash
# ==============================================================================
# Configure Host Nginx & Let's Encrypt SSL for newapi.cenvora.app
# Proxies requests to Cenvora web container on 127.0.0.1:8000
# ==============================================================================

set -e

DOMAIN="newapi.cenvora.app"
EMAIL="support@cenvora.app"
NGINX_CONF="/etc/nginx/sites-available/${DOMAIN}"

echo "=================================================="
echo "  Configuring Host Nginx for: ${DOMAIN}"
echo "=================================================="

# 1. Create Nginx virtual host configuration
echo "1. Writing Nginx reverse proxy configuration to ${NGINX_CONF}..."
cat > "${NGINX_CONF}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
EOF

# 2. Enable site by symlinking
echo "2. Enabling site in /etc/nginx/sites-enabled/..."
ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/

# 3. Test configuration and reload Nginx
echo "3. Testing and reloading Nginx..."
nginx -t
systemctl reload nginx

# 4. Obtain and install real Let's Encrypt SSL certificate
echo "4. Requesting Let's Encrypt SSL certificate via Certbot..."
certbot --nginx -d "${DOMAIN}" --agree-tos --email "${EMAIL}" --non-interactive --redirect

# 5. Reload Nginx with new certificate
systemctl reload nginx

echo ""
echo "=================================================="
echo "  🎉 SSL SETUP COMPLETE!"
echo "  URL: https://${DOMAIN}"
echo "  Testing health endpoint..."
echo "=================================================="

sleep 2
curl -I "https://${DOMAIN}/health" || curl -I "https://${DOMAIN}/api/users/login/"
