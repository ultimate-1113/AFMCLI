# TXT・JSON・CSVの処理と注文額の計算

AFMCLI v0.3.2 の `answer` は原文中の短い答えとUTF-8のバイト位置を返し、`summarize` は原文の文を選ぶ。`edit` は指定した旧値や行の版が一致したときに更新案を返す。サンプルはPython標準ライブラリでファイルの読み書きとCSVの列型変換を担当し、更新案を別名で保存する。

| 入力 | 処理 | 出力 |
|---|---|---|
| `notice.txt` | 受取会場を質問し、案内文から2文まで選ぶ。引用位置を原文のバイト列と照合する。 | `result.json` の短答と要約 |
| `before.json` | 定員の旧値が25人のときに30人へ変更する。 | `after.json` |
| `before.csv` | CSVをJSONに変換し、ID `0007`・版3・数量4が一致する行に2個加える。更新した行の版は4になる。 | `csv-input.json` と `after.csv` |
| `order.txt` | 1冊275円のノート18冊と送料320円の注文を、新しいRustの `calc --from-text` に標準入力で渡す。返った式の数値と支払額5,270円を照合する。 | `result.json` の式と計算結果 |

CSVにはカンマ・引用符・改行を含む品名がある。IDは先頭のゼロを含む文字列として、版・数量・単価は整数として読み取る。出力したCSVを読み直し、全行の値と型を検証する。

実機では会場名「白波館」を元のバイト範囲から取り出し、JSONの定員を30に、CSVの品番 `0007` の数量を6、版を4に更新した。JSONとCSVの更新に使ったモデル呼び出しは各0回。文章から注文額を計算する処理では1回呼び出した。[入出力ファイル一式](sample/)・[実行結果](sample/result.json)

リポジトリのルートで、指定したバイナリに合うコマンドを実行する。サンプルは合成の入力を作り、すべての検証が通った後で新規または空の出力フォルダーに結果を置く。モデルを呼んだ回数は処理ごとに `result.json` に記録する。

**v0.3.2でTXT・JSON・CSVを試す**

```sh
python3 examples/file_tasks/demo.py --binary ./target/release/afmcli --out ./demo-files --files-only
```

**`calc` を含むビルドで注文額も計算する**

```sh
python3 examples/file_tasks/demo.py --binary ./target/release/afmcli --out ./demo-with-calc
```

自然文で注文を渡すと、AFMが計算式を提案し、Rustの `calc` が式を検査して計算する。この経路では `status: review` と式の両方が返る。Python側は式に注文の3つの数値が含まれることと、金額が5,270円になることを確認して保存する。この例の検証件数は研究用の正答数と別に扱う。
