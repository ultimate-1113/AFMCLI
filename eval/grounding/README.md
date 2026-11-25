# 変更通知と数値ログの根拠を確認する

Mac mini M6（32 GB、モデルの配置先は内蔵SSD 256 GB）、macOS 27.2.1（26B131）で、AFM 3 Core Advancedのオンデバイス経路 `system` を使った。生成はgreedy、1プロセスにつき45秒まで、同時実行1件。各条件・各問を1回ずつ実行した。中央値は問題間で計算している。

## 何を変えたか

点検番号や測定値だけが変わる記録では、半角数字以外の部分が同じ行が4行以上続くとき、共通する文字列を1回記載し、各行の数字を元の順で保存する。1行でまとめる数字列は最大16個。ゼロ埋めの番号、符号、小数点、空白、改行を含めて原文を復元できる。質問の数字と同じ数字列を含む行が1〜8件なら、その行は原文のまま見せる。モデルへ送る前に実際にトークンを数え、入力予算を超える資料は従来の方式で読む。採用した場合の記録は `metrics.context.method = numbered_rows` となる。

圧縮や根拠選択が必要な長文で各方式の違いが現れる。一度で読める資料と、全区間を順に読む処理に戻った資料には、通常のQAを使う。

根拠照合では、初回の回答を次のモデル呼び出しに渡さず、同じ資料から対象・項目・時点を読み取り、値、値の根拠、変更を示す一節を引用させる。最初の回答候補と照合し、引用は元のUTF-8バイト列で検証する。変更の引用があれば、引用箇所を原文の順序で比べ、最後の一節だけをもう一度読んで、変更の適用先を判定する。モデルが根拠を選び損ねれば、正解まで回答を控えることがある。

候補を隠す操作と、同じ問いへの二つの回答を比べる考え方はCoVeとQAGSを参照した。別主体の取り違えと、矛盾する資料での棄権を分けた試験はAmbigDocsとRagabilityを参照した。以下はこのMacでの測定である。[CoVe](https://aclanthology.org/2024.findings-acl.212/)・[QAGS](https://aclanthology.org/2020.acl-main.450/)・[AmbigDocs](https://ambigdocs.github.io/)・[Ragability](https://aclanthology.org/2026.lrec-1.182/)

## 採点と入力条件

開発には既出の26問を使った。初見30問では15組の資料を作り、各組の末尾近くの告知だけを変更した。各組は回答できる1問と、回答を控えるべき1問を含む。14問が日本語、16問が英語。数値検索8問は日英各1資料に4つずつ質問した。合成資料の本文と質問だけをAFMに渡した。[初見資料の条件](holdout.json)・[事前に固定した採点基準](protocol.md)・[数値検索の基準](numeric.md)

ハーネスの正答には原文の値・引用位置の一致を求める。数値検索では正解行を指す引用を少なくとも1つ求める。直接回答では指定されたJSON形式と値を採点する。明示的な空回答は評価するが、入力予算超過、モデルによる拒否、無効な出力は正解に含めない。送信トークン数は生成・確認・再試行の入力推定値の合計で、計数のみの呼び出しは含まない。最大入力は1回のモデル呼び出しに実際に送った最大値である。

直接回答は同じ全文を標準の入力予算6,200トークンで送る。ハーネスは初見30問のうち4問で6,200、残る26問で1,300〜1,450、数値検索は全問1,300の予算を使う。小さい予算を設定した試験と標準予算の試験を含むため、全体の値はこの問題構成での実測である。

## 実測結果

固定した各実装で測定した値を表に示す。初見は変更通知15組30問。件数は「確定値のある15問で正しく回答」＋「確定値がない15問で正しく棄権」の合計である。直接回答で入力が6,200を超えた8問はモデルに送れなかった。直接回答の中央値と送信量にはその条件差がある。[初見の質問別記録](holdout.jsonl)

| 初見30問 | 正解 | 未確定なのに回答 | 両方正解した組 | 送信入力の合計 | 最大入力／回 | 完了時間の中央値 |
|---|---:|---:|---:|---:|---:|---:|
| 直接回答 | 2/30（2＋0） | 11/15 | 0/15 | 62,691 | 6,144 | 0.85秒 |
| 従来の `--compact=evidence` | 24/30（13＋11） | 4/15 | 10/15 | 103,587 | 6,111 | 2.16秒 |
| `--compact=records` | 24/30（13＋11） | 4/15 | 11/15 | 68,749 | 2,690 | 1.31秒 |
| `--compact=review` | 23/30（9＋14） | 1/15 | 8/15 | 75,298 | 2,733 | 1.96秒 |

数値行の圧縮を使えた初見10問に限ると、従来方式と `records` はともに8問正解で、合計入力は66,674から31,836に減った。30問全体で合否が入れ替わった2問はいずれも撤回の扱いだった。ハープ教室の予約取り消しでは誤答が直った。木立館の見学日については、金曜日とする案が未承認のままでも「金曜日」と答えた。

| 資料 | 従来方式 | `records` | `review` |
|---|---:|---:|---:|
| 開発26問の正答数 | 22/26 | 21/26 | 25/26 |
| 開発26問の合計入力 | 54,140 | 38,333 | 41,289 |
| 数値検索8問の正答数 | 8/8 | 8/8 | 7/8 |
| 数値検索8問の合計入力 | 28,330 | 14,454 | 14,745 |
| 数値検索8問の完了時間の中央値 | 2.40秒 | 1.34秒 | 1.60秒 |

数値検索では、質問された行を元の表記で残すことで、測定値と検査番号の対応を確認できる。直接回答は5/8だった。`review` が落とした1問は「4.9グラム」から単位を落として「4.9」と答えた。開発26問の `review` は正解が増えた一方、完了時間の中央値は従来の1.09秒から1.41秒に延びた。[数値検索の出力と引用位置](numeric.jsonl)・[開発26問の出力](dev.jsonl)

## 根拠照合の採否

既出のロッカーの問題では、最初の候補が旧コード、再読の引用が撤回を示した。最後に撤回通知だけを判定し、旧コードを返さずに済んだ。一度取り消したコードを再承認した対照にも正答した。取り消しが別のロッカーだけの対照では、有効な元のコードまで回答を控えた。初見資料では、返却期限を廃止し次の決定を持ち越した後も、旧期限「11月9日」を返した例が残った。[同じロッカー記録の3条件](controls.jsonl)・[初見のケース別出力](holdout.jsonl)

初見30問では独立照合が標準の確認処理を置き換える採用条件に届かなかった。このため `records` は従来の確認方法で数値行を圧縮し、`review` を独立照合まで行う選択肢とした。確定した値をなるべく多く返したい場合と、未確定の値を返す誤りを減らしたい場合で使い分けられる。

## 公開版での再確認

v0.3.2の同一実行ファイルで4つの指定を再実行した。`--compact` は26件、`--compact=evidence` は67件、`--compact=records` は64件、`--compact=review` は67件。計224件で各方式の比較元と回答・原文の引用位置・ステータス・呼び出し回数・送信トークン数が一致した。このときの初見30問の完了時間の中央値は順に `evidence` 2.23秒、`records` 1.40秒、`review` 2.10秒だった。実行ファイルのSHA-256は `33a86bdedebe8486322853f8ba62f060b46b41e62bb3a299430fe3b07fb12910`。[公開版の224件の記録](public.jsonl)

既存の短文40問では、`--compact`、`records`、`review` の3方式とも26問正解し、すべての問で旧版と合否・ステータスが一致した。通常のファイル入力では検体番号047について「2.0グラム」と原文の位置を返した。バッチ入力では同じロッカーのコードについて、撤回した資料で回答を控え、再承認した資料で `FERN-29` を返した。[短文40問の従来方式](short.jsonl)・[数値行圧縮](short-records.jsonl)・[独立照合](short-review.jsonl)

## 再現手順

リポジトリのルートでビルドし、同梱の合成資料を標準入力から渡す。

```sh
cargo build --release --locked
python3 -B eval/grounding/study.py show weight-middle |
  target/release/afmcli --compact=records --input-budget 1300 run
python3 -B eval/grounding/study.py show original |
  target/release/afmcli --compact=review --input-budget 1200 --trace run
```

実装を固定してから初見資料を実行した。旧版は `ba4954f`、数値行圧縮を追加した比較用の版は `27fb586`、独立照合を追加した比較用の版は `dfbdece`。これら3版の元のフラグはすべて `--compact=evidence`。公開版ではそれぞれ `--compact=evidence`、`--compact=records`、`--compact=review` に対応する。試験時のSHA-256は質問別記録の `binary` 欄に保存した。以下はLinuxでも実行できる保存結果の再採点である。

```sh
python3 -B eval/grounding/study.py verify
python3 -B eval/grounding/routes.py verify
python3 -B eval/repeats/check_short.py verify --results eval/grounding/short.jsonl
python3 -B eval/repeats/check_short.py verify --route records --results eval/grounding/short-records.jsonl
python3 -B eval/repeats/check_short.py verify --route review --results eval/grounding/short-review.jsonl
```

Appleのモデルが使えるMacでは、上記の3コミットを別々の作業フォルダでビルドして、質問別の測定を再実行できる。出力には新しいファイルを指定する。

```sh
git worktree add --detach ../afmcli-before ba4954f
git worktree add --detach ../afmcli-packing 27fb586
git worktree add --detach ../afmcli-review dfbdece
for path in ../afmcli-before ../afmcli-packing ../afmcli-review; do
  cargo build --manifest-path "$path/Cargo.toml" --release --locked
done
for split in dev holdout numeric controls; do
  python3 -B eval/grounding/study.py run --split "$split" \
    --before ../afmcli-before/target/release/afmcli \
    --packing ../afmcli-packing/target/release/afmcli \
    --after ../afmcli-review/target/release/afmcli \
    --output "eval/work/grounding-$split.jsonl"
done
python3 -B eval/grounding/routes.py run --binary target/release/afmcli --output eval/work/grounding-public.jsonl
```

短文40問を実行する場合は、固定した版の公開データを取得して、各方式を同じ質問で評価する。

```sh
python3 -B eval/external.py eval/work/data
for route in repeats records review; do
  python3 -B eval/repeats/check_short.py run --route "$route" \
    --binary target/release/afmcli --data eval/work/data \
    --results "eval/work/short-$route.jsonl"
done
```
