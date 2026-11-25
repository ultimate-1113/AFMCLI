# 周期的な通知の圧縮と、撤回済みの回答の検証

AFMCLI v0.3.1の `--compact` は、同じ並びの通知が何度も出る長い記録を短くできる。まずv0.3.0と同じ1文単位の辞書を試す。その表現が使えない場合に、2〜8文の同一ブロックが3回以上続く箇所を探す。モデルにはブロックの原文、出現順、回数を渡す。途中に入った変更通知、区切りの空白、末尾の改行も復元できる。採用した場合は `metrics.context.method` が `repeat_blocks` になる。辞書を使わず全区間を読む場合も、予算が一致すれば最初に測定した区切りを再利用する。

## v0.3.0との実機比較

| 資料 | 完全一致・正しい棄権 | 送信した入力の合計 | トークンを計数した回数 | 完了時間の中央値 |
|---|---:|---:|---:|---:|
| 周期的な通知を含む新規6問 | 0/6 → 4/6 | 32,861 → 5,543 | 205 → 68 | 3.14秒 → 1.02秒 |
| 既存の合成20問 | 14/20 → 14/20 | 36,784 → 36,784 | 263 → 185 | 1.46秒 → 1.25秒 |
| 上の20問のうち、全文を区間に分けた11問 | 5/11 → 5/11 | 30,115 → 30,115 | 197 → 119 | 1.77秒 → 1.51秒 |

新規6問には2・3・4・5・6文周期の記録を用意し、途中で話題、対象、確定事項が変わる通知を挿入した。全6問で旧版は資料を分けて読み、新版はブロックで圧縮できた。新版は会場・窓口の3問に正答し、出発日が未確定の1問で正しく回答を控えた。生成回数の合計は21回から11回に減った。1,800文の記録では、旧版が保留した最終会場 `Amber Hall` を原文の位置付きで返し、入力合計は12,387から843トークンになった。[新規の入力条件と正解](panel.json)・[52試行の結果](results.jsonl)

残る2問は読み取りと最終確認を個別に記録した。梅組の受取日については、日付だけを求めた質問に「梅組の新しい受取日は6月18日です。」と一文で答えた。成人グループの会場には正解と完全一致する `Coral Studio` を返した。最終確認はどちらにも `supported: false` を返し、利用者には回答が届かなかった。同じ実行ファイルの `answer` コマンドで上の1,800文も再試験し、`Amber Hall` のUTF-8バイト位置を原文と照合した。[3件の回答候補と判定](candidate-traces.json)

既存の合成20問では回答、引用位置、状態、生成回数、送信トークンが各問で一致した。全文を区間に分けた11問では同じ入力に対するトークン計数を1問につき6〜9回削減した。既存の日本語・英語資料QA40問も新版で再測定し、26/40正解、旧版との合否・状態の変動は0件だった。[公開QAの再測定](short.jsonl)

Mac mini M6、メモリ32 GB、モデルの配置先は内蔵SSD 256 GB、macOS 27.2.1（26B131）。オンデバイスのAFM 3 Core Advancedをgreedyで呼び出し、各条件を1回ずつ、同時実行1件で計測した。新規6問の1問は標準の6,200トークン、残る5問は1,100〜1,250トークンの入力予算を設定した。表の入力は確認・再試行を含む送信トークンの推定合計。時間はCLIの起動から終了までを測り、各群の問題間で中央値を取った。試験に使ったv0.3.1のSHA-256は `130219774035301d610328021548abe4a9a1a123e95bfddc20fcda894717cbb0`。

## 撤回の判定で残った問題

v0.3.0の長文QAで、模擬ロッカーのコードを撤回するケースを再実行した。3番の文には当初のコード、58番には撤回、59番には後継が未発行とある。選択器は3番と58番を選び、前後を含む6文が読解と最終確認に渡った。AFMは旧コードを返し、最終確認も `supported: true` と判定した。撤回を別のロッカーに変更した対照では旧コードが正答になった。再承認した対照でも最終回答は正しかったが、選択器は再承認の文を取りこぼした。[3例の選択文と判定](verifier-probe.json)

候補が現在も有効かを見る確認方法も比較した。日英各6例に、有効な候補6件と無効な候補6件を用意し、候補と根拠を両方式で共通にした。現在の真偽判定は10/12正解。各抜粋を「確定・無効化・影響なし」に分類してコードで順に反映する試作は9/12正解で、別対象への撤回を混同した1件、モデル側の拒否1件、分類数が抜粋数と合わない1件を落とした。上記の長文誤答から抽出した2区間も別に再試験すると、試作は3件の分類を返し、判定できなかった。v0.3.1は従来の真偽判定を使用する。[ケース・入力指示・両方式の出力](verifier-probe.json)

## 再現手順

リポジトリのルートで以下を実行する。`--before` は実測したv0.3.0、`--after` はv0.3.1の実行ファイル。再実行結果には新しい出力先を指定する。

```sh
cargo build --release --locked
python3 -B eval/repeats/bench.py show --case pattern-default | target/release/afmcli --compact run
python3 -B eval/repeats/bench.py verify
python3 -B eval/repeats/check_short.py verify
python3 -B eval/repeats/verifier_probe.py verify
python3 -B eval/repeats/bench.py run --before ./afmcli-v0.3.0 --after target/release/afmcli --results eval/work/repeats-new.jsonl
python3 -B eval/external.py eval/work/data
python3 -B eval/repeats/check_short.py run --binary target/release/afmcli --data eval/work/data --results eval/work/short-new.jsonl
python3 -B eval/repeats/verifier_probe.py run --binary target/release/afmcli --output eval/work/verifier-new.json
```

成人グループの会場を読み取った候補と最終確認の両方を見るには、計測時と同じ入力予算で `--trace` を付ける。

```sh
python3 -B eval/repeats/bench.py show --case pattern-scope |
  target/release/afmcli --compact --input-budget 1150 --trace run
```
