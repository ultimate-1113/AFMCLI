# 長文・文章題のハーネスとGPU・ANEの実機比較

Mac mini M6で、AFM 3 Core Advancedのテキスト生成、長文の索引、関数電卓、帳票画像を調べた。長文24問と文章題16問をそれぞれ7方式で解き、合計280試行の入力と結果を再生した。稼働率と並行処理は品質試験と別に測定した。

## AFMの生成中に動かせる処理

- **AFM単独のテキスト生成。** 2,648トークンの入力から同じ390トークンの文章を3回生成した。GPU使用率は要求中の85標本すべてで0%表示。明示GPU検索単独では中央値76%、AFMとの重複区間は73%だった。AFM単独の平均CPU使用率は全コア合計を100%として6.93%。このM6で読めるANEデバイスの項目から演算使用率は取得できなかった。[測定区間・結果・10秒で確認する方法](hardware/activity/README.md)
- **次の文書の先読み。** 軽い索引を毎回作る場合、次の質問の検索だけを今のAFMの生成に重ねると、4問の完了は2巡とも約5〜6%早くなった。すでに索引を保持していた場合、原文を選ぶ時間は約48 msから約0.5 msになり、4問の生成に重ねられた検索時間は合計1.43〜1.75 msだった。回答と根拠はどの方式でも一致した。[先読み・常駐索引の実測表](hardware/README.md)
- **多数の質問に対する検索。** 4,096行×1,024特徴×1,024質問で同じ480バッチを続けて処理すると、CPUのAccelerateは2.222秒、明示GPUのMetalは1.236秒。GPUとAFMを並行させた7巡では、同じ巡で別々に実測した所要時間の和より全体の中央値比が31%短く、AFMの回答は約4%遅くなった。4件の情報を返す課題の答えは全条件で同じで、先頭2件だけが正しかった。1バッチずつCPU・GPUを交替する試験で、GPUが10%以上速い形状は6サイズ中0件だった。[全要素の照合・7巡の実測・補助検索の配分](hardware/batched/README.md)
- **ANE優先の検索。** 同じ候補文書を重みとする固定のCore ML索引を作った。1問ずつの検索は4資料ともCPU優先。4,096行・1,024質問では畳み込みがANE優先になり、全得点をホストへ戻すまでがCPU限定2.213 ms、ANE優先0.854 msだった。初回ロードはそれぞれ23.97 msと133.80 ms。2,048行・128質問ではANE優先でもCPU限定のほうが速かった。優先デバイスは実行計画で確認し、照合には整数演算を使った。[演算別の計画・精度とコスト](hardware/placement/README.md)
- **GPUとANE優先のCore MLへの分担。** 別の7巡で同じ480バッチの検索を比較した。検索単独の中央値はGPUだけで1.237秒、Core MLだけで1.567秒、240件ずつの分担で0.842秒。AFMを同時に動かして両方が終わるまでは順に2.523秒、2.549秒、2.573秒だった。同じ試験のAFM単独は2.425秒。分担時はAFMの実行中に、Metalが記録したGPU実行区間とCore MLの予測呼び出しが中央値100.5 ms重なった。Core ML側で取得できたデバイス情報は畳み込みの優先先までである。[分担の集計](hardware/batched/results/split-summary.json)・[出力一致と時刻の検証手順](hardware/batched/README.md)
- **帳票の画像処理とAFMの画像入力。** 3サイズのノイズ除去はCPUのvImageが速く、最大の4,096×6,144画素ではCPU 2.402 ms、GPU 12.925 ms。72回すべてで独立参照と全画素が一致した。AFMは元画像と両処理後の画像から番号42を読めた。同じ文面で画像を添付して説明させた別試験でも、小画像43標本、大画像51標本のGPU表示はすべて0%だった。[画像試験と観測の粒度](hardware/document_image/README.md)

現行の `fm` とFoundation Modelsの公開インターフェースでは、システムモデルの層をGPUとANEに割り振る設定を確認できなかった。アプリ側はMetalで自分の計算をGPUに送り、自作のCore ML索引は使用可能なデバイスを設定できる。同じCore Advancedを搭載したM3 Maxには入力処理時と生成時のANE活動を報告した実機資料がある。今回のM6でAFMに帰属するANE使用率は未測定である。[Appleのシステムモデル](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel)・[Core MLのデバイス選択](https://developer.apple.com/documentation/coreml/mlcomputeunits/cpuandneuralengine)・[Julien SimonによるM3 Maxの計測](https://github.com/juliensimon/apple-fm-mac/blob/main/REPORT.md#111-power-the-neural-engine-runs-the-model-m)

## 長文の未確定事項

確定12問・未確定12問を、それぞれ8Kと16Kの文書で試した。確定値を返す場合は正しい値と原文上のUTF-8範囲の両方が必要になる。未確定での明示的な棄権は正答、処理保留・モデルエラーは別に集計して失点とした。「誤って値」は未確定の12問が分母。総入力は24問全体の送信入力の集計で、追加の再読と再試行を含む。

| 方式 | 正答 /24 | 確定正答 /12 | 不明正答 /12 | 誤って値 /12 | 保留＋エラー /24 | 総入力トークン | 完了中央値 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 旧索引 `indexed` | 11 | 10 | 1 | 10 | 2 | 168,399 | 2.64秒 |
| 旧索引＋独立再読 `checked` | 12 | 5 | 7 | 2 | 6 | 428,098 | 25.05秒 |
| 短い抜粋を1回読む `packet` | 9 | 9 | 0 | 9 | 6 | 20,248 | 0.97秒 |
| 初回の出典位置で抜粋を選ぶ `anchored` | 9 | 9 | 0 | 9 | 6 | 187,130 | 3.70秒 |
| 必要時だけ撤回を確認 `selective` | 11 | 10 | 1 | 10 | 2 | 170,102 | 2.73秒 |
| 独立2方式の照合 `independent` | 9 | 9 | 0 | 8 | 7 | 188,647 | 3.59秒 |
| 不一致時に第3セッション `arbitrate` | 9 | 9 | 0 | 9 | 6 | 188,914 | 3.66秒 |

速度と入力を維持して未確定への誤った値を減らす5つの事前条件を、すべて通った方式はなかった。選択的な確認は2問で作動したが、採点は旧索引と24問すべてで同じだった。抜粋だけの1回読解は総入力を88.0%、完了中央値を63.3%減らした。確定問は9/12で、未確定問は誤った値9件、保留3件だった。独立再読では誤った値が10件から2件に減った一方、確定問の正答は10件から5件になり、完了中央値は約9.5倍になった。[問ごとの勝敗・判定条件](results/summary.json)

夜間用カバーの素材を聞く `qa-fern-m` では原文に候補の `sailcloth` が書かれているが、その素材を実際に縫いつけた記録は欠けている。7方式のうち正しく棄権できたのは独立再読だけだった。新作24問で第3セッションが呼ばれたのは `qa-wrack-m` の1件で、独立照合の保留から未確定の値「白い貝の札」を返す誤りに変わった。短い出典を再提示しても、記載された候補を今も有効な値として採用する誤りは残った。[長文の生記録](results/qa.jsonl)

第3セッションに達する経路は、最初の2判定を意図的に食い違わせた別の2例でも確認した。実機へ送った入力は248・256トークン。英語の変更では新しい岸壁を選び、日本語の撤回では古い岸壁を選んだ。この2例は上表の24問には含めていない。[分岐確認の記録](results/review-control.jsonl)

## 文章題と関数電卓

新作の日英16問は、必要な数量がそろう8問と、一つの条件を変えて数値が決まらなくなる8問。後者は明示的な「不明」で正答とした。数値を返さず処理を保留した場合は失点。ここでの正答率には、文章を解釈して適切な数式を作る能力も含まれる。

| 方式 | 正答 /16 | 数値が決まる問の正答 /8 | 不明の正答 /8 | 不明への誤った数値 /8 | 保留 /16 | 完了中央値 |
|---|---:|---:|---:|---:|---:|---:|
| Swift・電卓なし | 4 | 1 | 3 | 5 | 0 | 199 ms |
| Swift・電卓を登録して最終文を採用 | 1 | 0 | 1 | 5 | 4 | 683 ms |
| 最後の電卓コールバックを優先 | 0 | 0 | 0 | 8 | 0 | 688 ms |
| AFMが式を出しRustが計算 | 3 | 3 | 0 | 8 | 1 | 507 ms |
| 数量と関係の根拠を抽出してから式を作る | 0 | 0 | 0 | 0 | 16 | 1,210 ms |
| コールバックと根拠抽出を独立に照合 | 0 | 0 | 0 | 0 | 16 | 1,977 ms |
| コールバックと最終文の不一致時などに照合 | 0 | 0 | 0 | 5 | 7 | 963 ms |

色紙の問題では、全体の2/3にあたる青い部分の3/8を銀にし、その半分を青に戻す。式を作ってRustに渡す方法は正答12.5%を返した。電卓を登録したモデルの答えは0.375で、最後のコールバックも0.375だった。受け取った束の枚数が不明な別の問題では、最後のコールバックの7をそのまま答えると誤答になる。[全試行](results/calc.jsonl)・[問題と正解の生成](panels.py)

数量と関係の逐語引用を必須にすると、16問すべてを保留した。原文に対応しない引用や数値の表現をホストが拒否しており、数値の捏造を防いだ件数だけを数えると、この方式の実用性を過大評価する。事前条件の「確定問の正答を維持し、不明への数値を減らし、合計正答を増やす」を満たした方法は0件。数式が確定している入力には既存の `afmcli calc` を直接使える。[Rust電卓の仕様とテスト](../calculator/README.md)

## 実機と再現手順

Mac mini M6、32 GB、内蔵SSD 256 GB、macOS 27.2.1（26B131）。評価用のプログラムと記録は別のSSDに置いた。AFMの入力上限表示は8,192トークン。長文の原文は8K群が7,972〜8,187、16K群が16,110〜16,350トークンで、各値はAppleのカウンターで計測した。方式と問題は計測前に固定した。品質試験は各ケース・各方式1回、AFMの同時要求は1件。完全に一致する原文の根拠位置も採点した。[事前条件](protocol.md)・[問題と実装の指紋](lock.json)・[使用した実行ファイルの指紋](results/manifest.json)

品質試験は長文の68試行目を保存した境目でデバイスをハードウェア試験に譲った。再開時には保存済み180試行を検算し、中断時に開始した次の試行をやり直した。以降は各結果を保存した後でロックを譲り、品質の計時とハードウェア試験の計時を直列に保った。同じスケジュールで実行するには `study.py run` を `queued_run.py run` に置き換える。時間にはCLI、トークン計数、各段の処理、実機で起きたタイムアウトが含まれる。初回ロード、ビルドや他アプリによる通常の背景負荷は分離していない。[試行間のロック制御](queued_run.py)

リポジトリのルートで以下を実行すると、Appleのモデルやアカウントなしで、全280試行の入力指紋・分岐・答え・採点を再現できる。追加の2件で第3セッションの経路も検証できる。

```sh
python3 -B -m unittest discover -s eval/combinations
python3 -B eval/combinations/study.py verify
python3 -B eval/combinations/study.py report
python3 -B eval/combinations/review_control.py replay --out eval/combinations/results/review-control.jsonl
```

任意の1問の入力は次のコマンドで取り出せる。`--observed` を付けると、保存済みの該当方式について、モデルに送った内容の指紋、回答、使った式、採点を確認できる。引数の選択肢は `--help` で確認できる。

```sh
python3 -B eval/combinations/inspect_case.py qa-fern-p > /tmp/afm-qa-fern-input.json
python3 -B eval/combinations/inspect_case.py qa-fern-p --observed anchored
python3 -B eval/combinations/inspect_case.py calc-stencil-p --observed formula
```

新たに全方式をライブ計測する場合は、`fm models` の `system` がCore Advancedであることを確認し、新しい作業先を作る。既存の出力先を指定した場合、ランナーは保存済みの先頭からすべてを再生してから続きを実行する。元の実行に使ったバイナリと条件の一致が必要になる。

```sh
RUN=$(mktemp -d -t afm-combinations)
git worktree add --detach "$RUN/indexed-src" 2f1778cd0d415ad11f2baefab2d8be3f6cfac821
git worktree add --detach "$RUN/checked-src" da1114b9b2ed63c1c52526dc9db0aa398f5cb945
git worktree add --detach "$RUN/calculator-src" v0.4.0
cargo build --release --locked --features experimental-index --manifest-path "$RUN/indexed-src/Cargo.toml" --target-dir "$RUN/indexed"
cargo build --release --locked --features experimental-index --manifest-path "$RUN/checked-src/Cargo.toml" --target-dir "$RUN/checked"
cargo build --release --locked --manifest-path "$RUN/calculator-src/Cargo.toml" --target-dir "$RUN/calc"
xcrun swiftc -parse-as-library -swift-version 6 -O -target arm64-apple-macosx27.0 \
  eval/calculator/native/CalculatorProbe.swift -o "$RUN/calculator-probe"
python3 -B eval/combinations/study.py run --out "$RUN/results" \
  --indexed "$RUN/indexed/release/afmcli" --checked "$RUN/checked/release/afmcli" \
  --native "$RUN/calculator-probe" --calculator "$RUN/calc/release/afmcli"
python3 -B eval/combinations/study.py verify --out "$RUN/results"
python3 -B eval/combinations/study.py report --out "$RUN/results"
```

`results/qa.jsonl` と `results/calc.jsonl` は方式間で出力を使い回さずに得た生の記録である。一つの方式内の独立判定や第3セッションも、モデル呼び出しはすべて順番に行った。主表と異なる機材・OS、自然文の実利用、PCCに対する性能はこの試験の対象外である。[長文の生記録](results/qa.jsonl)・[文章題の生記録](results/calc.jsonl)・[言語別・長さ別・問ごとの比較](results/summary.json)
