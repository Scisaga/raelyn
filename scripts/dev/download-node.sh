#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

NODE_VERSION="${NODE_VERSION:-20.18.1}"

uname_s="$(uname -s | tr '[:upper:]' '[:lower:]')"
uname_m="$(uname -m)"

case "$uname_s" in
  linux) os="linux" ;;
  darwin) os="darwin" ;;
  *)
    echo "[download] unsupported OS: $uname_s" >&2
    exit 1
    ;;
esac

case "$uname_m" in
  x86_64|amd64) arch="x64" ;;
  arm64|aarch64) arch="arm64" ;;
  *)
    echo "[download] unsupported arch: $uname_m" >&2
    exit 1
    ;;
esac

mkdir -p bin tmp

base="https://nodejs.org/dist/v${NODE_VERSION}"
tgz="node-v${NODE_VERSION}-${os}-${arch}.tar.xz"
tgz_path="tmp/${tgz}"
sha_path="tmp/SHASUMS256.txt"

echo "[download] node v${NODE_VERSION} (${os}-${arch})"
echo "[download] fetching checksums -> ${sha_path}"
curl -fsSL -L "${base}/SHASUMS256.txt" -o "${sha_path}"

echo "[download] fetching tarball -> ${tgz_path}"
curl -fsSL -L "${base}/${tgz}" -o "${tgz_path}"

echo "[download] verifying sha256"
expected="$(grep -E "[[:space:]]${tgz}$" "${sha_path}" | head -n 1 | awk '{print $1}')"
if [[ -z "${expected}" ]]; then
  echo "[download] checksum not found for ${tgz} in SHASUMS256.txt" >&2
  exit 1
fi
actual="$(sha256sum "${tgz_path}" | awk '{print $1}')"
if [[ "${actual}" != "${expected}" ]]; then
  echo "[download] sha256 mismatch for ${tgz}" >&2
  echo "[download] expected=${expected}" >&2
  echo "[download] actual=${actual}" >&2
  exit 1
fi

echo "[download] extracting node -> bin/node"
rm -rf tmp/node-extract
mkdir -p tmp/node-extract
tar -xJf "${tgz_path}" -C tmp/node-extract --strip-components=1
cp -f tmp/node-extract/bin/node bin/node
chmod +x bin/node

echo "[download] ok: $(./bin/node --version)"
