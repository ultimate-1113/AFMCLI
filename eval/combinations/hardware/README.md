# AFMに渡す原文の選択と次の質問の先読み

Apple M6（macOS 27.2.1、26B131）で、質問に関係する原文の行を得点順に選んだ。対象は公開済みの英語・日本語の8文書で、各言語の8K・16Kにそれぞれ異なる2文書を使った。このうち4文書では、検索を始めてからAFMが回答を返すまでの時間も測った。使用した文書のID、原文のトークン数、機種、コンパイラ、コードのハッシュを記録した。[実行条件](results/run.json)

| M6での関連測定 | 測定内容 |
| --- | --- |
| [AFMの応答中の端末使用率](activity/) | CPU・GPU使用率の実測と、取得できたANEカウンターの範囲 |
| [複数の質問をまとめた行列計算](batched/) | CPUのAccelerateとMetalで明示的に動かしたGPUの所要時間、AFMと並行して動いた時間 |
| [Core MLのデバイス割り当て](placement/) | コンパイル済みモデルの実行計画が、演算ごとに示す対応デバイスと優先デバイス |
| [帳票画像のノイズ除去とAFMによる読み取り](document_image/) | vImageとMetalの画像処理時間、画像を添付したAFM要求中の端末全体のGPU使用率 |

## 原文の選び方と回答の採点

`rank_study.py` は、テキストをNFKCで正規化し、大文字と小文字を区別しない形にそろえてから、特徴の有無を0か1で表す。ASCIIの英数字からなる単語、隣接する2単語の組、単語内で連続する3文字、日本語で連続する1・2・3文字を特徴とする。各特徴をSHA-256の先頭4バイトで1,024個のバケットに振り分け、特徴が一つでも入ったバケットの値を1にする。Swiftは、原文の各行の特徴を並べた行列と質問の特徴ベクトルの積を、Core MLの公開API `MLTensor.matmul` で求める。

文書と計算ポリシーの組ごとに新しいプロセスを起動し、最初に元の質問を、その後に特徴を変えた3問を処理する。後続の各問では、一つの特徴の値を2倍にする。選ぶ特徴は原文の少なくとも1行に現れ、出現する行の組み合わせは3問で異なる。最初の質問を含め、4問の得点ベクトルがすべて異なることを確認する。

入力と計算途中の和はすべて、Float16で正確に表せる小さな整数に収まる。Swiftは `shapedArray` で計算結果を読み出し、原文の行を得点の高い順に並べる。同点なら原文で先に現れる行を優先し、全行の得点と位置を返す。独立したPython実装でも整数の加算だけで全行の得点と順位を照合し、上位12行を復元する。AFMにはこの12行を原文の順に渡す。記録には原文中のバイト位置と、入力・並び順・抜粋のSHA-256ハッシュを残す。

MLTensorの計算には、タスクごとに `.cpuOnly`（CPU限定）または `MLComputePolicy(.cpuAndNeuralEngine)`（CPUとANEを許可）を指定した。後者ではどちらのデバイスも使える。MLTensorが実際に選んだデバイスと、システム言語モデルが内部で用いたデバイスは、この試験では観測できない。[Appleの計算ポリシー](https://developer.apple.com/documentation/coreml/withmltensorcomputepolicy%28_%3A_%3A%29)・[使用を許可するデバイス](https://developer.apple.com/documentation/coreml/mlcomputeunits/cpuandneuralengine)・[テンソルの読み出し](https://developer.apple.com/documentation/coreml/mltensor/shapedarray%28of%3A%29)

AFMで回答まで試した4問には、8K・16Kの資料から、言語ごとに確定値がある問題と未確定の問題を1問ずつ選んだ。各条件で独立に原文を抜き出し、共通の公開コマンド `fm respond --model system` に新しいプロンプトを渡す。回答の値が引用行の一部と完全に一致したとき、そのバイト範囲を出典として記録する。既存の採点器 `eval/index_status/fixtures.py` では、その出典が値を正式に決めた箇所かも調べる。明示的に `null` を返す場合は値を空、行番号を0とする。回答の各項目が食い違う場合や読み取りに失敗した場合は保留にする。

## MLTensorの検索時間と回答結果

| 測定区間（中央値） | CPU限定 | CPUとANEを許可 |
| --- | ---: | ---: |
| 初回の質問テンソル作成から得点の読み出しまで（8文書） | 0.205 ms | 0.190 ms |
| 2回目以降の質問テンソル作成から得点の読み出しまで（24試行） | 0.061 ms | 0.060 ms |
| 1問を処理するSwiftプロセスの所要時間（AFMに渡す4問） | 31.712 ms | 31.829 ms |
| 同じプロセスで文書行列のテンソルを初めて作る時間 | 22.582 ms | 22.772 ms |
| 同じプロセスで入力を読み込み、JSONをデコードする時間 | 4.468 ms | 4.545 ms |
| ホストで質問ベクトルと原文の行列を準備する時間（AFMに渡す4問） | 8.457 ms | 8.708 ms |
| 検索開始からAFMが回答を返すまでの実測時間（4問） | 604.933 ms | 592.720 ms |

64試行で、延べ28,336行すべての得点と順位が独立実装と一致した。両方の計算ポリシーで同じ質問を処理すると、抜粋とAFM入力のハッシュは全問で一致した。4文書の原文は8,224〜16,431トークン、AFMに実際に渡した入力は指示とスキーマを含めて717〜887トークンだった。

両方のポリシーで、確定2問は値と正式な出典の両方が正しかった。英語の未確定問では、引用行にある候補の値を誤って採用した。日本語の未確定問では「不明」としながら候補の値と行番号も返したため、保留になった。集計には最小値・最大値・標本数も載せた。[測定結果の集計](results/summary.json)・[各試行の記録](results/observations.jsonl)

測定後に、選択したバイト範囲 `selected` を、公開済みの評価データにある `change_spans`、`context_spans` と照合した。上位12行には、状態の判定を左右する行が4問とも含まれていた。未確定の2問では前後関係を示す2行もどちらも含まれており、判断に必要な原文をAFMに渡した状態で誤採用と保留が起きた。

## 4問を逐次処理した場合と先読みした場合

`pipeline_study.py` では、同じ4問の検索と回答をすべて逐次処理する場合と、次の1問の検索だけを先読みする場合を比べる。先読みは、現在の質問を実行中の `fm respond` プロセスに書き込み、入力用のパイプを閉じてから始める。一つのワーカーが次の1問を検索し、モデルへの要求は一つのスレッドが順に送る。

単調時計で、入力を送ってから `fm respond` の結果を受け取るまでの区間、次の検索の区間、両者が重なった時間を記録する。最初の区間にはサービスとプロセスの処理時間も入る。4問全体の所要時間は、最初の検索を始める直前から4問目の回答が返るまでとした。2巡目は条件の順番と質問の順番をどちらも逆にした。OSのキャッシュは巡をまたいで残る場合がある。

`tensor` では1問ごとに文書行列を作り直し、Swiftプロセスを起動する。`fixed` では、4文書それぞれに計算ポリシー別のCore ML索引を用意し、各索引をメモリに保持する。索引を先に読み込み、別の質問で初回の予測時間を記録してから、同じサーバーで評価対象の質問を処理する。固定索引を使う方式には、コンパイルと読み込みの費用がかかり、繰り返し使うとその準備を省ける。測定記録にはAFMの回答と、出典として返したすべての原文範囲を収めた。[MLTensorを使った4問の集計](pipeline-tensor/summary.json)

| MLTensorの計算ポリシー | 巡 | 4問を逐次処理 | 4問を先読みありで処理 | 実際に重なった時間 | 4問の所要時間の短縮率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU限定 | 1 | 2,602 ms | 2,440 ms | 156 ms | 6.22% |
| CPU限定 | 2 | 2,580 ms | 2,455 ms | 140 ms | 4.84% |
| CPUとANEを許可 | 1 | 2,587 ms | 2,456 ms | 154 ms | 5.05% |
| CPUとANEを許可 | 2 | 2,586 ms | 2,457 ms | 139 ms | 4.96% |

4問それぞれで、8回すべての抜粋とAFMの構造化回答が一致した。採用した引用もすべて原文の同じバイト範囲を指した。検索時間の中央値はCPU限定で48.81 ms、CPUとANEを許可すると47.75 msだった。このうち質問テンソルの作成から積の読み出しまでにかかった時間は、それぞれ0.249 ms、0.253 msだった。先読みで重ねられた時間の大半は、質問ごとに索引を作り、受け渡す処理にかかっていた。2巡の結果はいずれも同じ1台でこの4問を測った値である。[各巡の測定記録](pipeline-tensor/batches.jsonl)

| 常駐させた固定索引の計算ポリシー | 巡 | 4問を逐次処理 | 4問を先読みありで処理 | 実際に重なった時間 | 4問の所要時間の短縮率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU限定 | 1 | 2,736 ms | 2,424 ms | 1.55 ms | 11.40% |
| CPU限定 | 2 | 2,427 ms | 2,408 ms | 1.43 ms | 0.78% |
| CPUとANEを許可 | 1 | 2,441 ms | 2,433 ms | 1.75 ms | 0.35% |
| CPUとANEを許可 | 2 | 2,443 ms | 2,424 ms | 1.50 ms | 0.77% |

コンパイル済みの文書索引をメモリに保持した場合、質問を受けてから検索を終えるまでの中央値はCPU限定で0.483 ms、CPUとANEを許可すると0.523 msだった。CPU限定、CPUとANEを許可の順に比べると、索引サーバーの起動から応答可能になるまでの中央値は6.14 msと10.75 ms、初回の予測は0.464 msと0.485 ms、評価対象の質問での予測時間の中央値は0.134 msと0.148 msだった。初期化の測定はコンパイル済みモデルから始めた。4文書の小さな畳み込み演算について、Core MLの実行計画はいずれもCPUを優先デバイスとした。[演算別の実行計画](placement/)・[固定索引の実行条件](pipeline-fixed/run.json)

CPU限定の最初の逐次処理では、回答処理を開始してから最初のプロンプトが `fm respond` に届くまでに252.7 msかかった。1巡目のほかの条件では同じ区間が36.9〜37.6 msだった。この巡のCPU限定の2条件で観測した所要時間の差は312 msで、実際に先読みが重なった1.55 msを大きく上回る。2種類の検索方式で得た計64件の回答を比べると、4問それぞれで抜粋・出典のバイト範囲・AFMの構造化回答がすべて一致した。[固定索引の集計](pipeline-fixed/summary.json)・[各巡の測定記録](pipeline-fixed/batches.jsonl)

## 再現手順

記録済みの結果は、Python 3.9以降を備えたUnix系の環境で、モデルを起動せずに検証できる。公開用の読み書きにはUTF-8のJSONLを使う。再生時には、記録・再生に使用する元のコードが実行条件ファイルのSHA-256ハッシュと一致することも確かめる。リポジトリのルートで以下を実行する。

```sh
python3 -B eval/combinations/hardware/public_records.py single verify --out eval/combinations/hardware/results
python3 -B eval/combinations/hardware/public_records.py batch verify --out eval/combinations/hardware/pipeline-tensor
python3 -B eval/combinations/hardware/public_records.py batch verify --out eval/combinations/hardware/pipeline-fixed
```

Apple Silicon搭載Macで実測する場合は、端末内モデルの確認に使う実行ファイルを `AFMCLI_DOCTOR_BIN` に指定する。測定プログラムは、このファイルを実行してシステムのAFM 3 Core Advancedが利用可能であることを確かめ、この研究で共通の排他ロックを取得してからハードウェアとモデルの測定を始める。出力先には空のディレクトリを使う。

```sh
rank_work=$(mktemp -d)
xcrun swiftc -O -parse-as-library -target arm64-apple-macos15.0 \
  -module-cache-path "$rank_work/SwiftModules" eval/combinations/hardware/Rank.swift -o "$rank_work/rank"
python3 -B eval/combinations/hardware/public_records.py single capture --binary "$rank_work/rank" \
  --doctor "$AFMCLI_DOCTOR_BIN" --out "$rank_work/single-results"
python3 -B eval/combinations/hardware/public_records.py batch capture --engine tensor --binary "$rank_work/rank" \
  --doctor "$AFMCLI_DOCTOR_BIN" --out "$rank_work/tensor-batches"
```

準備済みの固定索引で実測する場合は、文書索引サーバーの実行ファイルを `PLACEMENT_BIN` に、公開ケースIDごとの索引をまとめたディレクトリを `PLACEMENT_INDEX_ROOT` に指定する。

```sh
python3 -B eval/combinations/hardware/public_records.py batch capture --engine fixed --binary "$PLACEMENT_BIN" \
  --index-root "$PLACEMENT_INDEX_ROOT" --doctor "$AFMCLI_DOCTOR_BIN" --out "$rank_work/fixed-batches"
```
