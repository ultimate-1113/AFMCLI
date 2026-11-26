# スキャンした帳票画像の孤立ノイズ除去

文書検索と密行列積に続く探索試験として、M6で帳票画像の前処理を測った。対象は、2桁の見出し番号、罫線のある記入欄、暗い筆跡、紙面の濃淡をプログラムで描いた8ビットのグレースケール画像である。同じ条件で再現できる白と黒の点状ノイズを加え、読み取り前に5×5の正方形の近傍でオープニング処理、その後にクロージング処理を行う。各画素を近傍の最小値、最大値、最大値、最小値で順に置き換える計4回の処理となる。

## Apple M6での測定結果

すべての画像サイズで、作業領域を毎回確保する場合も再利用する場合も、初回を除く5組すべてでCPUのvImageのほうが早くホストに結果を返した。作業領域を再利用したGPUの所要時間はCPUの3.73〜5.38倍だった。転送2回とフィルター4回を含むMetalコマンドの実行区間だけを測っても、各サイズで作業領域を再利用したCPU処理全体の時間を上回った。

| 画像サイズ | CPU・毎回確保 | GPU・毎回確保 | CPU・再利用 | GPU・再利用 | Metalコマンドのみ・再利用 | 全画素の絶対誤差の合計の減少率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 512×768 | 0.147 ms | 0.594 ms | 0.142 ms | 0.529 ms | 0.327 ms | 57.33% |
| 2048×3072 | 0.622 ms | 3.284 ms | 0.588 ms | 3.132 ms | 2.506 ms | 77.04% |
| 4096×6144 | 2.607 ms | 14.669 ms | 2.402 ms | 12.925 ms | 11.643 ms | 92.28% |

表は、各条件の初回を別に記録した後の5回の中央値である。72試行で照合した延べ764,411,904画素はすべて、独立したスカラー実装と一致した。ノイズ除去による誤差の減少率は、ノイズを加える前の帳票を基準に、全画素の絶対誤差の合計から計算した。

最小サイズの帳票を、ノイズを加えた元画像、vImageの処理後、MPSの処理後の3通りで端末内のAFMに渡した。AFMは同じ見出し番号を3回とも正確に `42` と読み取った。処理後の2枚のPNGはハッシュも一致した。AFMと画像処理を並行して動かすための事前条件を満たしたサイズはなかった。[結果の集計](results/summary.json)・[全所要時間・ハッシュ・読み取り記録](results/observations.json)

## 画像処理の条件と検算

画像サイズは事前に512×768、2048×3072、4096×6144画素に固定した。CPUではAppleのvImageで単一平面の最小値・最大値フィルターを実行し、内部のタイル分割には既定設定を使う。GPUでは、Metalのプライベートテクスチャ2枚（R8Unorm）にAppleのMPS最小値・最大値フィルターをかける。どちらもホストメモリ上の同じ画像から始め、すべての画素を処理し、呼び出し元が用意した通常のバイト配列に出力する。

GPUでは、まず入力を共有ステージングメモリにコピーし、Metalのblitコマンドでプライベートテクスチャに転送する。4回のフィルター処理後にblitで結果を共有メモリへ戻し、完了を待って出力先の配列にコピーする。Metalコマンドの実行時間には往復のblitを含む。[AppleのvImage形態学フィルター](https://developer.apple.com/documentation/accelerate/morphology)・[MPSの近傍最小値](https://developer.apple.com/documentation/metalperformanceshaders/mpsimageareamin)・[MPSの近傍最大値](https://developer.apple.com/documentation/metalperformanceshaders/mpsimageareamax)・[MetalのGPU開始時刻](https://developer.apple.com/documentation/metal/mtlcommandbuffer/gpustarttime)

作業領域を毎回確保する条件では、要求ごとの一時バッファまたはテクスチャの確保もホスト側の所要時間に含める。再利用する条件ではこれらを呼び出し間で保持し、各画像の入力転送と全画素の読み戻しは毎回行う。デバイスとカーネルの生成、再利用する作業領域の確保、帳票画像の生成、スカラー実装での参照値の計算は、それぞれ別に記録した。各サイズ・各条件で初回を別に計時し、続く5組ではCPUとGPUのどちらを先に動かすかを交替した。

ホストの出力先に届いた全画素を、画像端では最も近い端の画素値を使う独立した整数実装と1バイトずつ照合した。記録には出力のハッシュ、照合した画素数、所要時間と、画像内に分散させた14点の参照値をサイズごとに残した。オフライン再生では、その参照値を別に実装した2次元の近傍計算でも検算する。読み取りの確認では、ノイズのある帳票と2通りの処理後画像を実際のPNGとして端末内モデルに添付した。画像ファイルと画像を含むセッション記録は、この確認の間だけ一時ディレクトリで扱った。

AFMと画像処理を同時に実行する試験には、事前に選択条件を設けた。作業領域を再利用する条件で、MPSの所要時間の中央値がvImageの90%以下であり、かつ5組のうち4組以上でMPSが速ければ、該当する最大の画像サイズを採用する。処理するページ数はこのサイズのGPU所要時間の中央値から先に決め、すべての条件でそろえる。

選択条件を満たした場合は、順番を変えた3巡で、AFM単独、CPUまたはGPUによる画像処理単独、各画像処理とAFMの逐次実行、各画像処理とAFMの並行実行を測る計画とした。並行実行では、独立した固定のテキスト要求でAFMに物語を生成させ、その間に次の画像群にフィルターをかける。アプリケーションはAFMのプロセスを先に起動し、300 ms後に画像処理を開始する。実際のストリーム区間と各Metalコマンドの実行区間の重なりは、システム共通のMach絶対時計で求める。最初に空でない出力が届くまでには、モデルとサービスの処理時間も含む。画素の照合はホスト側の処理区間の外で計時する。

## 画像を添付したAFM要求中の端末全体のGPU使用率

追試では、同じ質問文を、画像を添付せずに、または1枚添付して、毎回新しい端末内AFM 3 Core Advancedのセッションに送った。質問文は2種類ある。一つは大きな見出しの番号と90〜120語の説明を求め、画像がなければその旨を答えるよう明記した。もう一つは番号だけを求め、画像を渡してから生成する文章を短くした。画像には前の試験でフィルターをかけた512×768と4096×6144の帳票をそのまま使った。各要求について公開APIのセッション記録から添付画像の枚数と寸法を確かめた。画像の準備と出力トークンの集計はストリームの計測区間の外で行った。

3巡それぞれで、3秒の待機区間を1回設け、質問文と添付条件の全6通りを測った。端末のIOAcceleratorが示すGPU使用率と、公開APIで読める端末全体のCPUの累積ティック数を、目標100 ms間隔で取得した。連続する2回の読み取りがともに対象区間内に収まるとき、その組を1標本とした。

Swiftは、ストリーム要求の開始、最初に空でない出力を受けた時点、応答の完了を、使用率の測定と同じMach時計で記録する。最初の出力までの時間には、画像とテキストの前処理、その他のモデル・サービスの処理、最初のトークンの生成が含まれる。どちらの公開インターフェイスからも、画像エンコーダーだけの実行区間は取得できない。GPU使用率は端末全体の値であり、他のソフトウェアや画面の描画も含む。[使用率の測り方と、明示的にGPUを動かした対照試験](../activity/)

このM6では、待機中、画像なしの要求中、画像付きの要求中とも、対象区間内で観測したGPU使用率の最大表示は **0%** だった。実際の取得間隔は中央値100.0 ms、95パーセンタイル109.0 msで、GPU使用率の表示単位は整数%である。これより短い稼働や表示分解能に満たない稼働は判別できない。取得できたANEのレジストリ項目には演算使用率の値がなかった。[測定記録とセッションの画像情報](results/vision-activity.json)・[巡別の標本数と時間](results/vision-activity-summary.json)

| 要求 | 区間内のGPU標本数 | 最初の出力前 / 後の標本数 | 端末全体の平均CPU使用率 | ストリーム所要時間の中央値 |
| --- | ---: | ---: | ---: | ---: |
| 待機 | 86 | — | 4.22% | — |
| 説明・画像なし | 46 | 10 / 33 | 9.82% | 1,714 ms |
| 説明・512×768 | 43 | 16 / 24 | 10.54% | 1,536 ms |
| 説明・4096×6144 | 51 | 12 / 36 | 11.36% | 1,796 ms |
| 番号のみ・画像なし | 1 | 1 / 0 | 15.13% | 156 ms |
| 番号のみ・512×768 | 3 | 3 / 0 | 17.51% | 214 ms |
| 番号のみ・4096×6144 | 11 | 11 / 0 | 8.56% | 516 ms |

最初の出力をまたぐ標本は、ストリーム全体の標本数にだけ含めた。番号のみを求めた場合、最初の出力後に収まった標本は全条件で0件だった。画像付きの12回は、すべてセッション記録に画像がちょうど1枚あり、添付した元のPNGとハッシュ・寸法が一致した。12回とも見出しの番号を42と読んだ。説明の文面は、添付サイズごとに3回とも同じだった。空白で区切って数えると小画像は110語、大画像は138語だった。同じ質問文で画像を添付しない場合は、画像がないときの応答が121語だった。

## 再現手順

リポジトリのルートで次のコマンドを実行すると、Pythonの標準ライブラリだけで測定記録を検証できる。

```sh
python3 -B eval/combinations/hardware/document_image/study.py verify --out eval/combinations/hardware/document_image/results
python3 -B eval/combinations/hardware/document_image/vision_activity.py verify \
  --raw eval/combinations/hardware/document_image/results/vision-activity.json \
  --summary eval/combinations/hardware/document_image/results/vision-activity-summary.json
```

実機での再現には、インストール済みのAppleの公開フレームワークと、端末内で利用できるシステムのAFM 3 Core Advancedを使う。測定時にはこの研究で共通の排他ロックを取得する。出力先には空のディレクトリを指定する。

```sh
image_work=$(mktemp -d)
xcrun clang++ -std=c++17 -O3 -fobjc-arc -fmodules-cache-path="$image_work/ClangModules" \
  -framework Foundation -framework Accelerate -framework Metal -framework MetalPerformanceShaders \
  eval/combinations/hardware/document_image/Filter.mm -o "$image_work/filter"
xcrun swiftc -O -parse-as-library -target arm64-apple-macos27.0 -module-cache-path "$image_work/SwiftModules" \
  eval/combinations/hardware/activity/StreamProbe.swift -o "$image_work/stream"
python3 -B eval/combinations/hardware/document_image/study.py capture \
  --binary "$image_work/filter" --stream "$image_work/stream" --scratch "$image_work" --out "$image_work/results"
python3 -B eval/combinations/hardware/document_image/vision_activity.py capture \
  --filter "$image_work/filter" --scratch "$image_work" --raw "$image_work/vision-activity.json" \
  --summary "$image_work/vision-activity-summary.json"
```
