# AFMCLI のローカル文書検索

`afmcli search` は、手元の文書から複数のクエリに関連する文書IDを返す。日英の単語と部分文字列の重なりを整数で採点する。検索は独立したプログラムで完結する。Apple Foundation Models（AFM）の呼び出しと内部のデバイス配置はシステム側が管理する。

## ビルドと使用例

Apple Silicon、macOS 14.4以降、対応するXcode Command Line Toolsを使う。リポジトリのルートで実行する。

```sh
cargo build --release
xcrun swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macos14.4 \
  tools/search/*.swift -o target/release/afm-search
mkdir -p "$HOME/.local/bin"
install -m 755 target/release/afmcli target/release/afm-search "$HOME/.local/bin/"
```

`afmcli search` と `afm-search` は同じ引数を受け取る。次の例は2文書に対して2件の検索を行う。

```sh
printf '%s\n' '[{"id":"出荷","text":"港の出荷予定は木曜です"},{"id":"倉庫","text":"倉庫の棚を確認する"}]' \
  | afmcli search index ./my-index
printf '%s\n' '{"queries":["出荷予定","倉庫の棚"],"top_k":1,"all_scores":true}' \
  | afmcli search query ./my-index --mode interactive
```

- `index INDEX` は標準入力からUTF-8のJSON配列を受け取る。各文書には一意の文字列 `id` と `text` が必要。索引は `INDEX/afm-search-index.json` に、ID・入力順と特徴量を保存する。原文の保管は呼び出し側が担当する。同じ場所で再作成すると新しい索引に一括で切り替わる。起動済みの検索プロセスは開始時に読み込んだ索引を使う。
- `query INDEX [--mode MODE]` はJSONオブジェクトを1件処理する。`queries` は文字列の配列。`top_k` の既定値は10、範囲は0～1,000。`all_scores` の既定値は `false`。
- `serve INDEX [--mode MODE]` は同じ形式のリクエストを標準入力から1行ずつ受け取る。行ごとに1件のJSONを返す。索引、初期化済みのデバイス、校正結果をこのプロセス内で再利用する。索引の更新後やCore MLの準備後は再起動して読み込む。
- 成功時は `status` が `ok` で、`results` はクエリ順に並ぶ。`query_index` は0始まり。`hits` は正のスコアを降順、同点は元文書順で最大 `top_k` 件を返す。`all_scores:true` のときは各結果の `scores` にゼロを含む全文書のスコアが元文書順で入る。`index_sha256` で使用した索引を識別できる。
- `routes` は返した結果のクエリ数を `cpu`、`metal`、`coreml_ane_preferred` ごとに返す。`parallel_pairs` は両経路で完了した並列組の数。`warmup` はCPUの全スコアと初回照合した経路、`fallbacks` はデバイス未対応や初期化・実行失敗の理由。`calibration` は3巡の計算時間（ミリ秒）と選択結果で、校正用の重複計算は `routes` に加えない。
- 不正な入力では `status:"error"` と `error` を返す。単発モードは非ゼロで終了し、常駐モードは次の行を受け付ける。文書は1～65,536件、クエリは1リクエストに最大65,536件。文書IDはUTF-8で512バイト、個々の原文とクエリは65,536バイトまで。索引作成入力は128 MiB、検索入力は1リクエスト64 MiB、返すヒット件数の上限は100万件、全スコアは1,677万7,216要素まで。

### スコア

文字列をUnicodeの互換正規化と大文字・小文字の折りたたみで揃える。漢字・かなの連続部分には1～3文字、その他の英数字の連続部分には単語・隣接単語対・3文字の特徴を使う。各特徴をSHA-256で1,024個のビット位置に写し、文書とクエリで同時に立つビットの数をスコアとする。範囲は0～1,024。ハッシュで同じ位置に写る特徴や、同語の反復は1ビットとして数える。検索結果を原文と一緒に表示する場合は、返ったIDで取得する。

## 優先目的と実行条件

`cpu` は任意の対応サイズで、64ビット整数の論理積とビットカウントを使う。`interactive` が既定で、AFMの応答と検索結果の両方を待つ場面を想定する。`throughput` は複数の独立した検索をまとめて終えることを優先する。どちらも次の条件で利用者の実データを計測する。

| 入力と状態 | 使用する経路 |
| --- | --- |
| 文書が4,096件未満または16,384件超、1,024件に満たないクエリ・端数 | CPU |
| 単発の `query`、全モード・全サイズ | CPU。デバイス初期化と校正を省く |
| 常駐の `serve` で最初の完全なブロック | 最初のブロックはCPUで返し、同じユーザー入力でGPUを検証・校正。throughputは準備済みCore MLも検証 |
| 校正済みのinteractive | GPUの中央値がCPUの1.10倍以内ならMetal、それ以外はCPU |
| 校正済みのthroughput、完全なブロックが1個 | CPU・Metalで中央値が短い方 |
| 校正済みのthroughput、完全なブロックが2個以上 | 異なる2ブロックをCPUのみ、Metalのみ、MetalとCore MLで1個ずつ同時に計算。分担が速い単一路より5%以上短ければ以後も分担し、それ以外は速い単一路 |

Metalは文書と新しいクエリの0/1行列をFP32のMPSで掛ける。Core MLは文書を重みに固定し、毎回届いたクエリをFP16の1×1畳み込みに渡す。すべての整数スコアがFP16でも厳密に表せる。Core MLは `.cpuAndNeuralEngine` を指定し、公開実行計画で畳み込みの優先デバイスがNeural Engineのモデルを使う。`coreml_ane_preferred` はこの構成で予測が返ったことを表す。物理デバイスでの命令ごとの実行は計測していない。

3巡の校正で変えるのは、経路別の入力行列への変換、計算、全スコアの取り出しである。並列校正では2個の新規ブロックの全要素をCPUと照合する。入力文字列の特徴化、順位付け、JSONは各経路に共通する。校正はプロセス・索引ごとに行う。GPUを使えない場合はCPU、Core MLの索引・適切な実行計画がない場合は校正済みの単一路を使う。途中でデバイスの実行が失敗したブロックは利用可能な経路で再計算する。16,384文書の上限は、Metalの常駐する文書行列と出力行列を合わせて128 MiB以内に収めるために設けた。

## Core ML索引の準備（throughput用）

CPU・Metalでの使用にはSwift製の実行ファイルだけで足りる。Core MLでの分担を使う場合は、索引作成後に次を一度実行する。ビルダーは公開の `coremltools` とNumPyを使う。通常の検索時にはPythonを起動しない。

```sh
python3.12 -m venv .venv-afm-search
.venv-afm-search/bin/python -m pip install 'coremltools==9.0'
.venv-afm-search/bin/python tools/search/prepare_coreml.py ./my-index
afmcli search serve ./my-index --mode throughput
```

索引に対応する `search-<index_sha256>.mlmodelc` を同じディレクトリ内に作る。ビルダーはそこに生成物すべてのSHA-256を記録してから一括で配置する。文書の入れ替え後は再度ビルダーを実行する。常駐throughputがCore MLを初期化するときだけ、ファイルの有無とハッシュを照合し、次に公開APIで実行計画、モデルの入出力形状、実データの全スコアを検査する。単発、少数のクエリ、interactiveの各経路ではCore ML索引を開かない。

`fallbacks` に `coreml_bundle_unverified` が出た場合、検索は利用可能な単一路で続く。ビルダーも既存の破損索引を検出すると非ゼロで終了する。対象の常駐検索を終了後、その `.mlmodelc` ディレクトリだけを別名に移して保存し、ビルダーを再実行してから検索を起動する。`afm-search-index.json` の保管場所は同じでよい。

## 検証

11月27日の再試験では、macOSの一時フォルダを別名経由で開くと、正常なCore ML索引でも整合性検査に失敗する事例を再現した。索引からの相対パスで各ファイルのハッシュを照合するよう修正した。通常の一時フォルダと追加の別名経由の両方でGPU・ANE優先のCore MLが利用でき、8,392,704スコアと順位が前回と一致した。常駐6巡の中央値はCPUが81.988 ms、interactiveが62.377 ms、throughputが52.799 ms。単発3巡は各モード96.9～97.9 msだった。[常駐と整合性検査の結果](../../eval/mode_improvements/results/search/warm.json)・[単発の結果](../../eval/mode_improvements/results/search/cold.json)

次の小規模試験は、2言語の参照スコア、順位、同点、索引の入れ替え、不正入力、常駐プロセスの動作を公開コマンド経由で照合する。

```sh
python3 tools/search/test_search.py --binary target/release/afm-search
```

実機試験は、追跡中のRustソースの4,096行を索引化し、そこから異なる3,073件のクエリを作る。初回1,024件で温め、残る2,049件の全スコアと上位3件をCPU・interactive・throughputで照合した。Apple M6、macOS 27.2.1で各モード8,392,704スコアと順位が一致した。Core ML索引を用意しないthroughputでは、2,048件がMetal、端数1件がCPUで一致した。別途、Metalを利用できない環境で1,024クエリを実行し、4,194,304スコアと上位結果が単発CPUと一致した。

同じ2,049件の上位3件を常駐プロセスに要求し、CPU・interactive・throughputの順序を交替して6巡した。下表はクライアントから見た中央値で、パイプ往復、文字列の特徴化、全件採点、順位付け、JSONの送受信を含む。索引の作成、デバイスの初期化と校正は計時の前に済ませた。特徴文字列のハッシュを再利用し、クエリの特徴ベクトルと検索結果は毎回計算している。測定中のAFM呼び出しは0件。初回の6巡でも同じ経路が選ばれた。[最終版の6巡・全スコア・破損索引の復旧](verification-final.json) [初回の6巡](verification.json)

| モード | 2,049件の待ち時間 | 採用経路 |
| --- | ---: | --- |
| cpu | 82.552 ms | CPU 2,049件 |
| interactive | 62.624 ms | Metal 2,048件、CPU 1件 |
| throughput | 52.895 ms | Metal 1,024件、ANE優先Core ML 1,024件、CPU 1件 |

単発の `query` は同じ索引とクエリを用意し、観測ごとに新しいプロセスを起動して3巡した。索引ファイルとコンパイル済みCore MLモデルは事前に作成した。単発でも初期化・校正した試作では初回費用が大きく、interactiveは219.352 ms、throughputは257.951 msだった。最終版は単発をCPUに固定し、全モードで95 ms前後、返った順位は同一だった。下表の冷時はプロセス起動からの時間である。[校正付き単発の3巡](cold-before.json) [最終版の単発3巡](cold.json)

| 単発の方針 | cpu | interactive | throughput |
| --- | ---: | ---: | ---: |
| 試作・該当サイズで校正 | 93.081 ms | 219.352 ms | 257.951 ms |
| 最終版・CPU | 94.908 ms | 95.138 ms | 94.927 ms |

旧試験では2組の合成質問を480回処理し、AFMの応答と検索を両方待つ条件ではGPU単独を使った検索が最短だった。今回の実装では文書・クエリの内容、ハードウェア、初期化状態で所要時間が変わるため、利用者の入力で校正する。[旧試験の測定条件](../../eval/combinations/hardware/batched/README.md)

```sh
python3 tools/search/test_search.py --binary target/release/afm-search --hardware \
  --coreml-python .venv-afm-search/bin/python --report search-verification.json \
  --source-commit d10fa71fc71cf1b1d2618bfab8e4a90f37a8fe92
```

同じ引数の `--hardware` を `--cold` に替えると、新しいプロセスを9回起動して単発の3巡を記録する。Metalを利用できない環境では `--expect-no-gpu` でCPUへの復帰と全出力を照合できる。
