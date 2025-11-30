# 動画切り抜きツール（cliptools）

YouTube 配信などの長尺動画やローカルの動画ファイルから日英字幕を作るための、
ダウンロード・文字起こし・固有名詞補正・翻訳までをカバーするツール群です。

---

## 1. ツール群の目的とできること

このリポジトリが提供するのは、次のような一連のパイプラインです。

1. **YouTube から動画／音声をダウンロード**（`download.sh`）
2. **Azure Speech (STT) による自動文字起こし & 文単位 SRT 生成**（`generate_srt.sh`）
3. **英語 SRT の固有名詞補正**（`fix_unique_nouns.py`）
4. **英語 SRT → 日本語 SRT 翻訳**（`translate_srt.sh` / `translate_srt_gpt.py`）
5. **上記すべてをワンコマンドで実行**（`run_all.sh`）

特徴:

- Azure Speech の **word-level timestamp** を利用しつつ、SpaCy で自然な文単位に分割した SRT を生成
- 固有名詞用のカスタム辞書を使って、配信者名・作品名などの表記揺れを統一
- Azure OpenAI (GPT) で SRT 構造を壊さずに日本語へ翻訳

基本的な利用イメージ:

```bash
# URL を渡して、ダウンロード → 字幕生成 → 固有名詞補正 → 日本語字幕
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir -l en-US -n 1
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
yt-dlpを動作させるために必要なdenoをインストールしておきます。
```bash
brew install pyenv deno
```

spaCy ベースの文分割を使うため、英語モデルも追加でインストールします。

```bash
python -m spacy download en_core_web_sm
```

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
3. `generate_srt.sh` – Azure STT → 文単位 SRT 生成
4. `fix_unique_nouns.py` – 英語 SRT の固有名詞補正
5. `translate_srt.sh` / `translate_srt_gpt.py` – 英語 → 日本語 SRT 翻訳

---

### 3-1. run_all.sh（フルパイプライン）

`run_all.sh` は、ダウンロード → 字幕生成 → 固有名詞補正 → 日本語翻訳という一連の流れを
ワンコマンドで実行する統合スクリプトです。

主な処理:

1. `download.sh` で YouTube から動画／音声を取得（または既存ファイルをそのまま利用）
2. `generate_srt.sh` で Azure Speech による SRT 生成（+spaCy ベースの文単位分割）
3. `fix_unique_nouns.py` で英語 SRT の固有名詞を補正
4. `translate_srt.sh` で日本語 SRT を生成

基本的な使い方:

```bash
# YouTube の URL からダウンロードして全文処理（話者 1 人想定）
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir 

# 時間範囲を指定して処理
./run_all.sh -u "https://youtu.be/xxxxx" -o clips/workdir --clip 00:03:45 00:18:32

# 既存のローカルファイルを入力にして処理
./run_all.sh -f clips/workdir/input.mp4 -o clips/workdir
```

主なオプション:

- `-u, --url`    : YouTube の URL（ダウンロードから開始）
- `-f, --file`   : 既存のローカルメディアファイル（ダウンロードをスキップ）(-uか-fどちらか必須)
- `-o, --outdir` : 出力ディレクトリ
- `-l, --locale` : STT 言語（例: `en-US`）
- `--clip S E`   : `hh:mm:ss` 形式で開始／終了時刻を指定して切り抜き(Option)
- `--audio`      : 音声のみをダウンロードして処理(Option)
- `-n` / `-m` / `-N` : 話者数の固定／最小／最大 (Option / デフォルト1)

---

### 3-2. download.sh（ダウンロード＆クリップ）

`download.sh` は YouTube から動画／音声をダウンロードし、必要に応じて
開始・終了時刻を指定したクリップ切り出しを行うスクリプトです。

主な機能:

- フル動画のダウンロード（MP4）
- 音声だけのダウンロード（MP3, `-w/--audio-only`）
- 開始／終了時刻を指定したクリップ抽出（`-s/--start`, `-e/--end`）

使用例:

```bash
# フル動画をカレントディレクトリにダウンロード
./download.sh "https://youtu.be/xxxxx"

# 出力先ディレクトリとファイル名を指定
./download.sh "https://youtu.be/xxxxx" clips/myclip myclip

# 10秒〜1分までをクリップとして切り出し
./download.sh -s 00:00:10 -e 00:01:00 "https://youtu.be/xxxxx" clips/myclip myclip

# 音声のみを MP3 でダウンロード
./download.sh -w "https://youtu.be/xxxxx" clips/audio_only myaudio
```

---

### 3-3. generate_srt.sh（Azure STT → 文単位 SRT）

`generate_srt.sh` は、編集済み音声ファイル（または動画）から Azure Speech のバッチ文字起こしを実行し、
spaCy ベースの文分割で「読みやすい長さ」の SRT を生成します。

主な処理フロー:

1. FFmpeg でモノラル WAV に変換
2. Azure Storage に音声をアップロード
3. Azure Speech (Speech to Text v3.2) でバッチ文字起こしジョブを作成
4. 結果 JSON (`tmp_script.json`) をダウンロード・マージ
5. `python -m utils.json_to_srt_sentences` で適度な長さの文単位の SRT を生成 (文を適切な長さで分割するためにSpaCyを利用)

使い方:

```bash
./generate_srt.sh [-o OUTDIR] [-n NUM] [-m MIN] [-M MAX] <audio.(wav|mp4)> [en-US|ja-JP]
```

主なオプション:

- `-o OUTDIR` : 出力先ディレクトリ（省略時は入力ファイルと同じディレクトリ）
- `-n NUM`    : 話者数を固定（例: `-n 1` で 1 人）
- `-m MIN`    : 話者数の最小値
- `-M MAX`    : 話者数の最大値
- `LOCALE`    : `en-US` または `ja-JP`（省略時は `en-US`）

出力:

- `OUTDIR` 配下に `Speaker1_en-US.srt` のような形で話者ごとの SRT が生成されます。

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
