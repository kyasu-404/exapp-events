#!/bin/sh
set -eu
umask 077
exec python /scripts/supervise.py
