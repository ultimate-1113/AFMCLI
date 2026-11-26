# AFMの応答中に文書検索を連続実行したときの所要時間

Apple M6で4,096文書に対する1,024件の検索を480回続けると、検索単独の総時間はMetalで1.236秒、Accelerateで2.222秒だった。オンデバイスのApple Foundation Models（AFM）が同時に応答すると、AFMの所要時間は同じ巡の単独実行からMetal併用で3.75%、Accelerate併用で18.74%増えた。いずれも7巡の中央値である。対象は日英の港湾記録から生成した合成負荷で、利用者から届いた質問の件数は測定していない。[同時実行の集計](results/contention-summary.json)

## 1バッチごとの比較

文書と質問をそれぞれ1,024個の0/1特徴に変換した。検索スコアは文書と質問で共通する特徴の個数である。CPU側ではスレッド数をAccelerateが決める `cblas_sgemm`、GPU側では実デバイスに送信する `MPSMatrixMultiplication` を使用した。いずれもFP32の入力と出力を使う。Accelerate内部での行列演算器の使用状況は観測していない。

各形状で常駐入力と毎回渡す入力を測った。各方式を2回ウォームアップし、続く8組でCPUとGPUの順番を交互に変えた。各組は同じ質問を用い、二つの質問集合を切り替えた。次表は常駐入力の受付から全スコアをホスト配列にコピーするまでの中央値である。[全試行](results/survey.json)・[入力を毎回渡した場合を含む集計](results/survey-summary.json)

| 文書数 | 質問数 | Accelerate | Metal | Metal / Accelerate |
|---:|---:|---:|---:|---:|
| 256 | 1 | 0.0025 ms | 0.193 ms | 77.28 |
| 1,024 | 32 | 0.0845 ms | 0.267 ms | 3.16 |
| 2,048 | 128 | 0.361 ms | 0.796 ms | 2.21 |
| 4,096 | 256 | 1.098 ms | 1.238 ms | 1.13 |
| 4,096 | 512 | 2.177 ms | 2.142 ms | 0.984 |
| 4,096 | 1,024 | 4.360 ms | 4.656 ms | 1.068 |

採用条件は、常駐入力の比が0.90未満になる最小の形状と事前に定めた。該当する形状は0件だった。この場合に使うと定めていた最大形状を、以下の診断試験に用いた。最大形状で毎回質問を渡した場合の比は1.069だった。Metalでは再利用できる共有バッファに質問をコピーし、Accelerateでは呼び出し元の連続配列を直接読んだ。デバイス初期化、バッファ作成、原文の特徴変換は別に記録した。

## 480バッチの診断試験

検索単独、AFM単独、検索とAFMの同時実行を計7条件で測り、各条件が測定順の各位置に一度ずつ来るよう7巡した。検索では毎回、4,194,304件のスコアをホストにコピーし、全要素を照合した。各巡で単独のAFMと同時実行時のAFMの比を求めた。[計測記録](results/contention.json)

| 検索方式 | 検索単独・480回 | AFMと同時・480回 | 同時実行時のAFM | AFMの増加率の中央値（7巡の範囲） |
|---|---:|---:|---:|---:|
| AFM単独 | — | — | 2.512秒 | — |
| Accelerate | 2.222秒 | 2.691秒 | 2.960秒 | 18.74%（16.58–24.20%） |
| Metal | 1.236秒 | 1.428秒 | 2.603秒 | 3.75%（2.71–5.97%） |
| Core ML・ANE優先 | 1.567秒 | 1.724秒 | 2.634秒 | 4.88%（4.03–5.89%） |

Core MLの実行計画で畳み込みの優先デバイスは32コアのNeural Engineだった。同じ0/1特徴をFP16で計算し、最大1,024の整数スコアを照合した。各バッチの計算と全出力コピーの中央値は、検索単独でCore ML 0.822 ms、Metal 2.277 msだった。480回分の総時間には、Core ML側では全FP16バイト列のハッシュ計算と各予測のプロセス間通信、Metal側では全要素のメモリ比較と480回をまとめた通信が含まれる。[Core MLの実行計画と読戻し](../placement/README.md)

AFMに渡した検索結果つきプロンプトは4,419入力トークンだった。公式の `fm respond --model system` の28応答はすべて同じで、指示した4件のうち先頭2件の番号と岸壁を正しく返した。4件すべてを返した応答は0件だった。AFMにプロンプトを渡してからプロセスが返るまでのうち、明示したMetalコマンドがGPU上で実行された時間は中央値39.12%だった。AFM内部の各演算の配置はこの計測の対象外である。

初回の同時実行では、Python 3.9のプロセス相対時計とGPUの起動時基準の時計が一致せず、検証で全7巡を除外した。再測定ではPythonからも `mach_absolute_time` と `mach_timebase_info` を呼び、CPU、GPU、Core MLすべての開始・終了時刻を親プロセスの時刻で照合した。バッチ数は初回校正の480に固定した。公開した同時実行の所要時間は再測定分である。[初回試行の記録](results/clock-rejected.json)

## 再生と実機測定

リポジトリ直下で次を実行すると、合成テキストから共通入力を再構築し、同時実行の集計と全15,270,400スコアを検証できる。この再生はAFMを呼び出さない。[整数演算による全要素照合](results/full-proof.json)

```sh
B=eval/combinations/hardware/batched
batch_work=$(mktemp -d)
python3 -B "$B/matrix_data.py" "$batch_work/common"
python3 -B "$B/survey.py" replay --inputs "$batch_work/common" --out "$B/results"
python3 -B "$B/integer_replay.py" --out "$B/results"
python3 -B "$B/contention.py" replay --out "$B/results"
```

実機の1バッチ測定では、macOSのSDKでMetalとAccelerateの計測プログラムをビルドする。新しい出力ディレクトリを指定すると、実機で計測してUTF-8 JSONに保存する。

```sh
xcrun clang++ -std=c++17 -O3 -fobjc-arc -DACCELERATE_NEW_LAPACK \
  -framework Foundation -framework Accelerate -framework Metal \
  -framework MetalPerformanceShaders "$B/Batch.mm" -o "$batch_work/metal-batched"
python3 -B "$B/survey.py" run --binary "$batch_work/metal-batched" \
  --inputs "$batch_work/common" --out "$batch_work/results"
```

同時実行の3方式を再測定するには、Core MLの手順で同じ `common` の行列をコンパイルする。対象の形状は新しく作った `survey-summary.json` の `selected` に従い、値が `null` なら4,096×1,024を使う。以下の `placement_work` にはコンパイル済みの索引と実行ファイルを置いた場所を指定する。[索引のビルド手順](../placement/README.md)

```sh
python3 -B "$B/contention.py" run --binary "$batch_work/metal-batched" \
  --inputs "$batch_work/common" --placement "$placement_work/placement-index" \
  --models "$placement_work/batch-indexes" --out "$batch_work/results"
```

## GPUとANE優先Core MLへの検索バッチの分担

補助検索でGPUとANE優先のCore MLを並行して使うため、アプリから独立した検索バッチを二つのプログラムに渡した。全480バッチのうち最初の240件をMetal、後の240件をCore MLに割り当て、両方のプログラムが準備できた時点で処理を開始した。どちらも4,096文書×1,024質問を計算し、二つの質問集合を120回ずつ処理した。AFMには従来と同じ文章を1回の公式システムモデル呼び出しで渡した。分担の対象はアプリの検索バッチであり、AFM内部の計算配置はシステムが管理する。

検索単独では、240件ずつの分担で全480件が0.842秒で完了した。同じ巡のMetal単独との比は中央値0.680、Core ML単独との比は0.537だった。AFMの応答中も検索部分は分担が最短だった。AFMの起動から両方が終わるまでの時間は、検索をMetalだけで処理する構成が2.523秒、分担する構成が2.573秒だった。表の検索時間は検索の受付から全出力の照合が返るまで、AFMの増加率は同じ巡のAFM単独応答時間に対する値である。各条件を7巡し、異なる測定位置に一度ずつ割り当てた。[分担試験の集計](results/split-summary.json)

| 検索の割当 | 検索単独の完了 | AFM応答中の検索完了 | AFMと検索の両方が完了 | AFMの遅延増加率 |
|---|---:|---:|---:|---:|
| AFM単独 | — | — | 2.425秒 | — |
| Metalに480件 | 1.237秒 | 1.432秒 | 2.523秒 | 4.01% |
| ANE優先Core MLに480件 | 1.567秒 | 1.660秒 | 2.549秒 | 4.98% |
| MetalとANE優先Core MLに240件ずつ | 0.842秒 | 0.868秒 | 2.573秒 | 6.20% |

AFMの応答中に検索を分担した条件では、二つの検索プログラムが同時に処理していた時間は中央値761.0 msだった。GPUでのコマンド実行区間とCore MLの予測呼び出し区間を照合すると、AFMへ入力を渡してから応答が返るまでに、両区間が重なった時間は中央値100.5 msだった。重なりは7巡すべてで観測した。Metalの時刻はコマンド完了後に取得できる実GPUの開始・終了時刻である。Core MLでは畳み込みの優先先が32コアのNeural Engineであることを実行計画で確認し、ホストから予測を呼び出して戻るまでを測定した。ANE上の実カーネルの開始・終了時刻はこのAPIからは得られない。[AppleのMetalコマンド時刻](https://developer.apple.com/documentation/metal/mtlcommandbuffer/gpustarttime)・[Core MLの優先デバイス](https://developer.apple.com/documentation/coreml/mlcomputeplan-1w21n/deviceusage/preferred)

全構成で480バッチの番号、質問集合、全スコアのダイジェストが一致した。4,419入力トークンの依頼に対するAFMの生回答も全28件で前の試験と同一だった。返ったのは指定4件のうち先頭2件で、4件完全正解は0件だった。[分担・照合・時刻・生回答の記録](results/split.json)

公開結果の再生は次のコマンドで実行できる。実機で再測定する場合は、同じ入力とコンパイル済み索引を指定し、参照する同時実行記録を新しい出力先に置く。

```sh
python3 -B "$B/split_devices.py" replay --out "$B/results"
mkdir -p "$batch_work/split"
cp "$B/results/contention.json" "$batch_work/split/"
python3 -B "$B/split_devices.py" run --binary "$batch_work/metal-batched" \
  --inputs "$batch_work/common" --placement "$placement_work/placement-index" \
  --models "$placement_work/batch-indexes" --out "$batch_work/split"
```
