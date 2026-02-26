#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p static/vendor
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

ver="3.15.3"
echo "[download] alpinejs@${ver} -> static/vendor/alpine.min.js"
curl -fsSL "https://registry.npmjs.org/alpinejs/-/alpinejs-${ver}.tgz" -o "$tmpdir/alpine.tgz"
tar -xzf "$tmpdir/alpine.tgz" -C "$tmpdir"
cp "$tmpdir/package/dist/cdn.min.js" static/vendor/alpine.min.js

echo "[download] done"

