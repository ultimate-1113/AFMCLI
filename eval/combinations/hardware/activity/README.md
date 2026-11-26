# AFMが動く間のGPU使用率

Mac全体のGPU使用率は、OS付属の `ioreg` から `IOAccelerator` の `PerformanceStatistics["Device Utilization %"]` を読む。通常のユーザー権限で動作する。この研究に使ったM6にはGPUが1つある。CPUは公開Mach APIの `HOST_CPU_LOAD_INFO` から集計し、全コアが動いている状態を100%とする。以下は、ほかのターミナルで好きな作業をしながら10秒観測するコマンドである。

```sh
python3 -B eval/combinations/hardware/activity/observe.py --seconds 10
```

GPUには画面描画やほかのアプリの処理も含まれる。このMacのANEサービスが公開する項目を確認したところ、演算使用率に相当する項目は見つからなかった。出力の `ane_compute_percent: null` は未取得を表す。IOServiceのビジー状態や接続数から、演算器の使用率を推定することはできない。[カウンターを使用するモニターの実装資料](https://github.com/wieslawsoltes/ActivityMonitor/blob/main/docs/METRICS.md#apple-neural-engine)

## Mac mini M6での観測

各条件3回。公開APIの `tokenCount(for:)` でテキストを数えると、入力は2,648トークン、出力は390トークンだった。出力文のSHA-256は単独・同時の全6回で一致した。表の標本数は隣り合う2回の観測がともに対象期間に含まれた数。最初の出力をまたぐ標本は生成全体の行だけに含む。[生の観測](observations.json)・[再集計値](summary.json)

| 観測区間 | 標本数 | GPU中央値 | GPU最大 | CPU平均（全コア合計を100%） |
|---|---:|---:|---:|---:|
| 待機 | 68 | 0% | 0% | 3.69% |
| AFM単独の要求から完了 | 85 | 0% | 0% | 6.93% |
| うち最初の出力まで | 16 | 0% | 0% | 5.84% |
| うち最初の出力から完了まで | 66 | 0% | 0% | 7.12% |
| 明示GPU検索だけ | 69 | 76% | 77% | 5.13% |
| GPU検索とAFMの重複時間 | 65 | 73% | 75% | 9.52% |

同じ文章を返すまでの中央値は、AFM単独で7.395秒、GPU検索を重ねると8.354秒。最初の出力までは1.575秒と1.800秒だった。6秒の作業枠で完了した検索バッチの中央値も2,328回から1,912回に減った。この測定で全試行のGPUカウンターは対照負荷に反応した。使用率の表示分解能は整数%で、250ミリ秒より短いAFMの処理と、各プロセスに帰属する割合は特定していない。仕事の量を固定した比較は別に掲載する。[固定バッチの比較](../batched/)

時刻検証の初回は、付属Python 3.9のプロセス相対時計と、Swift・Metalの起動時起点の時計が混在し、区間照合で棄却された。上表は公開Mach時計で3条件と待機をすべて取り直し、AFMの時刻、各GPUコマンドの時刻、観測時刻の包含を再生確認した値である。

## 条件を決めた実機試験

同じ合成台帳と同じ質問を `SystemLanguageModel.default` に渡し、実機が `.coreAdvanced3` を返した場合だけ生成する。各条件は3回。計測前にAFMとMetalを1回ずつ温め、各巡の冒頭に6秒待機し、AFM単独・明示GPU検索単独・両方同時の順を巡ごとに変える。検索は4,096行・1,024特徴・1,024質問の内積と全出力の照合を6秒間繰り返す。[Swift側のストリーミング処理](StreamProbe.swift)・[Metalと比較対象の実装](../batched/Batch.mm)

250ミリ秒ごとにOSが返した数値を保存し、各条件の時間内に収まる標本で集計する。`afm-to-first` はAPIへ要求してから最初の非空テキストを受け取るまで、`afm-after-first` は最後のテキストまでを指す。最初の表示には出力のバッファリングや最初の生成も含まれる。`both` はAFMとGPUの処理区間が実際に重なった時間を採用する。時刻は試験開始からの相対値。GPUの負荷が実際に掛かったことはMetalのコマンド実行時刻でも照合する。[AppleのMetal実行時刻](https://developer.apple.com/documentation/metal/mtlcommandbuffer/gpustarttime)

保存した結果はmacOS以外でも再集計できる。

```sh
python3 -B eval/combinations/hardware/activity/activity.py replay \
  --out eval/combinations/hardware/activity/observations.json
```

ライブ計測にはmacOS 27のCommand Line Toolsが必要になる。リポジトリのルートで、専用の一時ディレクトリを作って実行する。生成は `system` のみを使う。

```sh
P=eval/combinations/hardware
W=$(mktemp -d -t afmcli-activity)
xcrun swiftc -O -target arm64-apple-macos27.0 -parse-as-library \
  "$P/activity/StreamProbe.swift" -o "$W/stream"
clang++ -O3 -std=c++17 -fobjc-arc "$P/batched/Batch.mm" \
  -framework Foundation -framework Accelerate -framework Metal -framework MetalPerformanceShaders -o "$W/metal"
python3 -B "$P/batched/matrix_data.py" "$W/inputs"
```

生成した語彙特徴の行列を使い、専用の出力ファイルを指定して実行する。[バッチ検索の実装と再現](../batched/)

```sh
python3 -B "$P/activity/activity.py" run --probe "$W/stream" --metal "$W/metal" \
  --inputs "$W/inputs" --out "$W/observations.json"
```
