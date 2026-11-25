# 長文中の撤回・未確定・再確定を読む

同じ質問と背景記録を共有し、決定箇所だけが違う合成文書を2件ずつ用意した。片方には現在の正式な値があり、もう片方にはない。日英の資料で、未承認の提案、撤回、他の対象だけの変更、正式な再決定などを扱った。開発用は12問、初見は48問。各文書にはAppleのトークンカウンターで約4K・8K・16Kに合わせた原文が入る。初見用の原文・正解・根拠位置は、方式の実装担当に見せずに固定した。[評価条件](protocol.md)・[文書の生成と採点](fixtures.py)・[各原文のトークン数](source_tokens.json)

## 初見48問の結果

初見48問では、最初の回答を伏せて原文を読み直す `checked` が32問に正答した。旧索引の29問から3問増えた。未確定なのに値を返す誤りは12問から3問に減った一方、確定値と正しい出典を両方返せた問は21問から12問に減った。8K・16Kの合計入力も旧索引より20.6%増えた。速度と入力削減を維持しつつ、この両方の品質条件を満たした方式は今回の候補にはなかった。[事前の採用条件](protocol.md)・[初見の全336記録](results/test.jsonl)・[方式別の集計](results/summary.json)

| 方式 | 全正答 /48 | 確定値と出典が正解 /24 | 未確定を正しく `null` /24 | 未確定に値を返した /24 | 長文32問の総入力トークン | 長文の中央値 | 長文の95パーセンタイル |
|---|---:|---:|---:|---:|---:|---:|---:|
| records（従来の圧縮） | 24 | 15 | 9 | 11 | 681,425 | 14.47秒 | 67.60秒 |
| indexed（旧索引） | 29 | 21 | 8 | 12 | 343,965 | 6.72秒 | 69.42秒 |
| guard（生成後の原文照合） | 29 | 21 | 8 | 12 | 343,965 | 6.74秒 | 69.50秒 |
| checked（独立した再読） | 32 | 12 | 20 | 3 | 414,825 | 5.14秒 | 71.77秒 |
| handoff（圧縮した初回回答を添付） | 25 | 10 | 15 | 8 | 471,093 | 5.29秒 | 71.47秒 |
| review（食い違い時に第三確認を試行） | 25 | 10 | 15 | 8 | 471,093 | 5.28秒 | 71.38秒 |
| staged（段階別の読解） | 21 | 16 | 5 | 16 | 639,400 | 11.15秒 | 15.92秒 |

95パーセンタイルを含む時間は8K・16K各16問の完了時間で、保留や実行エラーで終わった試行も含む。確定・未確定の品質欄は4Kを含む全48問で採点した。未確定の欄が計24問に満たない方式では、残りの問が保留・拒否・実行エラーのいずれかで終了した。1回の送信入力の最大値は全方式で6,157トークンだった。[長さ別・終了状態別の内訳](results/summary.json)

**文書長によって速度の評価は変わる。** 8Kでは旧索引が6.99秒、独立した再読が5.10秒だった。16Kでは同じ順に2.43秒、19.81秒だった。16Kの最終回答まで索引の抜粋を使えたのは旧索引が12/16問、独立した再読が7/16問だった。独立した再読でも、従来の圧縮から旧索引への入力削減幅の79%は維持した。後処理だけを加えたguardは全48問で旧索引と同じ生成履歴・呼び出し回数になり、誤答も同じだった。[長さ別の処理経路と送信量](results/summary.json)

**撤回が書かれた3組では、初回回答を引き継ぐと古い値が残った。** 撤回後も決定していない3問のうち、独立した再読は2問で正しく `null` を返した。旧索引と引き継ぎ付きはいずれも3問すべてで値を返した。例えば霧樽の商品札では、産地表示の了承印が外され、差し替え原稿も未着だった。旧索引は「羽見野」、独立した再読は `null`、引き継ぎ付きは「羽見野の版」と答えた。対になる確定3問の正答は旧索引が3問、独立した再読と引き継ぎ付きが各1問だった。[合成資料の定義](fixtures/)・[全回答と引用位置](results/test.jsonl)

初見全体では、引き継ぎ付きだけが正解したのは1問、独立した再読だけが正解したのは8問だった。再取得を含む29回の引き継ぎで、初回出力計7,025バイトを5,221バイトに縮めた。引き継いだ初回判断が誤っていた10回のうち、2回は二度目で正され、8回は同じ誤りを繰り返した。正しかった初回判断が二度目で崩れた例も6回あった。第三確認の条件に合ったのは5回で、いずれも送信前に `unavailable_or_budget` と記録された。実際の第三呼び出しは0回で、3回目の読解で判断が変わるかは分からなかった。[初回判断・引き継ぎ・追加確認の記録](results/summary.json)

段階別の読解では長文32問で497回モデルを呼んだ。旧索引は106回だった。原文から根拠を選び、状態と値を順番に求めても、未確定24問のうち16問で値を返した。例えば夕立太鼓の収蔵番号を尋ねた問で、採譜帖の対象は凪太鼓だったが、その番号「ヨ23号」を夕立太鼓の答えに使った。1台のMacで各問・各方式を1回ずつ実行した合成資料の結果であり、特に分野の異なる実資料での頻度は別途測る必要がある。[原文と質問の再現方法](#再現手順)・[各方式の呼び出し履歴](results/test.jsonl)

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

初見試験の一部と並行して、同じMacで電卓のリリース用ビルドとローカルテストを行った。モデルによる生成は直列に保った。時間の小さな差にはこの背景負荷も影響しうる。

## 再現手順

保存済みの記録はモデルのない環境でも再採点できる。

```sh
python3 -B eval/index_status/bench.py verify --split dev
python3 -B eval/index_status/bench.py verify --split test
python3 -B eval/index_status/bench.py report --split test
python3 -B eval/index_status/targeted_bench.py verify
python3 -B eval/index_status/regressions.py verify
```

1問だけ結果を確かめるには、次の例で日本語資料と質問を取り出す。出力されるJSONには正解・判定ラベル・根拠位置を含めない。研究用ビルドの `--compact=index-checked` で原文を独立に読み直せる。同じ入力を `--compact=index-handoff` に渡すと、最初の判断と根拠を圧縮して次のセッションに添える。

```sh
cargo build --release --locked --features experimental-index
python3 -B - <<'PY' | target/release/afmcli --compact=index-checked --trace --input-budget 6200 --timeout 45 run
import json
import sys
sys.path.insert(0, "eval/index_status")
import fixtures
case = next(c for c in fixtures.cases("test") if c["id"] == "index-test-16k-08-n")
print(json.dumps(case["job"], ensure_ascii=False))
PY
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
