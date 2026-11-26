# 固定語彙による検索の Core ML 実行計画

候補文書の原文各行を1,024個の0/1特徴に変換し、その行列をCore MLの1×1畳み込みの重みにする。英語の単語・連語・単語内3文字列と、日本語の1〜3文字列をSHA-256で1,024個の区画に割り当てる。異なる語が同じ区画に入る場合もある。学習パラメータはない。新しい質問の特徴を入力すると、各行と共有する区画の数が全行分返る。同点は元の行の順序で並べる。文書の更新時には重みの作り直しと再コンパイルが必要になる。

対象は公開済みの `index-test-8k-01-p`、`index-test-8k-02-n`、`index-test-16k-02-n`、`index-test-16k-01-p`。順に 271、328、467、674 行を持つ。質問と原文は過去の fixture から再構成し、各ケースを公開コミットの `lock.json` と照合する。この試行では GitHub から取得したロックの SHA-256 とローカルの SHA-256 が `5bafed6cd3292d0723ca28f3018e8011b29eafcc396e3706eec28acc521783cb` で一致した。[公開した fixture](https://github.com/ultimate-1113/AFMCLI/tree/2683791860c1ba9249b5da61ce60e8d2a2064b0f/eval/index_status)

## 公開4文書の実測

Apple M6、macOS 27.2.1（26B131）で、Core MLはCPU、Apple M6 GPU、32コアのNeural Engineを列挙した。CPU＋ANE設定でも4件すべてで `ios16.conv` の優先デバイスはCPUだった。各問い合わせの全得点と順位は両構成の168件すべてで整数参照と一致し、再ロード後の8件でも一致した。文書ごとに異なる4つの質問はそれぞれ異なる得点列を返した。[実測JSON](documents.json)

| 構成 | 畳み込みの優先デバイス | 初回ロード | 同じパスを再ロード | 初回予測＋全読戻し | 常駐予測＋全読戻しの中央値 | 常駐JSON往復の中央値 |
|---|---|---:|---:|---:|---:|---:|
| CPU限定 | CPU（4/4） | 9.84–14.37 ms | 3.15–3.29 ms | 0.476–0.523 ms | 0.0193 ms | 0.137 ms |
| CPU＋ANE | CPU（4/4、ANEも対応） | 15.11–17.82 ms | 7.43–7.63 ms | 0.513–0.569 ms | 0.0270 ms | 0.150 ms |

常駐の中央値は構成ごとに80回。JSON往復は事前に得た質問特徴を送って全順位を受け取るまでの時間。候補側の特徴変換と重みの生成に文書あたり5.77–12.37 ms、最初の索引のMLプログラム変換・保存に217.47 ms、続く3件に16.14–17.28 msかかった。`.mlmodelc` の生成は17.80–47.57 ms、完成した索引は544–1,350 KiBだった。最初の索引の計測には変換器の初回使用の費用も含まれる。

## 大きな検索行列の実測

4,096行・1,024特徴に1,024質問をまとめた固定検索では、CPU＋ANE構成の `ios16.conv` がNeural Engineを優先した。CPU限定構成の同じ演算はCPUを優先した。各構成で2バンクの全セル（1バンク4,194,304得点）と全質問の全順位が整数参照に一致した。続く12回ずつの予測でも、両バンクの全出力バイト列が一致した。これはMetalとAccelerateの6サイズ比較でGPUが10%以上速いサイズが出なかった場合に用いると先に定めた診断サイズである。[実測JSON](batch-4096x1024.json)

| 構成 | 畳み込みの優先デバイス | 初回ロード | 常駐予測 | 常駐予測＋全読戻し | 全出力のハッシュを含むJSON往復 |
|---|---|---:|---:|---:|---:|
| CPU限定 | CPU | 23.97 ms | 2.072 ms | 2.213 ms | 4.630 ms |
| CPU＋ANE | 32コアNeural Engine | 133.80 ms | 0.705 ms | 0.854 ms | 3.301 ms |

時間欄の常駐値はそれぞれ12回の中央値。候補行列から索引の変換・保存に35.20 ms、共通の `.mlmodelc` の生成に16.94 ms、質問の並べ替えとFP16化にバンクあたり1.52–1.96 msかかった。質問バンクは事前に2組用意し、常駐プロセスは毎回交互に一方を入力した。実測した初回ロードの差109.83 msを常駐時の予測から全読戻しまでの差1.359 msで割ると、約81バッチとなる。この概算は同じコンパイル済み候補行列の初回ロード差だけを回収する条件で、AFMの回答時間は含まない。

あらかじめ比較用に選んだ2,048行・1,024特徴・128質問でも畳み込みはNeural Engine優先になった。このサイズでは全出力読戻しまでの中央値はCPU限定0.155 ms、ANE優先0.297 msで、CPU限定が速かった。両構成で2バンクの262,144得点ずつと、各質問の全順位が一致した。両サイズの報告値はCPU・GPU・ANEの3デバイスすべてを列挙できるホストで測定した。予備測定用のサンドボックスではGPUが見えず、IOSurfaceのアクセス制限も出たため、別パスに再コンパイルして取り直した。[中間サイズ実測JSON](batch-2048x128.json)

## 数値と計画の読み方

`documents.json` は4文書で実際の質問を入れたときの全行の得点と全順位を、独立した整数集合の積集合と突き合わせる。同じ索引に他の3文書の実際の質問も入れ、常駐プロセス内で出力が変わることを調べる。各構成・各文書につき初回1回と、4種類の質問を5巡する20回を記録する。全出力のハッシュと上位12行を保存する。ホストの分かち書き・ハッシュ、安定順位付け、JSON入出力はCPUで行う。

`batch-*.json` はMetal／Accelerate測定で使った同じ入力の先頭 M 行、K=1,024 特徴、決定的に生成した検索用Q件の特徴列2組から作る。入力は `[1,K,1,Q]`、重みは `[M,K,1,1]`、出力は `[1,M,1,Q]`。GPU／AccelerateはFP32、Core MLはFP16で計算する。0/1入力の内積は最大1,024で、どちらの型でも整数を正確に表せる。独立の整数検査はビット集合の論理積と `bit_count()` を使う。各構成・各バンクの初回には全セルをファイルに書き、全順位も照合する。その後はバンクを交互に送り、毎回新しい予測を実行して全出力を読み、全セルのダイジェストを照合する。

`plan.functions.main` に各演算の `preferred`、`supported`、推定コスト比がある。`all_compute_devices` と `available_compute_devices` はプロセスから見えたデバイス一覧。`preferred` は構成ごとの実行計画についての証拠で、各実行中のデバイス占有率や同時実行量を計測するAPIは使っていない。[AppleのMLComputePlan解説](https://developer.apple.com/documentation/coreml/analyzing-a-core-ml-model-s-performance-in-xcode) [AppleのDeviceUsage仕様](https://developer.apple.com/documentation/coreml/mlcomputeplandeviceusage)

`convert_and_serialize_ns` は文書固有の固定行列をMLプログラムへ変換して保存する時間、`compile_ns` は `.mlmodelc` を作る時間、`ready.load_ns` はコンパイル済みモデルを指定ポリシーでロードする時間。`.mlmodelc` の初回ロードには機種向け特殊化が入ることがあり、キャッシュはパスに依存する。`same_path_reopen` は同じパスを別プロセスで再度ロードした結果。常駐時の `predict_ns` は `prediction` の呼び出し、`readback_ns` は全出力を実ストライドに従って独立したホスト側FP16バッファへコピーする時間。`wall_ns` は呼び出し元でのJSONL往復も含む。デバイスごとの特殊化キャッシュは消去しない。[Appleのコンパイル済みモデルと特殊化キャッシュの説明](https://apple.github.io/coremltools/docs-guides/source/model-prediction.html#why-use-a-compiled-model)

## 保存済みの結果をCIで検算する

リポジトリのルートで次を実行する。Pythonの標準ライブラリだけを使い、ファイルを書き換えずに照合する。正常時は文書168試行と、バッチ8,912,896セルの得点・順位を検証した件数をJSONで表示する。不一致は終了コード1になる。

```sh
python3 -I -S -B eval/combinations/hardware/placement/replay.py
```

検算は公開fixtureのロック、原文、質問、候補重みを照合し、4文書の全得点・順位・上位12行を再計算する。2つのバッチサイズでは入力文から0/1特徴を再構成し、各バンクの得点と同点時の元行順を整数演算で求める。Core MLが保存したFP16・uint16の全出力と順位のハッシュ、Metal／Accelerate側が保存したFP32・uint16の全出力ハッシュを照合する。[共通のバッチ測定記録](../batched/results/survey.json)

実行計画の優先先・対応デバイス、初回と常駐試行の順序、予測と読戻しの時刻差、常駐時の中央値などは保存値から検査する。実機で別プロセスから再ロードした8件については、保存されている起動情報と時刻を照合できる。これら8件の得点・順位は元の実機測定で確認しており、再ロード後のハッシュは保存されていない。実機で測り直す手順は次節に示す。

## 実機での再現手順

Apple Silicon、macOS 14.4以降、Swiftコンパイラ、Python 3.12を使う。リポジトリのルートで次を実行する。作業パスは自分で新しく作った一時ディレクトリにする。モデル、専用venv、Swiftのモジュールキャッシュはそこに置く。

```sh
P=eval/combinations/hardware/placement
W=$(mktemp -d -t afmcli-placement)
python3 -m venv "$W/venv"
"$W/venv/bin/python" -m pip install --index-url https://pypi.org/simple --only-binary=:all: coremltools==9.0
swiftc -O -parse-as-library -target arm64-apple-macos14.4 -module-cache-path "$W/swift-cache" "$P/Placement.swift" -o "$W/placement-index"
"$W/venv/bin/python" -B "$P/study.py" build --work "$W"
"$W/venv/bin/python" -B "$P/study.py" measure --work "$W" --output "$W/documents.json"
```

バッチ実験は `hardware/batched` の生成器が出力した `documents.f32`、`queries.f32`、`inputs.json` を使う。`COMMON` にその3ファイルのあるディレクトリを指定する。`study.py` と `batch.py` は他の評価と同じmacOS一時ディレクトリの `afmcli-moe-system-<uid>.lock` を取ってから測定する。

```sh
"$W/venv/bin/python" -B "$P/batch.py" --work "$W" --common "$COMMON" --rows 2048 --queries 128 --output "$W/batch-2048x128.json"
```

## 同じ索引への問い合わせ

`placement-index serve DIRECTORY cpuAndNeuralEngine`（CPU限定は `cpuOnly`）は索引を一度ロードし、`ready:true` を含むJSONを1行出す。`DIRECTORY` は `$W/indexes/<case_id>` または `$W/batch-indexes/m<M>-q<Q>`。標準入力の1行ごとに1件を返し、EOFで終了する。単発処理は1件の後にEOFを送る。`client.py` の `FixedIndex` を使うと、複数の問い合わせで同じプロセスを使える。

Q=1 のときは `{"id":"q1","active":[...]}` を送ると、全行の `scores` と0始まりの `rank` を返す。`client.py BINARY DIRECTORY POLICY` は `{"id":"q1","question":"..."}` の質問文を元の検索器と同じ `vector` で変換する。

バッチは `{"id":"p0","load_query_file":"/absolute/queries.f32","query_offset":0,"key":"0"}` で質問を一度FP16に並べ替えて準備する。第二のバンクには `query_offset:1024` と別のキーを使う。`{"id":"run","prepared":"0","compact":true}` は用意した質問を入力にして改めて予測し、全出力の `scores_sha256_u16le`、`scores_sha256_f16le`、`predict_ns`、`readback_ns` を返す。各バンクの初回に整数との対応を確かめた後は `fp16_digest_only:true` でFP16バイト列のダイジェストだけ照合できる。`predict_start_ns` と `complete_ns` は他プロセスの区間と照合できる `DispatchTime.uptimeNanoseconds` による開始時刻と全セル読み戻し完了時刻。
