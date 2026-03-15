#!/usr/bin/env python
import argparse
import difflib
import os
import sys
from pathlib import Path
from openai import AzureOpenAI
from openai import APIConnectionError, APITimeoutError, APIError

from utils.srt_parser import SRTBlock, blocks_to_text, parse_srt_blocks, validate_srt

RED = "\033[31m"
HIGHLIGHT = "\033[38;5;207m"  # bright magenta/pink-ish (after)
B_BEFORE = "\033[38;5;39m"    # bright blue-ish (before)
BOLD = "\033[1m"
RESET = "\033[0m"


def _env_truthy(key: str) -> bool:
    v = os.getenv(key, "").strip().lower()
    return v not in {"", "0", "false", "no", "off"}


def _truncate_for_log(text: str, limit: int) -> str:
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    head = text[:limit]
    return head + f"\n... (truncated, total_chars={len(text)})"


def _debug_print(debug: bool, message: str, *, log_path: str | None = None) -> None:
    if not debug:
        return
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(message)
                if not message.endswith("\n"):
                    f.write("\n")
        except OSError as e:
            print(f"[fix_proper_nouns_gpt][debug] failed to write log to {log_path}: {e}", file=sys.stderr)
            print(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr)

def load_env(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None:
        raise SystemExit(f"Environment variable {key} is not set")
    return value


def read_dictionary(dict_path: Path) -> str:
    if not dict_path.exists():
        return ""
    return dict_path.read_text(encoding="utf-8")


def build_client() -> AzureOpenAI:
    endpoint = load_env("ENDPOINT_URL")
    api_key = load_env("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def call_model(
    client: AzureOpenAI,
    deployment: str,
    system_prompt: str,
    dictionary_text: str,
    srt_text: str,
    *,
    timeout_seconds: float = 60.0,
    max_retries: int = 3,
    debug: bool = False,
    debug_label: str = "",
    debug_log_path: str | None = None,
    debug_max_chars: int = 8000,
    debug_dump_response_json: bool = False,
    debug_response_json_max_chars: int = 20000,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
) -> str:
    """Call the model for a given SRT chunk with timeout & retry.

    - 1分 (timeout_seconds) 待っても応答がなければタイムアウト扱い
    - 最大 max_retries 回まで再試行
    - すべて失敗した場合は例外を投げる（呼び出し側でオリジナルにフォールバック）
    """

    user_content_parts = []
    if dictionary_text.strip():
        user_content_parts.append(dictionary_text.rstrip("\n"))
    user_content_parts.append("-----SRT-----")
    user_content_parts.append(srt_text)
    user_content = "\n".join(user_content_parts)

    if debug:
        label = f" {debug_label}" if debug_label else ""
        _debug_print(
            True,
            f"\n========== [fix_proper_nouns_gpt][debug]{label} SYSTEM PROMPT BEGIN =========="
            f"\n{_truncate_for_log(system_prompt, debug_max_chars)}"
            f"\n========== [fix_proper_nouns_gpt][debug]{label} SYSTEM PROMPT END =========="
            f"\n",
            log_path=debug_log_path,
        )
        _debug_print(
            True,
            f"\n========== [fix_proper_nouns_gpt][debug]{label} USER TEXT BEGIN =========="
            f"\n{_truncate_for_log(user_content, debug_max_chars)}"
            f"\n========== [fix_proper_nouns_gpt][debug]{label} USER TEXT END =========="
            f"\n",
            log_path=debug_log_path,
        )

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=deployment,
                max_completion_tokens=4000,
                timeout=timeout_seconds,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            choice = resp.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            message = choice.message
            content = (message.content or "") if message is not None else ""
            refusal = getattr(message, "refusal", None) if message is not None else None
            tool_calls = getattr(message, "tool_calls", None) if message is not None else None

            if debug:
                label = f" {debug_label}" if debug_label else ""
                _debug_print(
                    True,
                    "[fix_proper_nouns_gpt][debug]"
                    f"{label} finish_reason={finish_reason} content_len={len(content)} "
                    f"refusal={'yes' if refusal else 'no'} tool_calls={len(tool_calls) if tool_calls else 0}",
                    log_path=debug_log_path,
                )
                if refusal:
                    _debug_print(
                        True,
                        f"\n========== [fix_proper_nouns_gpt][debug]{label} REFUSAL BEGIN =========="
                        f"\n{_truncate_for_log(str(refusal), debug_max_chars)}"
                        f"\n========== [fix_proper_nouns_gpt][debug]{label} REFUSAL END =========="
                        f"\n",
                        log_path=debug_log_path,
                    )

                prompt_filter = getattr(resp, "prompt_filter_results", None)
                content_filter = getattr(resp, "content_filter_results", None)
                if prompt_filter is not None or content_filter is not None:
                    _debug_print(
                        True,
                        "[fix_proper_nouns_gpt][debug]"
                        f"{label} prompt_filter_results_present={prompt_filter is not None} "
                        f"content_filter_results_present={content_filter is not None}",
                        log_path=debug_log_path,
                    )

                if debug_dump_response_json:
                    try:
                        raw_json = resp.model_dump_json(indent=2, ensure_ascii=False)
                    except Exception:  # noqa: BLE001
                        raw_json = str(resp)
                    _debug_print(
                        True,
                        f"\n========== [fix_proper_nouns_gpt][debug]{label} RESPONSE JSON BEGIN =========="
                        f"\n{_truncate_for_log(raw_json, debug_response_json_max_chars)}"
                        f"\n========== [fix_proper_nouns_gpt][debug]{label} RESPONSE JSON END =========="
                        f"\n",
                        log_path=debug_log_path,
                    )

            if debug:
                label = f" {debug_label}" if debug_label else ""
                _debug_print(
                    True,
                    f"\n========== [fix_proper_nouns_gpt][debug]{label} RAW RESPONSE BEGIN =========="
                    f"\n{_truncate_for_log(content, debug_max_chars)}"
                    f"\n========== [fix_proper_nouns_gpt][debug]{label} RAW RESPONSE END =========="
                    f"\n",
                    log_path=debug_log_path,
                )
            return content
        except (APITimeoutError, APIConnectionError, APIError) as e:  # noqa: PERF203
            last_err = e
            print(
                f"[fix_proper_nouns_gpt] API error on attempt {attempt}/{max_retries}: {e}",
                file=sys.stderr,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(
                f"[fix_proper_nouns_gpt] Unexpected error on attempt {attempt}/{max_retries}: {e}",
                file=sys.stderr,
            )

    # ここまで来たら全リトライ失敗
    if last_err is not None:
        raise last_err
    raise RuntimeError("call_model failed without specific error")


def chunk_blocks(blocks: list[SRTBlock], size: int) -> list[list[SRTBlock]]:
    """Return a list of SRTBlock chunks with at most `size` blocks each."""

    return [blocks[i : i + size] for i in range(0, len(blocks), size)]


def is_timestamp_line(line: str) -> bool:
    """Return True if the line looks like an SRT timestamp line.

    This is a defensive check to make sure we never let the model
    modify timing information. We treat a line as a timestamp if:

    - It contains the arrow `-->`, and
    - It consists only of digits, spaces, ':', ',', '-', '>' characters.
    """

    if "-->" not in line:
        return False
    allowed = set("0123456789 :,->")
    return all(ch in allowed for ch in line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix proper nouns in an English SRT using Azure OpenAI and a custom dictionary.")
    parser.add_argument("input_srt", help="Path to the input English SRT file")
    parser.add_argument("--output", "-o", help="Output SRT path (default: <input>.fixed.srt)")
    parser.add_argument(
        "--dict",
        dest="dict_path",
        default="custom_dictionary/mydictionary.txt",
        help="Path to the tab-separated custom dictionary file",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_path",
        default="settings/checkunique_prompt.txt",
        help="Path to the system prompt file for proper-noun fixing",
    )
    parser.add_argument(
        "--deployment",
        dest="deployment",
        default=os.getenv("DEPLOYMENT_NAME", "gpt-5.1-chat"),
        help="Azure OpenAI deployment name to use",
    )

    args = parser.parse_args()

    input_path = Path(args.input_srt)
    if not input_path.exists():
        raise SystemExit(f"Input SRT not found: {input_path}")

    output_path = Path(args.output) if args.output else input_path.with_suffix(".fixed.srt")

    dict_path = Path(args.dict_path)
    prompt_path = Path(args.prompt_path)

    try:
        dictionary_text = read_dictionary(dict_path)
    except OSError as e:
        print(f"Warning: failed to read dictionary {dict_path}: {e}", file=sys.stderr)
        dictionary_text = ""

    if not prompt_path.exists():
        raise SystemExit(f"Prompt file not found: {prompt_path}")

    system_prompt = prompt_path.read_text(encoding="utf-8")
    srt_text = input_path.read_text(encoding="utf-8")

    debug = _env_truthy("FIX_UNIQUE_NOUNS_DEBUG")
    debug_log_path = os.getenv("FIX_UNIQUE_NOUNS_DEBUG_PATH")
    try:
        debug_max_chars = int(os.getenv("FIX_UNIQUE_NOUNS_DEBUG_MAX_CHARS", "8000"))
    except ValueError:
        debug_max_chars = 8000

    debug_dump_response_json = _env_truthy("FIX_UNIQUE_NOUNS_DEBUG_DUMP_RESPONSE_JSON")
    try:
        debug_response_json_max_chars = int(os.getenv("FIX_UNIQUE_NOUNS_DEBUG_RESPONSE_JSON_MAX_CHARS", "20000"))
    except ValueError:
        debug_response_json_max_chars = 20000

    if debug:
        where = debug_log_path or "stderr"
        print(f"[fix_proper_nouns_gpt][debug] enabled (log={where}, max_chars={debug_max_chars})", file=sys.stderr)

    # GPT-5 系の一部デプロイでは、completionトークンが reasoning に全振りされて
    # content が空で返るケースがあるため、デフォルトで推論を抑制する。
    reasoning_effort = os.getenv("FIX_UNIQUE_NOUNS_REASONING_EFFORT", "low").strip() or None
    verbosity = os.getenv("FIX_UNIQUE_NOUNS_VERBOSITY", "low").strip() or None
    if debug:
        print(
            f"[fix_proper_nouns_gpt][debug] reasoning_effort={reasoning_effort} verbosity={verbosity}",
            file=sys.stderr,
        )

    # If there is no dictionary, we can choose to skip processing.
    if not dictionary_text.strip():
        print(f"[fix_proper_nouns_gpt] Dictionary {dict_path} is empty; copying input to output.")
        output_path.write_text(srt_text, encoding="utf-8")
        return

    client = build_client()

    # Parse SRT into structured blocks and process in chunks to avoid
    # overloading the model with extremely long inputs. We keep index
    # and timestamps from the original blocks and only ever modify the
    # text lines.
    all_blocks = parse_srt_blocks(srt_text)
    if not all_blocks:
        print(
            "[fix_proper_nouns_gpt] No parsable SRT blocks found; copying input to output.",
            file=sys.stderr,
        )
        output_path.write_text(srt_text, encoding="utf-8")
        return

    blocks_per_chunk = 100
    print(
        f"[fix_proper_nouns_gpt] Processing {len(all_blocks)} blocks in chunks of {blocks_per_chunk}...",
        file=sys.stderr,
    )

    for idx, block_group in enumerate(chunk_blocks(all_blocks, blocks_per_chunk), start=1):
        chunk_srt = blocks_to_text(block_group)
        print(
            f"[fix_proper_nouns_gpt] Calling model '{args.deployment}' for chunk {idx} (blocks {len(block_group)})...",
            file=sys.stderr,
        )
        try:
            fixed_chunk = call_model(
                client=client,
                deployment=args.deployment,
                system_prompt=system_prompt,
                dictionary_text=dictionary_text,
                srt_text=chunk_srt,
                debug=debug,
                debug_label=f"chunk {idx}",
                debug_log_path=debug_log_path,
                debug_max_chars=debug_max_chars,
                debug_dump_response_json=debug_dump_response_json,
                debug_response_json_max_chars=debug_response_json_max_chars,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"[fix_proper_nouns_gpt] Error in chunk {idx}: {e}; keeping original chunk.",
                file=sys.stderr,
            )
            fixed_chunk = chunk_srt

        if not fixed_chunk.strip():
            print(
                f"[fix_proper_nouns_gpt] Chunk {idx} returned empty content; keeping original chunk.",
                file=sys.stderr,
            )
            fixed_chunk = chunk_srt

        # Parse model output back into blocks for this chunk.
        fixed_blocks = parse_srt_blocks(fixed_chunk)
        if not fixed_blocks:
            print(
                f"[fix_proper_nouns_gpt] Chunk {idx} produced no parsable blocks; keeping original chunk.",
                file=sys.stderr,
            )
            continue

        ok, errors = validate_srt(fixed_chunk)
        if not ok:
            print(
                f"[fix_proper_nouns_gpt] Chunk {idx} failed SRT validation; keeping original chunk.",
                file=sys.stderr,
            )
            for msg in errors[:5]:
                print(f"    ! {msg}", file=sys.stderr)
            continue

        # Align original and fixed blocks by index; timestamps from the
        # original always win. We only adopt text changes.
        orig_by_index: dict[int, SRTBlock] = {b.index: b for b in block_group if b.index is not None}
        fixed_by_index: dict[int, SRTBlock] = {b.index: b for b in fixed_blocks if b.index is not None}

        # If the sets of indices differ wildly, this chunk is suspicious.
        if set(orig_by_index.keys()) != set(fixed_by_index.keys()):
            print(
                f"[fix_proper_nouns_gpt] Chunk {idx} index mismatch between original and fixed; keeping original chunk.",
                file=sys.stderr,
            )
            continue

        # Diff logging and text adoption per block.
        for index_key in sorted(orig_by_index.keys()):
            ob = orig_by_index[index_key]
            fb = fixed_by_index[index_key]

            # Always restore original timestamps on the fixed block.
            if fb.start != ob.start or fb.end != ob.end:
                print(
                    f"[fix_proper_nouns_gpt] Chunk {idx} block index {index_key}: restoring timestamps "
                    f"{fb.start} --> {fb.end} to {ob.start} --> {ob.end}",
                    file=sys.stderr,
                )
                fb.start = ob.start
                fb.end = ob.end

            if ob.lines == fb.lines:
                continue

            # Block-level diff logging for text changes only.
            orig_text = "\n".join(ob.lines)
            fixed_text = "\n".join(fb.lines)
            orig_lines = orig_text.splitlines(keepends=False)
            fixed_lines = fixed_text.splitlines(keepends=False)
            matcher = difflib.SequenceMatcher(a=orig_lines, b=fixed_lines)
            print(
                f"[fix_proper_nouns_gpt] Diff for chunk {idx}, block index {index_key} "
                f"[{ob.start} --> {ob.end}]",
                file=sys.stderr,
            )
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                if tag in {"replace", "delete"}:
                    for line in orig_lines[i1:i2]:
                        before_words = line.split()
                        after_line = "".join(fixed_lines[j1:j2]) if tag == "replace" else ""
                        after_words = after_line.split()
                        word_matcher = difflib.SequenceMatcher(None, before_words, after_words)
                        before_result: list[str] = []
                        for wtag, wi1, wi2, wj1, wj2 in word_matcher.get_opcodes():
                            if wtag == "equal":
                                before_result.extend(before_words[wi1:wi2])
                            elif wtag in ("replace", "delete"):
                                for w in before_words[wi1:wi2]:
                                    before_result.append(f"{BOLD}{B_BEFORE}{w}{RESET}")
                            elif wtag == "insert":
                                continue
                        highlighted_before = " ".join(before_result) if before_result else line
                        print(f"  - {highlighted_before}", file=sys.stderr)

                if tag in {"replace", "insert"}:
                    for line in fixed_lines[j1:j2]:
                        before_line = "".join(orig_lines[i1:i2]) if tag == "replace" else ""
                        before_words = before_line.split()
                        after_words = line.split()
                        word_matcher = difflib.SequenceMatcher(None, before_words, after_words)
                        result_words: list[str] = []
                        for wtag, wi1, wi2, wj1, wj2 in word_matcher.get_opcodes():
                            if wtag == "equal":
                                result_words.extend(after_words[wj1:wj2])
                            elif wtag in ("replace", "insert"):
                                for w in after_words[wj1:wj2]:
                                    result_words.append(f"{BOLD}{HIGHLIGHT}{w}{RESET}")
                            elif wtag == "delete":
                                continue
                        highlighted = " ".join(result_words) if result_words else line
                        print(f"  + {highlighted}", file=sys.stderr)

            # Finally, adopt the fixed text lines for this block.
            ob.lines = fb.lines

    fixed_srt = blocks_to_text(all_blocks)
    output_path.write_text(fixed_srt, encoding="utf-8")
    print(f"[fix_proper_nouns_gpt] Wrote fixed SRT to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
