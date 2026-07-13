#!/usr/bin/env bash
# Встановлення Starlink Monitor на Raspberry Pi OS (Bookworm) / RPi Zero 2 W.
# Запускати з правами sudo: `sudo bash scripts/install.sh`
tar -xzf starlink-monitor.tar.gz
cd starlink-monitor
sudo bash scripts/install.sh