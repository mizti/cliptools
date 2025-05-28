#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
英語 SRT を 50 行ごとに GPT-4o へ投げ、日本語 SRT を
逐次ファイルへ追記しながら完成させるスクリプト
usage: python translate_srt_gpt.py -i input.srt -o ./out
"""

import os
import sys
import argparse
import uuid
import time
from pathlib import Path
from typing import List

from openai import AzureOpenAI   # pip install openai>=1.0
from tqdm import tqdm            # pip install tqdm

# -------------------- CLI ----------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input',     required=True, help='SRT file to translate')
parser.add_argument('-o', '--output_dir',required=True, help='Directory to write result')
parser.add_argument('--chunk', type=int, default=50,   help='Lines per request (default: 50)')
args = parser.parse_args()

src_path = Path(args.input)
out_dir  = Path(args.output_dir)
out_dir.mkdir(parents=True, exist_ok=True)
dst_path = out_dir / f"ja-{src_path.name}"

# -------------------- Azure OpenAI クライアント ------------------------------
client = AzureOpenAI(
    azure_endpoint=os.getenv("ENDPOINT_URL",          "https://ks-ai-foundry.openai.azure.com/"),
    api_key       =os.getenv("AZURE_OPENAI_API_KEY"),
    api_version   ="2025-01-01-preview",
)
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-4o")

# -------------------- システムプロンプト --------------------------------------
prompt_path = Path("settings/system_prompt.txt")
if not prompt_path.is_file():
    print(f"[ERROR] System prompt not found: {prompt_path}", file=sys.stderr)
    sys.exit(1)

system_prompt = prompt_path.read_text(encoding='utf-8').strip()

# -------------------- SRT 読み込み＆分割 --------------------------------------
lines = src_path.read_text(encoding='utf-8').splitlines(keepends=True)

def chunks(lst: List[str], n: int):
    """Yields successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# ★ 出力ファイルを空で初期化
with dst_path.open('w', encoding='utf-8'):
    pass

# -------------------- GPT-4o へ逐次リクエスト -------------------------------
for block in tqdm(list(chunks(lines, args.chunk)), desc="Translating"):
    block_text = "".join(block)
    messages = [
        {"role": "system",  "content": system_prompt},
        {"role": "user",    "content": block_text}
    ]

    # --- リクエスト＋指数バックオフリトライ ---
    for attempt in range(3):
        try:
            rsp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0.3,
                max_tokens=2000
            )
            break
        except Exception as e:
            wait = 2 ** attempt
            print(f"[WARN] request failed (attempt {attempt+1}): {e}", file=sys.stderr)
            if attempt == 2:
                print("[ERROR] 最大リトライに到達しました。処理を中止します。", file=sys.stderr)
                sys.exit(1)
            time.sleep(wait)

    # --- レスポンス検証 ---
    choice = rsp.choices[0] if rsp.choices else None
    content = getattr(choice.message, "content", None) if choice else None
    reason  = getattr(choice, "finish_reason", "unknown")

    if content is None:
        # ブロックや長さ超過などで None が返った場合のプレースホルダー
        print(f"[WARN] block skipped or filtered: finish_reason={reason}", file=sys.stderr)
        content = f"[BLOCKED: {reason}]"

    # --- 逐次追記 ---
    with dst_path.open('a', encoding='utf-8') as f_out:
        f_out.write(content)
        # 元の行末を保持するので改行はプロンプト依存
        f_out.flush()

print(f"\n✅ Completed! → {dst_path.resolve()}")

