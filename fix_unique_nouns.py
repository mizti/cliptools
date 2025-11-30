#!/usr/bin/env python
import argparse
import difflib
import os
import sys
from pathlib import Path
from openai import AzureOpenAI

RED = "\033[31m"
HIGHLIGHT = "\033[38;5;207m"  # bright magenta/pink-ish (after)
B_BEFORE = "\033[38;5;39m"    # bright blue-ish (before)
BOLD = "\033[1m"
RESET = "\033[0m"

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


def call_model(client: AzureOpenAI, deployment: str, system_prompt: str, dictionary_text: str, srt_text: str) -> str:
    """Call the model once for a given SRT chunk and return its content.

    The SRT text should be a sequence of complete SRT blocks. The function
    prepares the user content as:

        [dictionary]
        -----SRT-----
        [srt_text]
    """

    user_content_parts = []
    if dictionary_text.strip():
        user_content_parts.append(dictionary_text.rstrip("\n"))
    user_content_parts.append("-----SRT-----")
    user_content_parts.append(srt_text)
    user_content = "\n".join(user_content_parts)

    resp = client.chat.completions.create(
        model=deployment,
        max_completion_tokens=4000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )

    choice = resp.choices[0]
    content = choice.message.content or ""
    return content


def split_srt_into_blocks(srt_text: str) -> list[str]:
    """Split an SRT file into a list of blocks, preserving order.

    Each block is separated by one or more blank lines. The trailing
    newline of each block is preserved so simple concatenation reproduces
    the original structure.
    """

    lines = srt_text.splitlines(keepends=True)
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if line.strip() == "":
            # Blank line -> block boundary
            current.append(line)
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)

    if current:
        blocks.append(current)

    # Join inner lists to strings
    return ["".join(block) for block in blocks]


def chunks(seq: list[str], size: int) -> list[list[str]]:
    """Return a list of chunks (sublists) from seq with at most `size` items each."""

    return [seq[i : i + size] for i in range(0, len(seq), size)]


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

    # If there is no dictionary, we can choose to skip processing.
    if not dictionary_text.strip():
        print(f"[fix_proper_nouns_gpt] Dictionary {dict_path} is empty; copying input to output.")
        output_path.write_text(srt_text, encoding="utf-8")
        return

    client = build_client()

    # Split SRT into numbered blocks and process in chunks to avoid
    # overloading the model with extremely long inputs.
    all_blocks = split_srt_into_blocks(srt_text)
    blocks_per_chunk = 100
    print(
        f"[fix_proper_nouns_gpt] Processing {len(all_blocks)} blocks in chunks of {blocks_per_chunk}...",
        file=sys.stderr,
    )

    fixed_blocks: list[str] = []
    for idx, block_group in enumerate(chunks(all_blocks, blocks_per_chunk), start=1):
        chunk_srt = "".join(block_group)
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

        # Extra safety: for any timestamp-looking line, force it back to
        # the original even if the model changed it.
        try:
            fixed_lines_all = fixed_chunk.splitlines(keepends=True)
            orig_lines_all = chunk_srt.splitlines(keepends=True)
            if len(fixed_lines_all) == len(orig_lines_all):
                for li, (o_line, f_line) in enumerate(zip(orig_lines_all, fixed_lines_all)):
                    if is_timestamp_line(o_line.rstrip("\n")):
                        fixed_lines_all[li] = o_line
                fixed_chunk = "".join(fixed_lines_all)
        except Exception as e:  # noqa: BLE001
            print(
                f"[fix_proper_nouns_gpt] Warning: failed to enforce timestamp safety in chunk {idx}: {e}",
                file=sys.stderr,
            )

        # Diff logging: show only changed lines for this chunk, with
        # word-level highlights for differences. This runs *after*
        # timestamp safety correction, so timestamps in the diff
        # represent what actually ends up in the file.
        if fixed_chunk != chunk_srt:
            orig_lines = chunk_srt.splitlines(keepends=False)
            fixed_lines = fixed_chunk.splitlines(keepends=False)
            matcher = difflib.SequenceMatcher(a=orig_lines, b=fixed_lines)
            print(f"[fix_proper_nouns_gpt] Diff for chunk {idx}:", file=sys.stderr)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                # タイムスタンプ行は、出力側ですでに元に戻しているので
                # diff ログ上でもノイズにならないようスキップする。
                if all(is_timestamp_line(l) for l in orig_lines[i1:i2]) and all(
                    is_timestamp_line(l) for l in fixed_lines[j1:j2]
                ):
                    continue
                # 行レベルではどの行が変わったかだけを示し、
                # 実際の強調は「After 行」を単語レベルで行う。
                if tag in {"replace", "delete"}:
                    # Before 側も単語レベルでハイライトしてから出す
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
                                # insert は After 側だけに出てくる単語なので、Before では何も出さない
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
                                # 削除された単語はログ上では何も出さない
                                continue
                        highlighted = " ".join(result_words) if result_words else line
                        print(f"  + {highlighted}", file=sys.stderr)

        fixed_blocks.append(fixed_chunk)

    fixed_srt = "".join(fixed_blocks)
    output_path.write_text(fixed_srt, encoding="utf-8")
    print(f"[fix_proper_nouns_gpt] Wrote fixed SRT to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
