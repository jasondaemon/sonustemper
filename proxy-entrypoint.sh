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
    echo "Default credentials in use. Please set BASIC_AUTH_PASS in .env" >&2
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

if [ -z "$PROXY_SHARED_SECRET" ]; then
  # Generate a random default if not provided
  PROXY_SHARED_SECRET="$(head -c 24 /dev/urandom | base64 | tr -d '=+/[:space:]' | cut -c1-24)"
fi
export PROXY_SHARED_SECRET

# Render shared secret into nginx config (write via temp to avoid busy file)
if [ -f /etc/nginx/conf.d/default.conf ]; then
  esc_secret=$(printf '%s' "$PROXY_SHARED_SECRET" | sed -e 's/[\\/&]/\\&/g')
  tmpdir="/tmp/sonustemper"
  mkdir -p "$tmpdir"
  tmpfile="$tmpdir/default.conf.$$"
  cp /etc/nginx/conf.d/default.conf "$tmpfile"
  sed -i "s/__PROXY_SHARED_SECRET__/${esc_secret}/g" "$tmpfile"
  cp "$tmpfile" /etc/nginx/conf.d/default.conf
  rm -f "$tmpfile"
fi

exec nginx -g 'daemon off;'
