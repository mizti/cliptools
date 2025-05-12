# 動画切り抜きツール

## 1. 動画のダウンロード

* 動画URLを指定して.mp4形式でDLします。また、その動画の字幕ファイルのDLを行います
* また、クリップURLを指定した場合にはDL後にクリップされた範囲のみの動画と字幕を切り出します

```bash
./download.sh {URL} {Directory} {Filename}
```

* URL: YouTubeの動画もしくはクリップのURL
* Directory: 
* Filename: 

## 2. 編集済み動画に対するSpeech To Textによる字幕自動生成

* AzureのAIサービスを利用して編集済みの音声ファイルからSRTファイルを生成します
* 自動生成はできますが、ないよりマシ程度の編集土台だとお考えください

### 手順

#### 準備

それぞれ外部のドキュメントを参照して
* Azureのサブスクリプションを作成
* Azure CLIのインストール
* Azure CLIでのログイン
を行ってください

#### Azure Speech Serviceのリソース作成

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

#### Speech CLIのインストール

```bash
dotnet tool install --global Microsoft.CognitiveServices.Speech.CLI
```

#### .envファイルの作成

.envファイルに以下を書きます

```
export USER_OBJECT_ID=xxxxxxxx-xxxxx-xxxxx-xxxxxxxxxxx
export SUBSCRIPTION_ID=xxxxxx-xxxx-xxxx-xxxx-xxxxxxxxx
export RESOURCE_GROUP_NAME=rg-clipworkspace
export STORAGE_ACCOUNT_NAME=xxxxxxxxxxxxxx
export SPEECH_SERVICE_NAME=my_speech_service
export SPEECH_KEY=$(az cognitiveservices account keys list \
  --name  "$SPEECH_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --query key1 -o tsv) 
export CONTAINER_NAME=wavrts
export SPEECH_REGION=japaneast
```

```bash
source .env
```

#### ストレージの作成

```bash
az storage account create \
  --name              "$STORAGE_ACCOUNT_NAME" \
  --resource-group    "$RESOURCE_GROUP_NAME" \
  --location          "$LOCATION" \
  --sku               Standard_LRS \
  --kind              StorageV2
```

パブリック網からのアクセスを許可(自己責任で)
```bash
az storage account update --resource-group $RESOURCE_GROUP_NAME --name $STORAGE_ACCOUNT_NAME
```
上記から1分くらい待ってからコンテナ作成
```bash
az storage container create \
  --account-name      "$STORAGE_ACCOUNT_NAME" \
  --name              "$CONTAINER_NAME" \
```

### ストレージへのアクセス権の付与

マネージドIDの有効化
```bash
az cognitiveservices account identity show \
  --name "$SPEECH_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME"

```
プリンシパルIDの取得
```bash
export PRINCIPAL_ID=$(az cognitiveservices account show \
  --name              "$SPEECH_SERVICE_NAME" \
  --resource-group    "$RESOURCE_GROUP_NAME" \
  --query identity.principalId -o tsv)

```
変数設定
```bash
export SUBSCRIPTION_ID=$(az account show --query id -o tsv)
export CONTAINER_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME/blobServices/default/containers/$CONTAINER_NAME"

```
ロールのアサイン(自分)
```bash
az role assignment create \
  --assignee "$USER_OBJECT_ID" \
  --role "Storage Blob Data Contributor" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT_NAME"
```
ロールのアサイン(Cognitive Service)
```bash
az role assignment create \
  --assignee-object-id      "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role                    "Storage Blob Data Contributor" \
  --scope                   "$CONTAINER_SCOPE"
```

### 実行

```bash
./generate_rts.sh {中間出力した.wavのパス} {言語} 
```

例:
```
./generate_rts.sh clip1/temp.wav en-US
```

言語を指定しない場合はenになります
