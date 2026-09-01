# 動画切り抜きツール（cliptools）

YouTube 配信などの長尺動画やローカルの動画ファイルから日英字幕を作るための、
ダウンロード・文字起こし・固有名詞補正・翻訳までをカバーするツール群です。

---

## 1. ツール群の目的とできること

このリポジトリが提供するのは、次のような一連のパイプラインです。

1. **YouTube から動画／音声をダウンロード**（`download.sh`）
2. **Azure Speech (STT) / WhisperX による自動文字起こし & 文単位 SRT 生成**（`generate_srt.sh`）
3. **英語 SRT の固有名詞補正**（`fix_unique_nouns.py`）
4. **英語 SRT → 日本語 SRT 翻訳**（`translate_srt.sh` / `translate_srt_gpt.py`）
5. **上記すべてをワンコマンドで実行**（`run_all.sh`）

特徴:

- WhisperX または Azure Speech の **word-level timestamp** 相当を利用しつつ、SpaCy で自然な文単位に分割した SRT を生成
- 固有名詞用のカスタム辞書を使って、配信者名・作品名などの表記揺れを統一
- Azure OpenAI (GPT) で SRT 構造を壊さずに日本語へ翻訳

基本的な利用イメージ:

```bash
# URL を渡して、ダウンロード → 字幕生成 → 固有名詞補正 → 日本語字幕
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir -l en-US -n 1

# 既に取得済みの STT JSON（内部フォーマット: azure-stt.json）から SRT 生成以降だけを再実行
./run_all.sh -j clips/workdir/azure-stt.json -o clips/workdir -l en-US

```

---

## 2. 導入準備

### 2-1. Python / CLI のセットアップ

推奨環境: macOS + pyenv
多分ubuntuとかでも動きます

適宜 pyenv で Python 3.x をインストールし、このリポジトリ内で有効化したうえで:
```bash
pip install -r requirements.txt
```

字幕生成（`generate_srt.sh`）では音声抽出/変換に `ffmpeg` を使用します。macOS の場合は Homebrew で入れておくのが簡単です。

```bash
brew install ffmpeg xz
```
yt-dlpを動作させるために必要なdenoをインストールしておきます。
```bash
brew install pyenv deno
```

`pyenv` の Python は `xz` のインストール後にビルドしてください。`import lzma` が
失敗する既存環境では、`xz` を入れた状態で同じ Python バージョンを再インストールする必要があります。

spaCy ベースの文分割を使うため、英語モデルも追加でインストールします。

```bash
python -m spacy download en_core_web_sm
```

### 2-1-1. WhisperX（推奨: ローカルSTT）を使うためのセットアップ

WhisperX は forced alignment によって word-level timestamp の精度が安定しやすいため、
このリポジトリのローカル STT として推奨しています。

インストール:

```bash
pip install -r requirements.txt
```

（重要）macOS の MPS/Metal GPU は WhisperX が内部で利用する faster-whisper/ctranslate2 の制約により
現状サポートされません。macOS では基本的に CPU 実行になります。

### 2-2. Azure リソースの準備（Speech + Storage）

Azure 側では以下が必要です（詳細は公式ドキュメント参照）。

- Azure サブスクリプション
- Azure CLI
- Speech Services リソース
- Storage アカウント（Blob）

#### リソースグループ & Speech Service の作成例

```bash
az group create --name rg-clipworkspace --location japaneast
az cognitiveservices account create \
	--name your_speech_service_name \
	--resource-group rg-clipworkspace \
	--kind SpeechServices \
	--sku S0 \
	--location japaneast \
	--yes
```

#### Storage アカウントの作成例

```bash
az storage account create \
	--name              "${STORAGE_ACCOUNT_NAME}" \
	--resource-group    "${RESOURCE_GROUP_NAME}" \
	--location          "${LOCATION}" \
	--sku               Standard_LRS \
	--kind              StorageV2

az storage container create \
	--account-name      "${STORAGE_ACCOUNT_NAME}" \
	--name              "${CONTAINER_NAME}"
```

### 2-3. .env に設定する環境変数

このリポジトリでは、Azure Speech / Storage と Azure OpenAI まわりの設定を `.env` にまとめて管理します。

```bash
# ====== Azure Speech / Storage ======

export RESOURCE_GROUP_NAME=rg-clipworkspace
export STORAGE_ACCOUNT_NAME=xxxxxxxxxxxxxx
export CONTAINER_NAME=wavrts

# Speech Service
export SPEECH_SERVICE_NAME=my_speech_service
export SPEECH_REGION=japaneast
export SPEECH_KEY=$(az cognitiveservices account keys list \
	--name  "$SPEECH_SERVICE_NAME" \
	--resource-group "$RESOURCE_GROUP_NAME" \
	--query key1 -o tsv)

# Storage アカウントキー（generate_srt.sh では KEY ベースでアクセス）
export STORAGE_ACCOUNT_KEY=$(az storage account keys list \
	--resource-group "$RESOURCE_GROUP_NAME" \
	--account-name  "$STORAGE_ACCOUNT_NAME" \
	--query "[0].value" -o tsv)

# （任意）カスタム音声認識モデルを使う場合は self URL を設定
# 例: https://<speech-resource>.cognitiveservices.azure.com/speechtotext/v3.2/models/<model-id>
export SPEECH_CUSTOM_MODEL_SELF=""

# ====== Azure OpenAI (固有名詞補正 / 翻訳) ======

export ENDPOINT_URL="https://<your-openai-resource>.openai.azure.com/"
export AZURE_OPENAI_API_KEY="..."
export DEPLOYMENT_NAME="gpt-5.1-chat"  # または利用しているデプロイ名
```

読み込み:

```bash
source .env
```

---

## 3. 各ツールの使い方

ここでは、メインの 5 つのツールだけを説明します。

1. `run_all.sh`      – 一括処理用の統合スクリプト
2. `download.sh`     – YouTube からのダウンロード＆クリップ
3. `generate_srt.sh` – WhisperX / Azure STT → 文単位 SRT 生成
4. `fix_unique_nouns.py` – 英語 SRT の固有名詞補正
5. `translate_srt.sh` / `translate_srt_gpt.py` – 英語 → 日本語 SRT 翻訳

---

### 3-1. run_all.sh（フルパイプライン）

`run_all.sh` は、ダウンロード → 字幕生成 → 固有名詞補正 → 日本語翻訳という一連の流れを
ワンコマンドで実行する統合スクリプトです。

主な処理:

1. `download.sh` で YouTube から動画／音声を取得（または既存ファイルをそのまま利用）
2. `generate_srt.sh` で STT → 文単位 SRT 生成（デフォルト: WhisperX / オプション: Azure Speech）
3. `fix_unique_nouns.py` で英語 SRT の固有名詞を補正
4. `translate_srt.sh` で日本語 SRT を生成

URL から取得した場合、ダウンロード完了後に出力ディレクトリへ `source_metadata.json` も保存します。
このファイルには入力 URL、ダウンロードしたメディアファイル、取得日時、クリップ範囲、および取得可能な動画タイトル・配信者・公開日などの情報が含まれます。

基本的な使い方:

```bash
# YouTube の URL からダウンロードして全文処理（話者 1 人想定）
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir 

# 時間範囲を指定して処理
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir --clip 00:03:45 00:18:32

# 複数の時間範囲を指定して、それぞれ別サブディレクトリで処理
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir --clip 00:03:45 00:18:32 00:25:00 00:31:10

# 既存のローカルファイルを入力にして処理
./run_all.sh -f clips/workdir/input.mp4 -o clips/workdir

# 既に取得済みの STT JSON（内部フォーマット: azure-stt.json）からダウンロードをスキップして処理
./run_all.sh -j clips/workdir/azure-stt.json -o clips/workdir -l en-US
```

主なオプション:

- `-u, --url`    : YouTube の URL（ダウンロードから開始）
- `-f, --file`   : 既存のローカルメディアファイル（ダウンロードをスキップ）(-uか-fどちらか必須)
- `-o, --outdir` : 出力ディレクトリ
- `-l, --locale` : STT 言語（例: `en-US`）
- `--engine`     : STT エンジン（`azure` または `whisperx`）。省略時は `generate_srt.sh` のデフォルト（whisperx）
- `--clip S E [...]` : `hh:mm:ss` 形式の開始／終了時刻ペアを指定して切り抜き。複数ペア指定可(Option)
- `--audio`      : 音声のみをダウンロードして処理(Option)
- `-n` / `-m` / `-N` : 話者数の固定／最小／最大 (Option / デフォルト1)
- `-j, --from-json` : 既にマージ済みの STT JSON（通常は `azure-stt.json`）から開始（download.sh をスキップし、generate_srt.sh の `--from-json` モードを使用）
- `--from-srt`   : 既存の英語 SRT から開始（3.固有名詞補正 → 4.翻訳 のみ実行）


---

### 3-2. download.sh（ダウンロード＆クリップ）

`download.sh` は YouTube から動画／音声をダウンロードし、必要に応じて
開始・終了時刻を指定したクリップ切り出しを行うスクリプトです。

主な機能:

- フル動画のダウンロード（MP4）
- 音声だけのダウンロード（MP3, `-w/--audio-only`）
- 開始／終了時刻を指定したクリップ抽出（`-s/--start`, `-e/--end`）
- **Premiere Pro 互換の動画形式の選択**（H.264 + AACを優先）
- `--reencode` 指定時のみ、非互換形式をH.264 + AACへ再エンコード

（補足）60fps を優先して落としたい場合:

- `download.sh` は、元動画に **fps>=60 のストリームが存在する場合はそれを優先**して選びます（デフォルトON）。
- これは「60fps ストリームがあれば選ぶ」だけで、59.94→60 などの **フレームレート変換（再エンコードでの60化）はしません**。
- 無効化したい場合は `PREFER_60FPS=0` を設定してください。

#### Premiere対応と再エンコード

- 通常時は、YouTube上にあるPremiere互換のH.264 + AACストリームのうち、最も高画質なものを直接取得します。
- `--reencode` を指定した場合は、非互換形式（VP9/AV1など）も取得対象にし、出力動画を以下の条件へ変換します:
	- Video: H.264 (`libx264`), `-preset medium`, `-crf 18`, `pix_fmt=yuv420p`
	- Audio: AAC 256kbps
	- フレームレート: 元動画が 60fps なら **60fps を維持**
	- タイムスタンプ: `-vsync cfr` と `-video_track_timescale 30000` により
		固定フレームレート + Premiere が扱いやすいトラックタイムスケールに正規化
- 具体的な動作:
	- 通常時は、`yt-dlp` で **Premiere互換の範囲内で最高品質の動画** をMP4として取得
	- `--reencode` 指定時は、AV1/VP9を含む最高品質の動画をMP4として取得
	- クリッピング指定がある場合は、
		1. フル動画を一時ファイルとして保存（出力ディレクトリ内のみ）
		2. `ffmpeg -c copy` で指定範囲だけを切り出し（再エンコードなし）
		3. そのクリップがすでに H.264 + AAC + yuv420p ならそのまま採用
		4. `--reencode` 指定時で、互換形式でなければ上記設定で再エンコード
	- 通常時にPremiere互換ストリームが見つからない場合は、非互換ファイルを作らずエラー終了します。
- スクリプト終了時点で残るのは、Premiereに読み込める安全な `.mp4` のみです
	（中間ファイルや一時ファイルはすべて `-o` で指定したディレクトリ内で削除されます）。

使用例:

```bash
# フル動画をカレントディレクトリにダウンロード
./download.sh -u "https://youtu.be/xxxxx"

# 出力先ディレクトリとファイル名を指定
./download.sh -u "https://youtu.be/xxxxx" -o clips/myclip -b myclip

# 10秒〜1分までをクリップとして切り出し
./download.sh -u "https://youtu.be/xxxxx" -o clips/myclip -b myclip -s 00:00:10 -e 00:01:00

# 最高品質を取得してPremiere向けに再エンコード
./download.sh -u "https://youtu.be/xxxxx" -o clips/myclip -b myclip --reencode

# 音声のみを MP3 でダウンロード
./download.sh -u "https://youtu.be/xxxxx" -o clips/audio_only -b myaudio -w
```

---

### 3-3. generate_srt.sh（WhisperX / Azure STT → 文単位 SRT）

`generate_srt.sh` は、編集済み音声ファイル（または動画）から文字起こしを実行し、
spaCy ベースの文分割で「読みやすい長さ」の SRT を生成します。

デフォルトの STT エンジンは **WhisperX** です。
Azure Speech を使いたい場合は `--engine azure` を明示します。

主な処理フロー:

1. FFmpeg でモノラル WAV に変換
2. STT 実行（デフォルト: WhisperX / オプション: Azure Speech）
3. 出力ディレクトリに `azure-stt.json` を保存（内部フォーマット）
4. `python -m utils.json_to_srt_sentences` で適度な長さの文単位の SRT を生成 (文を適切な長さで分割するためにSpaCyを利用)

使い方:

```bash
./generate_srt.sh [--engine azure|whisperx] [-o OUTDIR] [-n NUM] [-m MIN] [-M MAX] <audio.(wav|mp4|m4a|flac|aac)> [en-US|ja-JP]
./generate_srt.sh --from-json <azure-stt.json> [-o OUTDIR] [en-US|ja-JP]
```

主なオプション:

- `--engine azure|whisperx` : 利用する STT エンジン（省略時: **whisperx**）
- `-o OUTDIR`      : 出力先ディレクトリ（省略時は入力ファイル／JSON と同じディレクトリ）
- `-n NUM`         : 話者数を固定（例: `-n 1` で 1 人）
- `-m MIN`         : 話者数の最小値
- `-M MAX`         : 話者数の最大値
- `--from-json`    : 既に取得済みの STT JSON（内部フォーマット。通常は `azure-stt.json`）から SRT のみ再生成するモード
- `LOCALE`         : `en-US` または `ja-JP`（省略時は `en-US`）

WhisperX エンジン関連の環境変数（`--engine whisperx` のとき）:

- `WHISPERX_MODEL` : モデル名（既定: `large-v3-turbo`）
- `WHISPERX_VAD_METHOD` : VAD（既定: `silero`）
- `WHISPERX_CHUNK_SIZE` : VAD 区間をまとめる最大秒数（既定: `30`）。短くすると区間が細かくなる一方、文脈不足で認識結果が悪化する場合があります
- `WHISPERX_VAD_ONSET`, `WHISPERX_VAD_OFFSET` : VAD 閾値（省略時は WhisperX の既定値）。発話の取りこぼしや余分な無音がある場合の調整用です。WhisperX 3.8.6 の Silero 実装では `VAD_OFFSET` は使用されません
- `WHISPERX_END_ANCHORED_ALIGNMENT` : forced alignment が merged VAD 区間の末尾を1秒超使わずに完了した場合、末尾を考慮する探索へフォールバック（既定: `1`）。後半の発話が区間前方の似た音へ割り当てられ、字幕が数秒早く出る事象を抑えます。WhisperX 3.8.6 本来の挙動との比較時だけ `0` にします
- `WHISPERX_DEVICE` : 実行デバイス（既定: `cpu`）
- `WHISPERX_COMPUTE_TYPE` : 計算精度（既定: **CPU の場合 `int8` / GPU の場合 `float16`**。環境変数で上書き可能）

WhisperX `3.8.6` を使用し、実行時にも最低版を検査します。3.8.6 標準の forced alignment は
典型的な開始誤差を改善する一方、長い merged VAD 区間ではアラインメントを音声より早く完了させる場合があります。
既定の選択的な終端補正ラッパーは3.8.6のモデルとCLIを維持したまま、末尾を1秒超残す疑わしい探索だけを補正します。

字幕の分割・表示時間関連の環境変数:

- `CLIPTOOLS_MAX_SUBTITLE_CHARS` : 1字幕の目安文字数（既定: `60`）。必ず空白＝単語境界で分割し、単語途中では分割しません。`0` 以下で長さによる分割を無効化できます
- `CLIPTOOLS_SUBTITLE_TAIL_SECONDS` : 最後の単語が終わった後も字幕を残す秒数（既定: `0.8`）。次の字幕が始まる場合はその開始時刻までに制限されます

字幕用途向け（非セリフを落とす）:

- `CLIPTOOLS_DROP_NON_SPEECH=1` : `*music*` や `*laughs*` のような **`*...*` だけで構成されたブロック**を SRT から除去します（CC ではなく通常字幕向け）
- `CLIPTOOLS_DROP_TEXTS` : 追加で落としたいテキストの正規表現をカンマ区切りで指定します（例: `^thank you\\.?$`）

※このフィルタは、WhisperX / Azure どちらのエンジンでも共通の「内部 JSON → SRT」段階で適用されます。

Azure エンジン使用時に必要な環境変数:

- `STORAGE_ACCOUNT_NAME`, `STORAGE_ACCOUNT_KEY`, `CONTAINER_NAME`
- `SPEECH_REGION`, `SPEECH_KEY`
- （任意）`SPEECH_CUSTOM_MODEL_SELF`

例:

```bash
# default (whisperx)
./generate_srt.sh input.mp4 en-US

# Azure を使う
./generate_srt.sh --engine azure input.mp4 en-US

# WhisperX を使う（デフォルト）
./generate_srt.sh --engine whisperx input.mp4 en-US
```

出力:

- `OUTDIR` 配下に `Speaker1_en-US.srt` のような形で話者ごとの SRT が生成されます。
- 通常モードでは、同じディレクトリに `azure-stt.json` も保存され、後から `--from-json` で再利用できます。

（補足）WhisperX のログ:

- WhisperX 実行ログは `OUTDIR/logs/` 配下に保存されます（端末には進捗だけ表示）。

---

### 3-4. fix_unique_nouns.py（固有名詞補正）

`fix_unique_nouns.py` は、英語 SRT 内の固有名詞（名前・作品名など）の表記揺れや聞き取り間違いをAzure OpenAI + カスタム辞書で補正するスクリプトです。音声ベースではなく生成された「怪しい名詞」を辞書ベースで「多分これのことだろうな」ベースで修正するので完璧ではありません。あくまで編集負荷を下げる目的です。

機能:

- 入力 SRT をブロック単位で分割し、
	`custom_dictionary/mydictionary.txt` に基づいて固有名詞候補の表記を統一
- タイムスタンプやインデックスは絶対に書き換えず、テキスト行のみを修正
- 差分を標準エラーにカラー表示（どの語がどう変わったかの確認用）

使用例:

```bash
python fix_unique_nouns.py Speaker1_en-US.srt -o Speaker1_en-US_fixed.srt
```

主なオプション:

- `--dict`   : 辞書ファイルのパス（既定: `custom_dictionary/mydictionary.txt`）
- `--prompt` : 固有名詞補正用プロンプト（既定: `settings/checkunique_prompt.txt`）

---

### 3-5. translate_srt.sh / translate_srt_gpt.py（英語 → 日本語 SRT）

`translate_srt.sh` は、英語 SRT を Azure OpenAI (GPT) を使って日本語 SRT に翻訳する
シンプルなラッパースクリプトです。内部で `translate_srt_gpt.py` を呼び出します。

使い方:

```bash
./translate_srt.sh -i Speaker1_en-US_fixed.srt -o clips/output_dir
```

- `-i` : 入力となる英語 SRT
- `-o` : 出力ディレクトリ（省略時は入力 SRT と同じディレクトリ）

翻訳結果のファイル名ポリシー:

- 入力ファイル名に `en-US` を含む場合は、`en-US` を `ja-JP` に置き換えた名前で出力
	- 例: `Speaker1_en-US.srt` → `Speaker1_ja-JP.srt`
	- 例: `Speaker1_en-US_fixed.srt` → `Speaker1_ja-JP_fixed.srt`
- それ以外の場合は、`ja-<元ファイル名>` という形で出力

`translate_srt_gpt.py` の中では:

- SRT をブロック単位に分解
- 一定数のブロックをグループ化して Azure OpenAI に投げ、SRT 構造を保ったままテキストだけ翻訳
- タイムスタンプは元 SRT からそのまま引き継ぐ（壊さない）
