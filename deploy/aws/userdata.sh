#!/bin/bash
# EC2 user data (first boot) for the Tank Tactics server on Amazon Linux 2023.
# The AWS deploy workflow substitutes __FETCH_CONFIG_B64__ (deploy/aws/fetch-config.sh) and
# __INSTALL_B64__ (deploy/aws/install-env.sh, already rendered), both gzipped and base64-encoded
# (user data is limited to 16 KB).
set -euxo pipefail
install -m 644 /dev/null /etc/tank-tactics.env
echo '__FETCH_CONFIG_B64__' | base64 -d | gunzip > /usr/local/bin/tank-tactics-config
echo '__INSTALL_B64__' | base64 -d | gunzip > /usr/local/bin/tank-tactics-install
chmod 755 /usr/local/bin/tank-tactics-config /usr/local/bin/tank-tactics-install
# Settings from SSM Parameter Store. Google sign-in is required: if this fails, boot stops
# here, the game never starts, and the workflow's `up` check fails.
/usr/local/bin/tank-tactics-config
dnf install -y python3.12 python3.12-pip || dnf install -y python3.11 python3.11-pip
/usr/local/bin/tank-tactics-install production
/usr/local/bin/tank-tactics-install staging
