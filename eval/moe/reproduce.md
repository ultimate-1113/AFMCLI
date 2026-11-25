# 実測の再現

オフラインでケース・判定・モデルに渡した入力・呼び出し回数を検証するには、リポジトリのルートで以下を実行する。結果のJSONLには試行ごとのモデル出力、通常コードの判定と処理時間が入っている。別のMacで再生成するときは結果用に別のディレクトリを指定する。

```sh
python3 -B eval/moe/general_checks.py
python3 -B eval/moe/special_check.py
python3 -B eval/moe/study.py verify
python3 -B eval/moe/study.py report
python3 -B eval/moe/regression.py verify
```

手元の文書で索引方式を試す場合は、研究用のCargo機能を有効にする。次の例は原文と同じ言語で具体的な項目を尋ねる。

```sh
cargo run --release --locked --features experimental-index -- --compact=indexed answer '最終的に承認された会場はどこですか？' < minutes.txt
```

## Macで再計測する

Appleの `fm models` に利用可能な `system (AFM 3 Core Advanced)` が表示されるMacを使う。Rust 1.89以降とPython 3.9以降が必要。一般・専門試験では全条件で同じv0.3.2バイナリを使う。全文検索の試験は、現行2条件にv0.3.2、索引2条件に凍結した研究用コードを使う。以前のコミットを独立した作業ディレクトリに展開する。

```sh
LAB=$(mktemp -d)
git worktree add --detach "$LAB/baseline" 37861465faf1364e6b2c677e0ca030bbbf3eac5a
git worktree add --detach "$LAB/index-study" 99af598
cargo build --release --locked --manifest-path "$LAB/baseline/Cargo.toml"
cargo build --release --locked --manifest-path "$LAB/index-study/Cargo.toml"
BASE="$LAB/baseline/target/release/afmcli"
INDEX="$LAB/index-study/target/release/afmcli"
OUT="$LAB/measurements"
"$BASE" doctor
"$INDEX" doctor
```

各組の開発試験を先に行う。長文の30件はあらかじめAppleのトークナイザーで長さを校正しており、実測に使った繰り返し行数と元文書のトークン数も同梱している。比較指標のひとつである送信入力の合計には、回答・確認・再取得それぞれの指示とスキーマを含める。CLIのトークン数は公式のカウンターによる推定値である。

```sh
for split in dev test; do
  python3 -B eval/moe/study.py run --track general --split "$split" --baseline "$BASE" --binary "$BASE" --output "$OUT"
  python3 -B eval/moe/study.py run --track context --split "$split" --baseline "$BASE" --binary "$INDEX" --output "$OUT"
  python3 -B eval/moe/study.py run --track special --split "$split" --baseline "$BASE" --binary "$BASE" --output "$OUT"
done
python3 -B eval/moe/study.py winner --output "$OUT"
python3 -B eval/moe/study.py verify --output "$OUT"
python3 -B eval/moe/study.py report --output "$OUT"
```

同じコマンドの再実行で、完了済みのケースを照合して次の条件から続行する。比較条件の順はケースIDと方式名のハッシュから決める。モデルの呼び出しは直列で、ヒントの登録には毎回新しい一時ディレクトリを使う。専門分野は開発全件の応答を再生・再採点した後、選ばれた1分野だけを初見評価する。

旧試験の短文40件は以前の外部データを取得してから再実行する。長文30件と数値8件は同梱資料から復元できる。`FINAL` には互換性を検証する通常版の実行ファイルを指定する。

```sh
python3 -B eval/external.py "$LAB/old-data"
python3 -B eval/repeats/check_short.py run --route records --binary "$FINAL" --data "$LAB/old-data" --results "$OUT/regression-short.jsonl"
python3 -B eval/moe/regression.py run --binary "$FINAL" --output "$OUT/regression-long.jsonl"
```
