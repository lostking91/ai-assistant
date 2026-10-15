#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 config/*.json 编码为 *.bin（base64混淆），用法：
  python scripts/encode_config.py config/auth.prod.json
  python scripts/encode_config.py config/auth.test.json
"""
import base64, sys

def encode(src: str):
    dst = src.replace(".json", ".bin")
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(base64.b64encode(data))
    print(f"Written: {dst}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/encode_config.py <config.json>")
        sys.exit(1)
    encode(sys.argv[1])
