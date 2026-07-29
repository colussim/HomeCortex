#!/bin/sh
set -eu

INSTALL_DIR=$1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
id homecortex >/dev/null 2>&1 || useradd --system --home /var/lib/homecortex --shell /usr/sbin/nologin homecortex
install -d -o homecortex -g homecortex /etc/homecortex /var/lib/homecortex /var/log/homecortex
cp -R "$INSTALL_DIR/config/." /etc/homecortex/
cp "$INSTALL_DIR/.env" /etc/homecortex/homecortex.env
chmod 600 /etc/homecortex/homecortex.env
chown -R homecortex:homecortex /etc/homecortex /var/lib/homecortex /var/log/homecortex

sed "s|__INSTALL_ROOT__|$INSTALL_DIR|g" \
  "$SCRIPT_DIR/homecortex-core.service.tmpl" \
  > /etc/systemd/system/homecortex-core.service
sed "s|__INSTALL_ROOT__|$INSTALL_DIR|g" \
  "$SCRIPT_DIR/homecortex-control.service.tmpl" \
  > /etc/systemd/system/homecortex-control.service
systemctl daemon-reload
echo "systemd units installed. Run: systemctl enable --now homecortex-core homecortex-control"
