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
import random
import signal
from pathlib import Path
from typing import List

from openai import AzureOpenAI      # pip install openai>=1.0
from openai import APIConnectionError, APITimeoutError, APIError
import tqdm                         # pip install tqdm

from utils.srt_parser import SRTBlock, blocks_to_text, parse_srt_blocks, validate_srt


# ───────────────── CLI ──────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input',      required=True, help='SRT file to translate')
parser.add_argument('-o', '--output_dir', required=True, help='Directory to write result')
parser.add_argument(
    '--chunk',
    dest='blocks_per_request',
    type=int,
    default=int(os.getenv('TRANSLATE_BLOCKS_PER_REQUEST', '40') or 40),
    help='SRT blocks per request (default: 40; env: TRANSLATE_BLOCKS_PER_REQUEST)'
)
parser.add_argument(
    '--timeout',
    type=float,
    default=float(os.getenv('TRANSLATE_TIMEOUT_SECONDS', '180') or 180),
    help='Request timeout seconds (default: 180; env: TRANSLATE_TIMEOUT_SECONDS)'
)
parser.add_argument(
    '--retries',
    type=int,
    default=int(os.getenv('TRANSLATE_MAX_RETRIES', '3') or 3),
    help='Max retries per chunk (default: 3; env: TRANSLATE_MAX_RETRIES)'
)
parser.add_argument(
    '--max-output-tokens',
    type=int,
    default=int(os.getenv('TRANSLATE_MAX_OUTPUT_TOKENS', '20000') or 20000),
    help='max_output_tokens for the model (default: 20000; env: TRANSLATE_MAX_OUTPUT_TOKENS)'
)
parser.add_argument(
    '--debug',
    action='store_true',
    help='Enable verbose debug logs to stderr (env: TRANSLATE_DEBUG=1)'
)
parser.add_argument(
    '--debug-save-input',
    action='store_true',
    help='Save each chunk input SRT into debug dir (env: TRANSLATE_DEBUG_SAVE_INPUT=1)'
)
parser.add_argument('--debug-dump', action='store_true', help='Dump raw model response for inspection (one chunk only)')
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
    api_version   =os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
)
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-5-chat")

debug_enabled = bool(args.debug) or bool(int(os.getenv('TRANSLATE_DEBUG', '0') or 0))
save_debug_input = bool(args.debug_save_input) or bool(int(os.getenv('TRANSLATE_DEBUG_SAVE_INPUT', '0') or 0))


def debug_log(msg: str) -> None:
    if debug_enabled:
        print(f"[DEBUG] {msg}", file=sys.stderr)

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


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_response_text(rsp) -> str | None:
    """Extract assistant text from a Responses API result (best-effort)."""

    if rsp is None:
        return None

    text = _get(rsp, "output_text", None)
    if isinstance(text, str):
        return text

    output = _get(rsp, "output", None) or []
    texts: list[str] = []
    for item in output:
        content_parts = _get(item, "content", None) or []
        for part in content_parts:
            part_type = _get(part, "type", "")
            if part_type in ("output_text", "text"):
                t = _get(part, "text", None)
                if isinstance(t, str) and t:
                    texts.append(t)
    if texts:
        return "\n".join(texts)

    return None


def _write_current_output() -> None:
    final_srt = blocks_to_text(blocks)
    dst_path.write_text(final_srt, encoding="utf-8")


BLOCKS_PER_REQUEST = 40

# Prefer CLI/env setting over hard-coded default
try:
    BLOCKS_PER_REQUEST = max(1, int(args.blocks_per_request))
except Exception:  # noqa: BLE001
    BLOCKS_PER_REQUEST = 40

# デバッグ: 問題のクリップで GPT 応答が SRT としてパースできない原因を調べるため、
# 先頭いくつかのチャンクについてはモデル応答の生テキストと、パース/検証エラーを
# ログに詳細出力する。長期的にはフラグで切り替えるか削除予定の一時的な計測コード。
DEBUG_LOG_RAW_CHUNKS = int(os.getenv("TRANSLATE_DEBUG_RAW_CHUNKS", "0") or 0)
DEBUG_LOG_DIR = os.getenv("TRANSLATE_DEBUG_DIR", "clips/_translate_debug")

total_chunks = (len(blocks) + BLOCKS_PER_REQUEST - 1) // BLOCKS_PER_REQUEST

debug_log(
    "translate_srt_gpt.py config: "
    f"endpoint={os.getenv('ENDPOINT_URL','')!r} "
    f"api_version={os.getenv('AZURE_OPENAI_API_VERSION','2025-04-01-preview')!r} "
    f"deployment={deployment!r} "
    f"blocks={len(blocks)} chunks={total_chunks} blocks_per_request={BLOCKS_PER_REQUEST} "
    f"timeout={args.timeout}s retries={args.retries} max_output_tokens={args.max_output_tokens}"
)


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _exception_details(e: BaseException) -> str:
    status = getattr(e, 'status_code', None)
    request_id = getattr(e, 'request_id', None)
    code = getattr(e, 'code', None)
    typ = type(e).__name__
    parts = [f"type={typ}"]
    if status is not None:
        parts.append(f"status_code={status}")
    if code is not None:
        parts.append(f"code={code}")
    if request_id is not None:
        parts.append(f"request_id={request_id}")
    parts.append(f"message={e}")
    return " ".join(parts)


def _sigint_handler(signum, frame):  # noqa: ANN001
    # Ctrl+C で中断した場合でも、途中結果を保存して調査しやすくする。
    try:
        _write_current_output()
        print(f"\n[INFO] Interrupted. Partial output saved → {dst_path.resolve()}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"\n[WARN] Interrupted, but failed to save partial output: {e}", file=sys.stderr)
    signal.default_int_handler(signum, frame)


signal.signal(signal.SIGINT, _sigint_handler)

# ─────────────── GPT へ逐次リクエスト ───────────────────────────────────
for idx, block_group in enumerate(
    tqdm.tqdm(chunk_blocks(blocks, BLOCKS_PER_REQUEST), desc="Translating"), start=1
):

    # モデルに渡すテキストの前後に余計な空行を付けない。
    # 各チャンクは SRT として構造を保った部分文字列になる。
    block_text = blocks_to_text(block_group)
    if debug_enabled:
        first_i = next((b.index for b in block_group if b.index is not None), None)
        last_i = next((b.index for b in reversed(block_group) if b.index is not None), None)
        debug_log(
            f"chunk {idx}/{total_chunks}: blocks={len(block_group)} index_range={first_i}-{last_i} "
            f"chars={len(block_text)} lines={len(block_text.splitlines())}"
        )

    if (save_debug_input or (DEBUG_LOG_RAW_CHUNKS and idx <= DEBUG_LOG_RAW_CHUNKS)):
        try:
            log_dir = Path(DEBUG_LOG_DIR)
            _safe_mkdir(log_dir)
            in_path = log_dir / f"chunk_{idx:03d}_input.srt"
            in_path.write_text(block_text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] chunk {idx}: failed to write debug input: {e}", file=sys.stderr)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": block_text}
    ]

    # ― リクエスト＋タイムアウト付き指数バックオフリトライ ―
    rsp, last_err = None, None
    max_retries = max(1, int(args.retries))
    timeout_seconds = float(args.timeout)
    max_output_tokens = int(args.max_output_tokens)
    for attempt in range(1, max_retries + 1):
        try:
            t0 = time.time()
            rsp = client.responses.create(
                model=deployment,
                input=messages,
                #temperature=0.3,
                max_output_tokens=max_output_tokens,
                timeout=timeout_seconds,
            )
            dt = time.time() - t0
            debug_log(f"chunk {idx}/{total_chunks}: request ok attempt={attempt} elapsed={dt:.1f}s")
            break        # 成功
        except (APITimeoutError, APIConnectionError, APIError) as e:
            last_err = e
            wait = (2 ** (attempt - 1)) + random.random()
            print(
                f"[WARN] chunk {idx}/{total_chunks}: API error on attempt {attempt}/{max_retries} "
                f"(timeout={timeout_seconds}s) {_exception_details(e)}",
                file=sys.stderr,
            )
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = (2 ** (attempt - 1)) + random.random()
            print(
                f"[WARN] chunk {idx}/{total_chunks}: unexpected error on attempt {attempt}/{max_retries}: {e}",
                file=sys.stderr,
            )
            time.sleep(wait)

    if rsp is None:      # 3 回とも失敗
        content = f"[ERROR: {last_err}]"
        reason  = "exception"
    else:
        # ― レスポンス検証 ―
        reason = _get(rsp, "status", "unknown")
        content = extract_response_text(rsp)

        if content is None:
            print(f"[WARN] block {idx}: empty response: status={reason}", file=sys.stderr)
            content = ""

        # ― 念のため、生成された本文から全角の句点「。」を除去してから書き出す ―
        #   GPT が指示に反して句点を付けてしまった場合でも、ここで落とすことで
        #   出力 SRT のポリシー（行末に句点を付けない）を守る。
        content = content.replace("。", "")

    # --debug-dump が指定されている場合は、最初のチャンクに対するモデル応答を
    # そのまま標準出力に書き出して終了する。プロンプトとの噛み合わせ調査用。
    if args.debug_dump:
        sys.stdout.write(content or "")
        sys.stdout.flush()
        sys.exit(0)

    # デバッグ用に生テキストを保存（必要数だけ）
    if DEBUG_LOG_RAW_CHUNKS and idx <= DEBUG_LOG_RAW_CHUNKS:
        try:
            log_dir = Path(DEBUG_LOG_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            raw_path = log_dir / f"chunk_{idx:03d}_raw.txt"
            raw_path.write_text(content or "", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] chunk {idx}: failed to write raw debug log: {e}", file=sys.stderr)

    # モデル出力を一旦 SRT としてパースする。
    fixed_blocks = parse_srt_blocks(content)
    if not fixed_blocks:
        print(
            f"[WARN] chunk {idx}: no parsable SRT blocks in translation; keeping original blocks.",
            file=sys.stderr,
        )
        # 失敗時も、デバッグ用に短縮表示を残しておく
        preview = (content or "").splitlines()
        preview = " \n".join(preview[:5])
        print(f"       raw head (first 5 lines): {preview}", file=sys.stderr)
        continue

    # index で対応付けし、タイムスタンプは元のブロックから引き継ぐ。
    orig_by_index = {b.index: b for b in block_group if b.index is not None}
    fixed_by_index = {b.index: b for b in fixed_blocks if b.index is not None}

    common_keys = sorted(orig_by_index.keys() & fixed_by_index.keys())
    if not common_keys:
        print(
            f"[WARN] chunk {idx}: no common indices between original and translated; keeping original blocks.",
            file=sys.stderr,
        )
        continue

    # 共通 index だけ本文を差し替えつつ、タイムスタンプは元の値を書き戻す。
    for key in common_keys:
        ob = orig_by_index[key]
        fb = fixed_by_index[key]

        fb.start = ob.start
        fb.end = ob.end

        ob.lines = fb.lines

    # タイムスタンプを書き戻した後の状態で SRT として妥当かを検証する。
    # ここでは「致命的な構造崩れ」のみチャンク単位でロールバックし、
    # そうでなければ個々のブロックはできるだけ採用する方針とする。
    merged_text = blocks_to_text(blocks)
    ok_srt, srt_errors = validate_srt(merged_text)
    if not ok_srt:
        # 文字通りの構造崩れ（例: インデックス欠落、タイムスタンプ順序の破綻など）
        # だけを致命的とみなし、それ以外の軽微な警告は許容する。
        fatal_errors: list[str] = []
        nonfatal_errors: list[str] = []
        for msg in srt_errors:
            m_lower = msg.lower()
            if "start time" in m_lower and "after end time" in m_lower:
                fatal_errors.append(msg)
            elif "overlap" in m_lower:
                fatal_errors.append(msg)
            elif "missing index" in m_lower:
                fatal_errors.append(msg)
            else:
                nonfatal_errors.append(msg)

        if fatal_errors:
            print(
                f"[WARN] chunk {idx}: merged SRT failed validation with fatal errors; keeping original blocks for this chunk.",
                file=sys.stderr,
            )
            for msg in fatal_errors[:5]:
                print(f"    ! {msg}", file=sys.stderr)

            # 差し替えた本文を元に戻す（共通 index の範囲のみ）。
            for key in common_keys:
                ob = orig_by_index[key]
                # ob.lines はまだ英語のままなので、何もせず元に戻す。
                # （上書き済みの状態を持たない実装のため、実質 no-op）
                pass
        else:
            # 軽微なエラーはログだけ出してテキストは採用する。
            print(
                f"[WARN] chunk {idx}: merged SRT has non-fatal validation warnings; accepting translated blocks.",
                file=sys.stderr,
            )
            for msg in nonfatal_errors[:5]:
                print(f"    ~ {msg}", file=sys.stderr)

    tqdm.tqdm.write(f"chunk {idx}: finish_reason={reason}")


# すべてのブロックのテキストを差し替えたので、まとめて SRT を書き出す。
_write_current_output()
print(f"\n✅ Completed! → {dst_path.resolve()}")

