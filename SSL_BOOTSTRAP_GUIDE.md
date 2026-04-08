# Cenvoras SSL Bootstrap Guide for .app Domains

## Problem & Solution

**The Problem:**
- .app domains require HTTPS (HSTS-preload list)
- Let's Encrypt HTTP-01 validation requires HTTP access
- Domain is inaccessible without certificate = circular dependency

**The Solution:**
- Phase 1: Bootstrap with self-signed certificates (90-day validity)
- Phase 2: Get real Let's Encrypt certs via DNS-01 challenge (no HTTP needed)
- Phase 3: Automatic renewal every 90 days via cron

---

## Phase 1: Bootstrap with Self-Signed Certificates

### On Your Production Droplet

```bash
# 1. Copy setup script from backend folder
scp cenvoras/setup-ssl.sh root@your.droplet.ip:/root/

# 2. SSH into droplet
ssh root@your.droplet.ip

# 3. Run setup script
bash /root/setup-ssl.sh
```

This creates:
- Self-signed certificates at `/etc/nginx/self-signed/`
- Renewal hook at `/etc/letsencrypt/renewal-hooks/post/reload-nginx.sh`
- Cron job for automatic renewal
- Interactive script `/root/obtain-letsencrypt-certs.sh` for Let's Encrypt

### Update Nginx Configuration

Your `cenvoras/nginx/default.conf` already has the bootstrap paths set:

```nginx
ssl_certificate /etc/nginx/self-signed/fullchain.pem;
ssl_certificate_key /etc/nginx/self-signed/privkey.pem;
```

### Test Nginx

```bash
nginx -t                    # Validate config
systemctl reload nginx      # Apply changes
```

Your domains should now be accessible via HTTPS (self-signed warnings expected).

**Test from local machine:**
```bash
curl -k https://api.cenvora.app -I
# Should return 200 OK with warning about self-signed cert
```

---

## Phase 2: Obtain Real Let's Encrypt Certificates

### Prerequisites

You need DNS access to your domain registrar (Namecheap, Cloudflare, etc.) to add TXT records.

### Run Certbot Script

```bash
# On your droplet
/root/obtain-letsencrypt-certs.sh

# Or specify email:
/root/obtain-letsencrypt-certs.sh your-email@example.com
```

### During Certbot Execution

Certbot will ask you to add DNS TXT records. For example:

```
Please deploy a DNS TXT record under the name
_acme-challenge.api.cenvora.app with the following value:

abc123def456ghi789jkl012mno345pqr

Before continuing, verify the record is deployed.
```

**Add to DNS Provider (example for Namecheap):**

| Type | Name | Value |
|------|------|-------|
| TXT | `_acme-challenge` | `abc123def456ghi789jkl012mno345pqr` |

**Repeat for other domains:**
```
_acme-challenge.dev.cenvora.app = <token>
_acme-challenge.devapi.cenvora.app = <token>
```

**Wait for DNS propagation:**
```bash
# On your local machine, verify DNS is ready
nslookup -type=txt _acme-challenge.api.cenvora.app
# Should return the token you added
```

This typically takes 5-15 minutes. Once verified, press Enter in Certbot.

---

## Phase 3: Switch Nginx to Real Certificates

Once `certbot` succeeds, certificates are at:
```
/etc/letsencrypt/live/api.cenvora.app/fullchain.pem
/etc/letsencrypt/live/api.cenvora.app/privkey.pem
```

### Update Nginx Config

Edit `cenvoras/nginx/default.conf`:

**Comment out self-signed:**
```nginx
# ssl_certificate /etc/nginx/self-signed/fullchain.pem;
# ssl_certificate_key /etc/nginx/self-signed/privkey.pem;
```

**Uncomment Let's Encrypt:**
```nginx
ssl_certificate /etc/letsencrypt/live/api.cenvora.app/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/api.cenvora.app/privkey.pem;
```

### Reload Nginx

```bash
nginx -t                    # Test configuration
systemctl reload nginx      # Apply changes
```

### Verify Real Certificate

```bash
openssl s_client -connect api.cenvora.app:443 -servername api.cenvora.app < /dev/null | grep -A2 "Issuer:"

# Output should show:
# Issuer: CN = R3, O = Let's Encrypt, ...
```

Also verify in browser: ✅ Green lock, no warnings.

---

## Phase 4: Automatic Renewal

Certbot renewal runs automatically daily (set by `setup-ssl.sh` cron job).

### Monitor Renewal

```bash
# View all certificates
certbot certificates

# View renewal schedule
systemctl list-timers

# Test renewal process (dry-run, no actual renewal)
certbot renew --dry-run

# View renewal logs
tail -f /var/log/letsencrypt/letsencrypt.log
```

Renewal happens at 3 AM daily. Nginx auto-reloads via the hook script.

---

## Docker Integration

If running Nginx in Docker, mount the certificate volumes:

```yaml
services:
  nginx:
    image: nginx:latest
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro              # Let's Encrypt certs
      - /etc/nginx/self-signed:/etc/nginx/self-signed:ro  # Self-signed certs
      - /var/www/certbot:/var/www/certbot                 # ACME challenge dir
    depends_on:
      - web
```

---

## Troubleshooting

### Certificate File Not Found
```bash
# Check if setup ran
ls -la /etc/nginx/self-signed/
ls -la /etc/letsencrypt/live/api.cenvora.app/

# Regenerate if missing
bash /root/setup-ssl.sh
```

### DNS Validation Fails
```bash
# Verify DNS record exists and propagated
nslookup -type=txt _acme-challenge.api.cenvora.app

# If empty, add record to your DNS provider and wait 5-15 minutes

# If Certbot timed out, run again
/root/obtain-letsencrypt-certs.sh
```

### Nginx Won't Reload After Certificate Change
```bash
# Check syntax
nginx -t

# Check if Nginx process is running
ps aux | grep nginx

# Restart if needed
systemctl restart nginx

# Check logs
journalctl -u nginx -n 20
```

### HSTS Prevents Access with Self-Signed Cert
.app domains use HSTS. If you see "Cannot proceed" in browser:
- Use incognito/private window (new session)
- Or access from different device
- Or wait for HSTS cache to expire (max-age=31536000 = 1 year)

### Certificate Still Shows Self-Signed in Browser
```bash
# Browser might be caching old cert
# Hard refresh: Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows/Linux)

# Or clear browser cache completely
# Then access domain again
```

---

## Timeline

| Phase | Time Required | Action | Certificate Type |
|-------|---------------|--------|------------------|
| **1** | 5 min | Run `setup-ssl.sh` | Self-signed |
| **1** | 5 min | Update Nginx config, reload | Self-signed |
| **2** | 30 min | Add DNS records, wait propagation | Pending |
| **2** | 5 min | Run `obtain-letsencrypt-certs.sh` | Let's Encrypt |
| **3** | 5 min | Update Nginx config, reload | Let's Encrypt ✅ |
| **4** | - | Automatic renewal every 90 days | Let's Encrypt ✅ |

---

## Summary Commands

### Bootstrap
```bash
bash /root/setup-ssl.sh
nginx -t && systemctl reload nginx
curl -k https://api.cenvora.app -I
```

### Get Let's Encrypt
```bash
/root/obtain-letsencrypt-certs.sh your-email@example.com
# Add DNS records when prompted
# Wait for DNS propagation
# Press Enter in Certbot
```

### Switch to Production Certs
```bash
# Edit cenvoras/nginx/default.conf
# Comment out self-signed, uncomment Let's Encrypt
nginx -t && systemctl reload nginx
openssl s_client -connect api.cenvora.app:443 -servername api.cenvora.app
```

### Monitor Renewal
```bash
certbot certificates
certbot renew --dry-run
```

---

## Important Notes

1. **Self-signed validity:** 90 days
2. **Let's Encrypt validity:** 90 days (renewal starts at day 30)
3. **HSTS enforcement:** Once set, .app domains require valid HTTPS or become inaccessible
4. **Renewal automation:** Enabled by default, no manual intervention needed
5. **Email updates:** Certbot may send renewal reminder emails, but renewal happens automatically
6. **Downtime:** Renewal causes brief Nginx reload (usually <1 second)

---

## Questions?

Check logs at: `/var/log/letsencrypt/letsencrypt.log`
Check certs at: `certbot certificates`
