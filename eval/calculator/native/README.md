# AFM 3 Core Advanced の関数電卓呼び出し

この試験は Apple の `LanguageModelSession` に Swift の `ScientificCalculator: Tool` を1個登録する。対照は同じモデル、質問、greedy sampling、応答上限256トークン、`toolCallingMode: .allowed` でツール一覧を空にする。各問・各条件でプロセスとセッションを新しく作る。自然文6問の式と指示文は親ディレクトリの `probe.py` を共有する。期待値は採点側だけが読み、モデルには式と指示文だけを送る。

## ビルドと確認

macOS 27以降のApple Silicon Macで、リポジトリのルートから実行する。引数に指定する `afmcli` はローカルの `calc` サブコマンドに対応するビルドを使う。

```sh
mkdir -p /tmp/afm-calc-study
xcrun swiftc -parse-as-library -swift-version 6 -O -target arm64-apple-macosx27.0 \
  -module-cache-path /tmp/afm-calc-study/modules \
  eval/calculator/native/CalculatorProbe.swift -o /tmp/afm-calc-study/calculator-probe
/tmp/afm-calc-study/calculator-probe doctor
```

2026-11-26にmacOS 27.2.1（26B131）、Command Line ToolsのSwift 6.4.2でビルドし、`doctor` で `available=true`、`variant="AFM 3 Core Advanced"`、`core_advanced_3=true` を確認した。サンドボックス内で表示名が空になる環境では、ランナーは生成の前に停止する。Swiftは `SystemLanguageModel.default` を取得し、`.variant == .coreAdvanced3` と `.isAvailable` を各セッションでも確認する。[Appleのシステムモデル](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel)・[モデルのバリアント](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel/variant-swift.struct)

## 自然文の比較

```sh
python3 eval/calculator/native/run.py --native /tmp/afm-calc-study/calculator-probe \
  --calculator "$PWD/target/release/afmcli" --out /tmp/afm-calc-study/natural.jsonl
```

12セッションを直列に実行し、同じ問題内で2条件を隣接させ、先に実行する条件を問ごとに交代する。出力ファイルは新規作成する。明示的な指示を別に試すには `--condition explicit --mode registered --ids integer-control nested-fraction` と別の出力先を指定する。この条件では質問にツールを呼ぶよう日本語で追記する。APIの呼び出し許可は自然文と同じ `.allowed` のまま。

## 記録の読み方

- `record` にモデル名、質問、登録ツール名、各実コールバック、Frameworkの `Transcript` に現れたツール呼び出しと出力、AFMの最終文、経過時間が入る。コールバックは完了時に標準出力へ即時記録するので、AFMの最終文がタイムアウトしても終了済みの電卓呼び出しを集計できる。[AppleのToolの呼び出しとTranscript](https://developer.apple.com/documentation/foundationmodels/expanding-generation-with-tool-calling)
- `callback_count` と `successful_callback_count` を分ける。前者には電卓に拒否された式も含む。`matched_dispatch_count` はコールバックで受けた名前・式と、FrameworkのTranscriptの名前・引数が一致した件数。`matched_output_count` は電卓が返した値とTranscriptのツール出力が一致した件数。複数回呼ぶ場合は各回の式・値・正誤・時刻を `callbacks` に残す。
- `tool_direct_output` は最後に成功した電卓呼び出しの値、`tool_direct_correct` はその採点。`output` と `correct` はAFMの最終文と採点である。電卓値を得られなかった場合、直接出力とその採点はともに `null`。最後に成功した計算が元の問題の途中式であれば、その値も元の問題の正解と照合する。
- `tool_direct_finished_ms` はSwift側で要求の処理を開始してから最後に成功したコールバックが終了するまでの時間。実験はその後もモデルの最終文まで待つ。直接返して打ち切る実装の速度は実測していない。

## ツールの実装

ツールの引数は `expression` 1個で、ホストが明示パスのプログラムを `Process` で起動し、引数 `calc --angle deg` または `calc --angle rad` を固定した上で、式を標準入力へ渡す。6問では角度を度に固定する。数値が得られたときは値の文字列だけをモデルに戻す。不正な式はエラーになり、各セッションが開始できる電卓プロセスは最大8回である。CLTでの `@Generable` の型検査は `FoundationModelsMacros` プラグイン欠落で失敗したため、公開APIの `DynamicGenerationSchema` で引数を定義し、`GeneratedContent` から式を取り出している。[AppleのTool定義](https://developer.apple.com/documentation/foundationmodels/tool)・[動的な引数スキーマ](https://developer.apple.com/documentation/foundationmodels/dynamicgenerationschema)

次はハンドラ単体試験で、モデルへは送信しない。出力の `source` は `offline-check` で、モデルによる呼び出しの採点から除く。

```sh
printf '%s' '{"expression":"(7/12 + 5/18)^2"}' | \
  /tmp/afm-calc-study/calculator-probe check-calculator "$PWD/target/release/afmcli" deg
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s eval/calculator/native
```

## このMacの fm コマンド

macOS 27.2.1の `/usr/bin/fm respond --help` と `/usr/bin/fm chat --help` は `--tool` を内蔵の `barcode` と `ocr` に限定している。カスタム電卓の登録には上記のSwiftセッションを使用する。`fm serve --help` にはChat CompletionsのHTTPエンドポイントと接続設定がある。現行ビルドのサーバーを介したカスタム関数呼び出しはこの試験の測定対象外である。[AppleのCLIとPython SDK解説](https://developer.apple.com/videos/play/wwdc2026/334/)
