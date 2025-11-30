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

from utils.srt_parser import SRTBlock, blocks_to_text, parse_srt_blocks, validate_srt


# ───────────────── CLI ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input',      required=True, help='SRT file to translate')
parser.add_argument('-o', '--output_dir', required=True, help='Directory to write result')
parser.add_argument('--chunk', type=int, default=50, help='Lines per request (default: 50)')
args = parser.parse_args()

src_path = Path(args.input)
out_dir  = Path(args.output_dir)
out_dir.mkdir(parents=True, exist_ok=True)

# 出力ファイル名のポリシー:
# - 入力ファイル名に "en-US" が含まれている場合:
#     Speaker1_en-US.srt           -> Speaker1_ja-JP.srt
#     Speaker1_en-US_fixed.srt     -> Speaker1_ja-JP_fixed.srt
#   のように、"en-US" を "ja-JP" に差し替える。
# - それ以外の場合:
#     従来通り "ja-<元ファイル名>" を先頭に付ける。
name_str = src_path.name
if "en-US" in name_str:
    dst_name = name_str.replace("en-US", "ja-JP")
else:
    dst_name = f"ja-{name_str}"

dst_path = out_dir / dst_name

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
src_text = src_path.read_text(encoding='utf-8')
blocks: List[SRTBlock] = parse_srt_blocks(src_text)
if not blocks:
    print("[ERROR] No parsable SRT blocks in input; aborting.", file=sys.stderr)
    sys.exit(1)


def chunk_blocks(seq: List[SRTBlock], size: int) -> List[List[SRTBlock]]:
    """Chunk SRT blocks into groups of at most `size` blocks each."""

    return [seq[i : i + size] for i in range(0, len(seq), size)]


BLOCKS_PER_REQUEST = 20

total_chunks = (len(blocks) + BLOCKS_PER_REQUEST - 1) // BLOCKS_PER_REQUEST

# ─────────────── GPT へ逐次リクエスト ───────────────────────────────────
for idx, block_group in enumerate(
    tqdm.tqdm(chunk_blocks(blocks, BLOCKS_PER_REQUEST), desc="Translating"), start=1
):

    # モデルに渡すテキストの前後に余計な空行を付けない。
    # 各チャンクは SRT として構造を保った部分文字列になる。
    block_text = blocks_to_text(block_group)
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
                #temperature=0.3,
                max_completion_tokens=2000
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

    # モデルの出力を SRT としてパースし、構造が壊れていないかを検証する。
    ok_srt, srt_errors = validate_srt(content)
    if not ok_srt:
        print(
            f"[WARN] chunk {idx}: translated text failed SRT validation; keeping original blocks.",
            file=sys.stderr,
        )
        for msg in srt_errors[:5]:
            print(f"    ! {msg}", file=sys.stderr)
        continue

    fixed_blocks = parse_srt_blocks(content)
    if not fixed_blocks:
        print(
            f"[WARN] chunk {idx}: no parsable SRT blocks in translation; keeping original blocks.",
            file=sys.stderr,
        )
        continue

    # index で対応付けし、タイムスタンプは元のブロックから引き継ぐ。
    orig_by_index = {b.index: b for b in block_group if b.index is not None}
    fixed_by_index = {b.index: b for b in fixed_blocks if b.index is not None}

    if set(orig_by_index.keys()) != set(fixed_by_index.keys()):
        print(
            f"[WARN] chunk {idx}: index mismatch between original and translated; keeping original blocks.",
            file=sys.stderr,
        )
        continue

    for key in sorted(orig_by_index.keys()):
        ob = orig_by_index[key]
        fb = fixed_by_index[key]

        # タイムスタンプは絶対に元のまま。
        fb.start = ob.start
        fb.end = ob.end

        # 本文だけを差し替える。
        ob.lines = fb.lines

    tqdm.tqdm.write(f"chunk {idx}: finish_reason={reason}")


# すべてのブロックのテキストを差し替えたので、まとめて SRT を書き出す。
final_srt = blocks_to_text(blocks)
dst_path.write_text(final_srt, encoding="utf-8")
print(f"\n✅ Completed! → {dst_path.resolve()}")

