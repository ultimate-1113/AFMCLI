# 長文中の撤回・未確定・再確定を読む

同じ質問と背景記録を共有し、決定箇所だけが違う合成文書を2件ずつ用意した。片方には現在の正式な値があり、もう片方にはない。日英の資料で、未承認の提案、撤回、他の対象だけの変更、正式な再決定などを扱った。開発用は12問、初見は48問。各文書にはAppleのトークンカウンターで約4K・8K・16Kに合わせた原文が入る。初見用の原文・正解・根拠位置は、方式の実装担当に見せずに固定した。[評価条件](protocol.md)・[文書の生成と採点](fixtures.py)・[各原文のトークン数](source_tokens.json)

## 比較条件

| 方式 | モデルに渡す情報とホスト側の処理 |
|---|---|
| records | 可逆圧縮を試し、必要なら全文を区間に分けて読む |
| indexed | 旧版の索引で関連箇所を検索し、根拠が足りなければ全文走査に戻る |
| guard | indexedと同じ生成後に、確定・撤回・別属性の明示的な原文と、返した値を照合する |
| checked | 最初に状態と原文の節を選ぶ。別セッションには質問と原資料を渡し、独立して現在値と出典を確認する |
| handoff | 二度目の読解に、最初の回答を圧縮した状態・候補値・引用を追加する |
| review | handoffに加え、判断が食い違った場合に850入力トークン以内の第三確認を最大1回行う |
| staged | 問いの対象と属性を特定し、原文を順次走査する。関係する箇所を残し、決定状態と値を別セッションで解決する |

checkedとhandoffでは、二度目に最初の判断を見せることの影響を同じ文書で比較できる。reviewではさらに、食い違いのある問にだけもう1回かける価値を測れる。独立した確認はCoVe、質問に関係する原文だけを残す設計はRECOMPを参照した。今回の追加読解はすべて同じオンデバイスモデルを使う。[CoVe論文](https://aclanthology.org/2024.findings-acl.212/)・[RECOMP論文](https://arxiv.org/abs/2310.04408)・[RECOMP著者の実装](https://github.com/carriex/recomp)

## 採点と時間

確定した問では正式な値と、その値を確定した箇所のUTF-8バイト位置を採点する。未確定の問では明示的な `answer: null` を正答にする。古い値を返した場合と、未確定の事情を説明する文を値の欄に入れた場合は、どちらも非空値の誤答に含める。処理エラーや予算超過による保留は正答に含めない。正答した問でも、古い同一表記を余分に出典として列挙した場合は別に数える。

実機はMac mini M6、32 GB、モデル配置先は内蔵SSD 256 GB、macOS 27.2.1（26B131）。1回のモデル入力予算は6,200トークン、表示コンテキストは8,192トークン。同時生成は1件、greedy設定で、問ごとに7条件の順を交替した。時間はCLI起動から最終JSONまで。トークン数は再取得・追加確認・全文走査を含む全生成への送信入力の合計である。各条件は各文書1回のため、時間にはそのときの実機負荷も含まれる。

初見と補助試験では、比較対象の実行ファイルを同一の一時ボリュームに複製し、SHA-256を照合してから計測した。初回の開発計測は旧版を外部SSD、候補を一時ディレクトリから起動した。モデルの配置先はどの条件でも内蔵SSDで、ディスクの物理容量の表示は251,000,193,024バイトだった。各試験の時間に初回ロード時間だけを分離した数値は含めていない。

## 再現手順

保存済みの記録はモデルのない環境でも再採点できる。

```sh
python3 -B eval/index_status/bench.py verify --split dev
python3 -B eval/index_status/bench.py verify --split test
python3 -B eval/index_status/bench.py report --split test
python3 -B eval/index_status/targeted_bench.py verify
python3 -B eval/index_status/regressions.py verify
```

次の例は、公開済みの比較コードを別ディレクトリにビルドし、生成結果を新規の出力先へ保存する。事前に `fm models` の `system` が `AFM 3 Core Advanced` と表示されることを確認する。

```sh
RUN=$(mktemp -d)
git worktree add --detach "$RUN/prior" 2f1778cd0d415ad11f2baefab2d8be3f6cfac821
git worktree add --detach "$RUN/candidate" da1114b
cargo build --release --locked --manifest-path "$RUN/prior/Cargo.toml" --target-dir "$RUN/records"
cargo build --release --locked --features experimental-index --manifest-path "$RUN/prior/Cargo.toml" --target-dir "$RUN/index"
cargo build --release --locked --features experimental-index --manifest-path "$RUN/candidate/Cargo.toml" --target-dir "$RUN/new"

for split in dev test; do
  python3 -B eval/index_status/bench.py run --split "$split" --out "$RUN/results" \
    --records "$RUN/records/release/afmcli" --indexed "$RUN/index/release/afmcli" --candidate "$RUN/new/release/afmcli"
done
python3 -B eval/index_status/targeted_bench.py run --out "$RUN/secondary" \
  --records "$RUN/records/release/afmcli" --indexed "$RUN/index/release/afmcli" --candidate "$RUN/new/release/afmcli"
python3 -B eval/index_status/regressions.py run --out "$RUN/results" --candidate "$RUN/new/release/afmcli"
```

途中で中断しても、同じバイナリ・条件・出力先で再開できる。今回実際に使ったバイナリのSHA-256は計測ファイルに記録した。[実行バイナリ一覧](results/binaries.json)・[原文と正解の指紋](lock.json)・[前回から追跡した4つの誤答](prior.md)
