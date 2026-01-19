#!/bin/sh
set -e

AUTH_CONF="/etc/nginx/conf.d/auth.conf"
HTPASS="/etc/nginx/conf.d/.htpasswd"

: "${BASIC_AUTH_ENABLED:=1}"
: "${BASIC_AUTH_USER:=admin}"
: "${BASIC_AUTH_PASS:=CHANGEME}"
: "${PROXY_SHARED_SECRET:=}"

# Ensure htpasswd is available
if ! command -v htpasswd >/dev/null 2>&1; then
  apk add --no-cache apache2-utils >/dev/null
fi

if [ "$BASIC_AUTH_ENABLED" = "1" ]; then
  if [ -z "$BASIC_AUTH_PASS" ] || [ "$BASIC_AUTH_PASS" = "CHANGEME" ]; then
    echo "ERROR: BASIC_AUTH_PASS is not set or still default ('CHANGEME'). Set it in .env." >&2
    exit 1
  fi
  htpasswd -bc "$HTPASS" "$BASIC_AUTH_USER" "$BASIC_AUTH_PASS"
  cat > "$AUTH_CONF" <<EOF
auth_basic "Protected";
auth_basic_user_file $HTPASS;
EOF
else
  echo "# basic auth disabled" > "$AUTH_CONF"
fi

# Require a shared secret (must match app env)
if [ -z "$PROXY_SHARED_SECRET" ] || [ "$PROXY_SHARED_SECRET" = "changeme-proxy" ]; then
  echo "ERROR: PROXY_SHARED_SECRET is not set or still default ('changeme-proxy'). Set a strong value in .env." >&2
  exit 1
fi
export PROXY_SHARED_SECRET

# Render nginx config from template with raw secret in header (escape $)
if [ -f /etc/nginx/templates/nginx.conf.template ]; then
  cp /etc/nginx/templates/nginx.conf.template /etc/nginx/conf.d/default.conf
  # Escape chars that sed/nginx interpret; keep literal '$' in the header value
  esc_secret=$(printf '%s' "$PROXY_SHARED_SECRET" | sed -e 's/[\\/&]/\\&/g' -e 's/\$/$$/g')
  sed -i "s/__PROXY_SHARED_SECRET__/${esc_secret}/g" /etc/nginx/conf.d/default.conf
  echo "[proxy] rendered config with PROXY_SHARED_SECRET len=${#PROXY_SHARED_SECRET}"
else
  echo "nginx.conf.template missing" >&2
  exit 1
fi

# Wait for app host to resolve before starting nginx (up to 30s)
for i in $(seq 1 30); do
  if getent hosts sonustemper >/dev/null 2>&1; then
    break
  fi
  echo "[proxy] waiting for DNS of sonustemper (attempt $i)..."
  sleep 1
done

exec nginx -g 'daemon off;'
