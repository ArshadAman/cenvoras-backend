#!/bin/bash
# Setup SSL certificates for Cenvoras .app domains
# Run this on your production DigitalOcean droplet

set -e

DOMAINS=("api.cenvora.app" "dev.cenvora.app" "devapi.cenvora.app")
SELF_SIGNED_DIR="/etc/nginx/self-signed"
CERTBOT_DIR="/var/www/certbot"

echo "=== Cenvoras SSL Bootstrap Setup ==="
echo ""

# 1. Create directories
echo "Step 1: Creating directories..."
mkdir -p "$SELF_SIGNED_DIR"
mkdir -p "$CERTBOT_DIR"

# 2. Generate self-signed certificate (bootstrap)
echo "Step 2: Generating self-signed certificate (90-day validity)..."
openssl req -x509 -nodes -days 90 -newkey rsa:2048 \
  -keyout "$SELF_SIGNED_DIR/privkey.pem" \
  -out "$SELF_SIGNED_DIR/fullchain.pem" \
  -subj "/CN=api.cenvora.app/O=Cenvoras/C=US"

echo "✓ Self-signed certificate created"
echo "   Private key: $SELF_SIGNED_DIR/privkey.pem"
echo "   Certificate: $SELF_SIGNED_DIR/fullchain.pem"

# 3. Install Certbot
echo ""
echo "Step 3: Installing Certbot..."
apt-get update -qq
apt-get install -y -qq certbot python3-certbot-nginx

echo "✓ Certbot installed"

# 4. Create renewal hook
echo ""
echo "Step 4: Creating certificate renewal hook..."
mkdir -p /etc/letsencrypt/renewal-hooks/post

cat > /etc/letsencrypt/renewal-hooks/post/reload-nginx.sh <<'EOFHOOK'
#!/bin/bash
echo "$(date): Reloading Nginx after certificate renewal..."
systemctl reload nginx
echo "$(date): Nginx reloaded successfully"
EOFHOOK

chmod +x /etc/letsencrypt/renewal-hooks/post/reload-nginx.sh
echo "✓ Renewal hook created"

# 5. Setup cron for automatic renewal
echo ""
echo "Step 5: Setting up automatic certificate renewal..."
echo "0 3 * * * root /usr/bin/certbot renew --quiet --post-hook 'systemctl reload nginx'" | tee /etc/cron.d/certbot-cenvoras > /dev/null

echo "✓ Cron job created (runs daily at 3 AM)"

# 6. Create interactive Certbot script
echo ""
echo "Step 6: Creating Let's Encrypt certificate request script..."

cat > /root/obtain-letsencrypt-certs.sh <<'EOFCERT'
#!/bin/bash
# Interactive script to obtain Let's Encrypt certificates via DNS-01 challenge

DOMAINS="api.cenvora.app,dev.cenvora.app,devapi.cenvora.app"
EMAIL="${1:-your-email@domain.com}"

echo "=== Obtaining Let's Encrypt Certificates ==="
echo ""
echo "Domains: $DOMAINS"
echo "Email: $EMAIL"
echo ""
echo "⚠️  IMPORTANT: DNS-01 Challenge"
echo ""
echo "When Certbot prompts, you will need to:"
echo "1. Log in to your DNS provider (Namecheap, Cloudflare, etc.)"
echo "2. Add a TXT record: _acme-challenge.api.cenvora.app = <token>"
echo "3. Wait 5-15 minutes for DNS propagation"
echo "4. Press Enter in Certbot to verify"
echo ""
echo "Domains to verify:"
for domain in api.cenvora.app dev.cenvora.app devapi.cenvora.app; do
  echo "  - _acme-challenge.$domain"
done
echo ""
read -p "Ready? Press Enter to continue..."
echo ""

certbot certonly --manual --preferred-challenges=dns \
  -d api.cenvora.app \
  -d dev.cenvora.app \
  -d devapi.cenvora.app \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email

echo ""
echo "=== Certificate Obtained Successfully ==="
echo ""
echo "Certificates located at:"
echo "  /etc/letsencrypt/live/api.cenvora.app/fullchain.pem"
echo "  /etc/letsencrypt/live/api.cenvora.app/privkey.pem"
echo ""
echo "Next steps:"
echo "1. Edit /etc/nginx/conf.d/default.conf (or nginx/default.conf in docker)"
echo "2. Comment out:"
echo "   # ssl_certificate /etc/nginx/self-signed/fullchain.pem;"
echo "   # ssl_certificate_key /etc/nginx/self-signed/privkey.pem;"
echo ""
echo "3. Uncomment:"
echo "   ssl_certificate /etc/letsencrypt/live/api.cenvora.app/fullchain.pem;"
echo "   ssl_certificate_key /etc/letsencrypt/live/api.cenvora.app/privkey.pem;"
echo ""
echo "4. Reload Nginx:"
echo "   nginx -t && systemctl reload nginx"
echo ""
echo "5. Verify:"
echo "   openssl s_client -connect api.cenvora.app:443 -servername api.cenvora.app"
EOFCERT

chmod +x /root/obtain-letsencrypt-certs.sh
echo "✓ Script created: /root/obtain-letsencrypt-certs.sh"

echo ""
echo "=== Bootstrap Complete ==="
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Update your Nginx configuration to use self-signed certificates:"
echo "   (Already updated in cenvoras/nginx/default.conf if using Docker)"
echo ""
echo "2. Test Nginx configuration:"
echo "   nginx -t"
echo ""
echo "3. Reload Nginx:"
echo "   systemctl reload nginx"
echo ""
echo "4. Verify HTTPS works (self-signed warning is expected):"
echo "   curl -k https://api.cenvora.app -I"
echo ""
echo "5. Once stable, run to obtain Let's Encrypt certificates:"
echo "   /root/obtain-letsencrypt-certs.sh"
echo ""
echo "⚠️  IMPORTANT:"
echo "  - Self-signed certs expire in 90 days"
echo "  - Switch to Let's Encrypt certificates before expiry"
echo "  - Once done, renewal is automatic via cron"
echo ""
