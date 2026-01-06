#!/bin/sh
set -e

AUTH_CONF="/etc/nginx/conf.d/auth.conf"
HTPASS="/etc/nginx/conf.d/.htpasswd"

: "${BASIC_AUTH_ENABLED:=1}"
: "${BASIC_AUTH_USER:=admin}"
: "${BASIC_AUTH_PASS:=CHANGEME}"

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

exec nginx -g 'daemon off;'
