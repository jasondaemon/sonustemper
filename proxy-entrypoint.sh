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

# Require a shared secret (must match app env)
if [ -z "$PROXY_SHARED_SECRET" ]; then
  echo "Missing PROXY_SHARED_SECRET. Set it in .env for both app and proxy." >&2
  exit 1
fi
export PROXY_SHARED_SECRET

# Render shared secret into nginx config (write via temp to avoid busy file)
if [ -f /etc/nginx/conf.d/default.conf ]; then
  # compute hash to send in header
  SECRET_HASH=$(printf '%s' "$PROXY_SHARED_SECRET" | sha256sum | awk '{print $1}')
  esc_hash=$(printf '%s' "$SECRET_HASH" | sed -e 's/[\\/$&\"]/\\&/g')
  tmpdir="/tmp/sonustemper"
  mkdir -p "$tmpdir"
  tmpfile="$tmpdir/default.conf.$$"
  cp /etc/nginx/conf.d/default.conf "$tmpfile"
  sed -i "s/__PROXY_SHARED_SECRET_HASH__/${esc_hash}/g" "$tmpfile"
  # Overwrite in place (handle overlay FS by writing via cat)
  if ! cat "$tmpfile" > /etc/nginx/conf.d/default.conf; then
    echo "Failed to write proxy shared secret hash into nginx config" >&2
    exit 1
  fi
  rm -f "$tmpfile"
fi

exec nginx -g 'daemon off;'
