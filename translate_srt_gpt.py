#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""英語 SRT を GPT で日本語に翻訳するスクリプト。

SRT を「字幕ブロック」単位（連番・タイムスタンプ・セリフ群・空行）で分割し、
20 ブロックずつまとめて生成 AI に投げます。
"""

import os
import sys
import argparse
import time
from pathlib import Path
from typing import List

from openai import AzureOpenAI      # pip install openai>=1.0
import tqdm                         # pip install tqdm


def group_srt_blocks(lst: List[str], blocks_per_group: int):
    """SRT の行リストを「字幕ブロック」を単位としてまとめて返すジェネレータ。

    1 ブロック = 連番行 + タイムスタンプ行 + セリフ数行 + 空行
    という前提で、「空行が来たら 1 ブロック終了」とみなし、
    それを blocks_per_group 個ずつ連結して 1 グループとして yield します。
    """
    current_block: List[str] = []
    blocks: List[List[str]] = []

    for line in lst:
        current_block.append(line)
        # 空行で 1 ブロック終了とみなす
        if line.strip() == "":
            blocks.append(current_block)
            current_block = []

    # ファイル末尾に空行が無くても最後のブロックを拾う
    if current_block:
        blocks.append(current_block)

    # blocks_per_group 個ずつまとめて 1 リクエスト分のテキストにする
    for i in range(0, len(blocks), blocks_per_group):
        group = []
        for b in blocks[i:i + blocks_per_group]:
            group.extend(b)
        yield group
BLOCKS_PER_REQUEST = 20


# ───────────────── CLI ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input',      required=True, help='SRT file to translate')
parser.add_argument('-o', '--output_dir', required=True, help='Directory to write result')
parser.add_argument('--chunk', type=int, default=50, help='Lines per request (default: 50)')
args = parser.parse_args()

src_path = Path(args.input)
out_dir  = Path(args.output_dir)
out_dir.mkdir(parents=True, exist_ok=True)
dst_path = out_dir / f"ja-{src_path.name}"

# ── (1) 出力ファイルを必ずゼロから作り直す ──────────────────────────────
if dst_path.exists():
    dst_path.unlink()           # 既存ファイルを削除
dst_path.touch()                # 空ファイルを生成

# ─────────────── Azure OpenAI クライアント ──────────────────────────────────
client = AzureOpenAI(
    azure_endpoint=os.getenv("ENDPOINT_URL", "https://ks-ai-foundry.openai.azure.com/"),
    api_key       =os.getenv("AZURE_OPENAI_API_KEY"),
    api_version   ="2025-01-01-preview",
)
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-5-chat")

# ─────────────── システムプロンプト読み込み ─────────────────────────────────
prompt_path = Path("settings/system_prompt.txt")
if not prompt_path.is_file():
    print(f"[ERROR] System prompt not found: {prompt_path}", file=sys.stderr)
    sys.exit(1)

system_prompt = prompt_path.read_text(encoding='utf-8').strip()

# ─────────────── SRT 読み込み＆分割 ────────────────────────────────────────
lines = src_path.read_text(encoding='utf-8').splitlines(keepends=True)

def chunks(lst: List[str], n: int):
    """Yield n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

total_chunks = (len(lines) + args.chunk - 1) // args.chunk   # 進捗バー用

# ─────────────── GPT へ逐次リクエスト ───────────────────────────────────
for idx, block in enumerate(
    tqdm.tqdm(group_srt_blocks(lines, BLOCKS_PER_REQUEST),
          desc="Translating"), start=1):

    # f
    block_text = "\n" + "".join(block) + "\n"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": block_text}
    ]

    # ― リクエスト＋指数バックオフリトライ ―
    rsp, last_err = None, None
    for attempt in range(3):
        try:
            rsp = client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=0.3,
                max_tokens=2000
            )
            break        # 成功
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"[WARN] block {idx}: request failed (attempt {attempt+1}): {e}", file=sys.stderr)
            time.sleep(wait)

    if rsp is None:      # 3 回とも失敗
        content = f"[ERROR: {last_err}]"
        reason  = "exception"
    else:
        # ― レスポンス検証 ―
        choice  = rsp.choices[0] if rsp.choices else None
        content = getattr(choice.message, "content", None) if choice else None
        reason  = getattr(choice, "finish_reason", "unknown")

        if content is None:
            print(f"[WARN] block {idx}: skipped or filtered: finish_reason={reason}", file=sys.stderr)
            content = f"[BLOCKED: {reason}]"

        # ― 念のため、生成された本文から全角の句点「。」を除去してから書き出す ―
        #   GPT が指示に反して句点を付けてしまった場合でも、ここで落とすことで
        #   出力 SRT のポリシー（行末に句点を付けない）を守る。
        content = content.replace("。", "")

    # ― (2) 逐次追記＋ブロック間に必ず空行を 1 行入れる ―
    with dst_path.open('a', encoding='utf-8') as f_out:
        f_out.write('\n')         # 追加の空行
        f_out.write(content)
        f_out.write('\n')         # 追加の空行
        f_out.flush()

    tqdm.tqdm.write(f"block {idx}: finish_reason={reason}")

print(f"\n✅ Completed! → {dst_path.resolve()}")

