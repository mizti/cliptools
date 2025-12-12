import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional

import matplotlib
matplotlib.use("Agg")  # non-GUI backend for PNG output
import matplotlib.pyplot as plt


@dataclass
class ChatMessage:
    timestamp_sec: float
    author: str
    message: str
    is_member: bool = False
    is_superchat: bool = False


@dataclass
class TimeBinScore:
    start_sec: float
    end_sec: float
    score_raw: float
    score_norm: float


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="hype-finder: find hype segments from live chat replay")
    parser.add_argument("-o", "--outdir", required=True, help="Output directory for SRT and PNG")
    parser.add_argument("--emoji-dict", help="Path to custom emoji/reaction word list (one per line)")
    parser.add_argument("--window-sec", type=int, default=5, help="Aggregation window size in seconds (default: 5)")
    parser.add_argument("--top-k", type=int, default=None, help="Max number of hype segments (default: auto by video length)")
    parser.add_argument("--live-chat-json", help="Path to yt-dlp live_chat.json subtitle file")
    parser.add_argument("--live-chat-jsonl", help="Path to preprocessed JSONL (timestamp_sec/author/message)")
    return parser.parse_args(argv)


def ensure_outdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def ensure_hype_data_dir(outdir: str) -> str:
    """Ensure the hype-data subdirectory exists and return its path."""
    hype_data_dir = os.path.join(outdir, "hype-data")
    os.makedirs(hype_data_dir, exist_ok=True)
    return hype_data_dir


def load_custom_tokens(path: Optional[str]) -> List[str]:
    if not path:
        return []
    if not os.path.exists(path):
        return []
    tokens: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                tokens.append(t)
    return tokens


def mock_fetch_live_chat(url: str) -> List[ChatMessage]:
    """Temporary stub: returns synthetic chat data for development.

    本実装では YouTube API / ライブチャットリプレイ取得処理は未実装。
    後で差し替えられるよう、インターフェイスだけ固定しておく。
    ライブチャットが取得できない場合は空リストを返す想定。
    """
    # TODO: 実際のライブチャットリプレイ取得処理を実装する
    duration_sec = 15 * 60  # 15 分相当のダミー長さ
    messages: List[ChatMessage] = []

    # シンプルなダミーデータ: 5〜10分にかけてコメントが増え、
    # 7〜8分あたりに非常に密集する「盛り上がり」を作る
    import random

    random.seed(42)
    for t in range(0, duration_sec, 2):
        base = 0
        if 5 * 60 <= t <= 10 * 60:
            base = 1
        if 7 * 60 <= t <= 8 * 60:
            base = 4
        n = base + random.randint(0, 1)
        for _ in range(n):
            msg = "www" if random.random() < 0.3 else "nice"
            messages.append(ChatMessage(timestamp_sec=float(t), author="user", message=msg))
    return messages


def extract_live_chat_to_jsonl(raw_json_path: str, jsonl_path: str) -> None:
    """Convert yt-dlp live_chat.json into JSON Lines with timestamp_sec/author/message.

    yt-dlp の live_chat 字幕 JSON は 1 ファイル内に複数のトップレベル JSON オブジェクトが
    連結された NDJSON 形式になっている。そのため通常の json.load では読み込めない。

    この関数では 1 行ずつ json.loads し、liveChatTextMessageRenderer を持つ要素だけを
    抽出して、より単純な JSONL に正規化する。
    """

    def iter_raw_objects(path: str) -> Iterable[dict]:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # 行が長すぎる / 折り返し等で壊れている場合はスキップ
                    continue

    with open(jsonl_path, "w", encoding="utf-8") as out_f:
        for obj in iter_raw_objects(raw_json_path):
            action = obj.get("replayChatItemAction")
            if not action:
                continue

            # 動画相対オフセット（ミリ秒）
            video_offset_ms = action.get("videoOffsetTimeMsec")

            for sub_action in action.get("actions", []):
                add_item = sub_action.get("addChatItemAction")
                if not add_item:
                    continue
                item = add_item.get("item", {})

                renderer = item.get("liveChatTextMessageRenderer")
                if not renderer:
                    continue

                # timestamp_sec は videoOffsetTimeMsec を優先し、無ければ timestampUsec を使う
                ts_sec: Optional[float] = None
                if video_offset_ms is not None:
                    try:
                        ts_sec = float(video_offset_ms) / 1000.0
                    except (TypeError, ValueError):
                        ts_sec = None
                if ts_sec is None:
                    ts_usec = renderer.get("timestampUsec")
                    if ts_usec is not None:
                        try:
                            ts_sec = float(ts_usec) / 1_000_000.0
                        except (TypeError, ValueError):
                            ts_sec = None
                if ts_sec is None:
                    continue

                author = ""
                author_info = renderer.get("authorName")
                if isinstance(author_info, dict):
                    author = author_info.get("simpleText", "")

                # message.runs から text/emoji を連結
                parts: List[str] = []
                message_obj = renderer.get("message") or {}
                for run in message_obj.get("runs", []) or []:
                    if "text" in run:
                        parts.append(str(run["text"]))
                    elif "emoji" in run:
                        emoji = run["emoji"]
                        shortcuts = emoji.get("shortcuts") or []
                        if shortcuts:
                            parts.append(shortcuts[0])
                        else:
                            # ショートカットが無ければ emojiId を控えめに使う
                            eid = emoji.get("emojiId")
                            if eid:
                                parts.append(str(eid))
                message = "".join(parts).strip()

                rec = {
                    "timestamp_sec": ts_sec,
                    "author": author,
                    "message": message,
                }
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def compute_video_length_sec(messages: List[ChatMessage]) -> float:
    if not messages:
        return 0.0
    return max(m.timestamp_sec for m in messages)


def iter_messages_from_jsonl(jsonl_path: str) -> Iterable[ChatMessage]:
    """Stream ChatMessage objects from a JSONL file produced by extract_live_chat_to_jsonl.

    各行は少なくとも `timestamp_sec`, `author`, `message` を持つ JSON オブジェクトであることを想定する。
    """
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = float(obj.get("timestamp_sec"))
            except (TypeError, ValueError):
                continue
            author = str(obj.get("author", ""))
            message = str(obj.get("message", ""))
            yield ChatMessage(timestamp_sec=ts, author=author, message=message)


def auto_top_k(video_length_sec: float) -> int:
    if video_length_sec <= 0:
        return 0
    L_min = video_length_sec / 60.0
    # k_default = max(2, 1 + floor(L / 30))
    k = max(2, 1 + int(L_min // 30))
    return k


def collect_bins(messages: List[ChatMessage], window_sec: int, custom_tokens: List[str]) -> List[TimeBinScore]:
    if not messages:
        return []
    duration = compute_video_length_sec(messages)
    n_bins = int(math.ceil(duration / window_sec))

    # 重みの初期値（とりあえず固定値。将来オプション化してもよい）
    alpha = 1.0  # コメント数
    beta = 1.5  # 一般リアクション文字（www など）
    gamma = 2.0  # カスタム絵文字/トークン

    bins: List[TimeBinScore] = []
    for i in range(n_bins):
        start = i * window_sec
        end = min((i + 1) * window_sec, duration)
        # このビンに含まれるメッセージ
        msgs = [m for m in messages if start <= m.timestamp_sec < end]
        C = len(msgs)
        R = 0  # リアクション文字（簡易: "w"/"www" のカウント）
        E_custom = 0

        for m in msgs:
            text = m.message
            # とりあえず単純に "w" 系をリアクションとしてカウント
            if "w" in text or "W" in text:
                R += 1
            # カスタムトークン
            for tok in custom_tokens:
                if tok in text:
                    E_custom += 1

        score_raw = alpha * C + beta * R + gamma * E_custom
        bins.append(TimeBinScore(start_sec=float(start), end_sec=float(end), score_raw=score_raw, score_norm=0.0))

    # 正規化（z-score）
    values = [b.score_raw for b in bins]
    mean = sum(values) / len(values) if values else 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
    std = math.sqrt(var) if var > 0 else 1.0

    for b in bins:
        b.score_norm = (b.score_raw - mean) / std

    return bins


def smooth_scores(bins: List[TimeBinScore], window_size: int = 3) -> List[float]:
    """移動平均でスコアを平滑化する（window_size はビン数）。"""
    if not bins:
        return []
    scores = [b.score_norm for b in bins]
    n = len(scores)
    smoothed: List[float] = []
    half = window_size // 2
    for i in range(n):
        left = max(0, i - half)
        right = min(n, i + half + 1)
        window = scores[left:right]
        smoothed.append(sum(window) / len(window))
    return smoothed


def detect_hype_segments(bins: List[TimeBinScore], smoothed: List[float], threshold: float, top_k: int) -> List[TimeBinScore]:
    """平滑化スコア列から threshold を超える連続区間を検出し、
    代表スコアの高い順に top_k 個まで返す。"""
    assert len(bins) == len(smoothed)
    segments: List[TimeBinScore] = []

    start_idx: Optional[int] = None
    for i, s in enumerate(smoothed):
        if s >= threshold:
            if start_idx is None:
                start_idx = i
        else:
            if start_idx is not None:
                end_idx = i - 1
                seg = merge_bins(bins, smoothed, start_idx, end_idx)
                segments.append(seg)
                start_idx = None
    if start_idx is not None:
        seg = merge_bins(bins, smoothed, start_idx, len(bins) - 1)
        segments.append(seg)

    # 代表スコアでソートし、top_k 個に絞る
    segments.sort(key=lambda b: b.score_norm, reverse=True)
    return segments[:top_k]


def merge_bins(bins: List[TimeBinScore], smoothed: List[float], i_start: int, i_end: int) -> TimeBinScore:
    start_sec = bins[i_start].start_sec
    end_sec = bins[i_end].end_sec
    # 区間内の smoothed スコアの平均を代表値とする
    seg_scores = smoothed[i_start : i_end + 1]
    score_norm = sum(seg_scores) / len(seg_scores)
    # raw スコアは単純和でもよいが、とりあえず平均値にしておく
    raw_scores = [bins[i].score_raw for i in range(i_start, i_end + 1)]
    score_raw = sum(raw_scores) / len(raw_scores)
    return TimeBinScore(start_sec=start_sec, end_sec=end_sec, score_raw=score_raw, score_norm=score_norm)


def format_srt_timestamp(sec: float) -> str:
    # 秒数から "HH:MM:SS,mmm" 形式に変換
    millis = int(round(sec * 1000))
    h, rem = divmod(millis, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: str, segments: List[TimeBinScore]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(segments, start=1):
            start_ts = format_srt_timestamp(seg.start_sec)
            end_ts = format_srt_timestamp(seg.end_sec)
            f.write(f"{idx}\n")
            f.write(f"{start_ts} --> {end_ts}\n")
            f.write(f"HYPE SCORE: {seg.score_norm:.2f}\n\n")


def plot_heatmap(path: str, bins: List[TimeBinScore], smoothed: List[float]) -> None:
    if not bins:
        return
    times = [b.start_sec / 60.0 for b in bins]  # minutes
    scores = smoothed

    # アスペクトを細長くして、帯全体をかなり太く見せる
    plt.figure(figsize=(10, 4.0))
    # 1D heatmap: scatter with color by score
    # マーカーを極太にして、実質 1 本のカラーバーのように見せる
    plt.scatter(times, [0] * len(times), c=scores, cmap="viridis", marker="s", s=2000, linewidths=0)
    plt.ylim(-2, 2)
    plt.yticks([])

    # X 軸は分単位で読みやすく
    # 配信時間寄りのラベル (HH:MM) を付ける
    plt.xlabel("Stream time")
    if times:
        total_min = max(times)
        # おおよそ 10〜12 個程度の目盛りになるようにステップを決める
        if total_min <= 10:
            step_min = 1
        elif total_min <= 30:
            step_min = 5
        elif total_min <= 90:
            step_min = 10
        else:
            step_min = 15

        ticks_min = list(range(0, int(total_min) + step_min, step_min))
        tick_positions = [t for t in ticks_min]

        def format_hhmm(m: int) -> str:
            h = m // 60
            mm = m % 60
            return f"{h}:{mm:02d}"

        tick_labels = [format_hhmm(m) for m in ticks_min]
        plt.xticks(tick_positions, tick_labels)

    plt.colorbar(label="Hype score (smoothed)")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    ensure_outdir(args.outdir)
    hype_data_dir = ensure_hype_data_dir(args.outdir)

    custom_tokens = load_custom_tokens(args.emoji_dict)

    # 入力ソースの決定: JSONL が指定されていればそれを優先し、
    # なければ live_chat.json から JSONL を生成する。
    jsonl_path: Optional[str] = args.live_chat_jsonl

    if not jsonl_path and args.live_chat_json:
        # live_chat.json -> JSONL 変換
        jsonl_path = os.path.join(hype_data_dir, "live_chat.jsonl")
        extract_live_chat_to_jsonl(args.live_chat_json, jsonl_path)

    if not jsonl_path or not os.path.exists(jsonl_path):
        # 入力が無い場合は何もせず終了
        return

    # JSONL から ChatMessage をストリーミングしつつメモリに積む
    # （必要になれば将来的に完全ストリーム化も検討）
    messages = list(iter_messages_from_jsonl(jsonl_path))
    if not messages:
        return

    video_len_sec = compute_video_length_sec(messages)
    bins = collect_bins(messages, args.window_sec, custom_tokens)
    smoothed = smooth_scores(bins, window_size=3)

    # top-k の決定
    if args.top_k is not None:
        top_k = args.top_k
    else:
        top_k = auto_top_k(video_len_sec)
    if top_k <= 0:
        return

    # 閾値（z-score ベース）: とりあえず 1.0 に固定
    threshold = 1.0
    segments = detect_hype_segments(bins, smoothed, threshold=threshold, top_k=top_k)
    if not segments:
        return

    # SRT 出力（成果物）
    srt_path = os.path.join(args.outdir, "hype_segments.srt")
    write_srt(srt_path, segments)

    # ヒートマップ出力（成果物）
    png_path = os.path.join(args.outdir, "hype_heatmap.png")
    plot_heatmap(png_path, bins, smoothed)

    # 必要に応じて hype-data/ 配下に中間生成物を保存する場合は、
    # hype_data_dir を用いて書き出す想定（現時点では未使用）。
    # 例: bin スコアの JSON ダンプなど。
    _ = hype_data_dir


if __name__ == "__main__":
    main()
