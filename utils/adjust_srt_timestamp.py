import sys
import re
from datetime import timedelta, datetime

def parse_srt_timestamp(ts):
    return datetime.strptime(ts, "%H:%M:%S,%f")

def format_srt_timestamp(dt):
    return dt.strftime("%H:%M:%S,%f")[:-3]

def parse_time_arg(arg):
    """
    Parse a time argument which can be:
    - seconds as float (e.g., "90.5")
    - mm:ss or m:ss (e.g., "5:30")
    - hh:mm:ss (e.g., "01:05:30")
    Returns total seconds as float.
    """
    if ":" in arg:
        parts = arg.split(":")
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"Invalid time format: {arg}")
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        elif len(parts) == 3:
            hours, minutes, seconds = parts
            return hours * 3600 + minutes * 60 + seconds
        else:
            raise ValueError(f"Invalid time format: {arg}")
    else:
        return float(arg)

def adjust_srt(input_path, output_path, clip_start_sec, clip_end_sec):
    clip_start = timedelta(seconds=clip_start_sec)
    clip_end = timedelta(seconds=clip_end_sec)

    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    output = []
    buffer = []
    for line in lines:
        if line.strip() == '':
            if buffer:
                if len(buffer) >= 2:
                    idx, times, *text = buffer
                    m = re.match(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", times)
                    if m:
                        start = parse_srt_timestamp(m.group(1)) - datetime(1900, 1, 1)
                        end = parse_srt_timestamp(m.group(2)) - datetime(1900, 1, 1)
                        if end >= clip_start and start <= clip_end:
                            new_start = max(start, clip_start) - clip_start
                            new_end = min(end, clip_end) - clip_start
                            output.append(idx)
                            output.append(
                                f"{format_srt_timestamp(datetime(1900,1,1)+new_start)} --> {format_srt_timestamp(datetime(1900,1,1)+new_end)}\n"
                            )
                            output.extend(text)
                            output.append("\n")
                buffer = []
        else:
            buffer.append(line)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(output)

if __name__ == '__main__':
    if len(sys.argv) != 5:
        print("Usage: adjust_srt_timestamp.py <input_srt> <output_srt> <clip_start> <clip_end>")
        sys.exit(1)

    input_srt = sys.argv[1]
    output_srt = sys.argv[2]
    start_arg = sys.argv[3]
    end_arg = sys.argv[4]

    try:
        clip_start_sec = parse_time_arg(start_arg)
        clip_end_sec = parse_time_arg(end_arg)
    except ValueError as e:
        print(e)
        sys.exit(1)

    adjust_srt(input_srt, output_srt, clip_start_sec, clip_end_sec)
