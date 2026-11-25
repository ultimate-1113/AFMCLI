# AFMCLI

Rustで実装した、Mac上の **AFM 3 Core Advanced** 用タスクハーネス。標準入力の文書を、根拠位置付きの値、分類ラベル、要約、書き換え、指定したJSONに変換する。ファイルの更新案と小規模な数理最適化は通常コードでも検証できる。モデルを使うときはOS付属の `/usr/bin/fm` で `system` のモデル名を確認する。

表を編集するときは、行の並びが変わっても対象を見失わないよう、品番などの一意なIDと読み取り時の版を指定できる。36件の試験では、行番号と旧値だけの方式で18件あった誤った変更案が0件になり、正当な15件はすべて完了した。[表編集の比較](eval/row_guard/README.md)

初見の合成54件では直接回答の28件に対してAFMCLIが43件正解。別の著者が作った日本語・英語の公開データ47件では10件から19件になった。日本語の根拠付きQAは2/16から9/16、英語QAは双方3/16だった。最適化の数式を自然言語から正しく読み取ることや、抜き出した語句が質問に答えているかには誤りが残る。[実測結果と失敗例](eval/README.md)

## インストール

モデル実行には、Apple Intelligenceが有効で、`fm models` に `✓ system (AFM 3 Core Advanced)` と表示されるMacが必要。今回の実機はMac mini M6、32 GB、モデル配置先は内蔵SSD 256 GB、macOS 27.2.1（26B131）。次のコマンドでRust 1.89以上を使い、関数電卓を含むv0.4.0のソースをインストールできる。[v0.4.0のmacOS向け配布ファイルとSHA-256](https://github.com/ultimate-1113/AFMCLI/releases/tag/v0.4.0)・[AppleのCLI解説](https://developer.apple.com/videos/play/wwdc2026/334/)

```sh
cargo install --locked --git https://github.com/ultimate-1113/AFMCLI --tag v0.4.0 afmcli
afmcli doctor
```

ダウンロード版はアーカイブと同名の `.sha256` を同じフォルダに置き、空のディレクトリで確認して展開する。

```sh
shasum -a 256 -c afmcli-v0.4.0-aarch64-apple-darwin.tar.gz.sha256
tar -xzf afmcli-v0.4.0-aarch64-apple-darwin.tar.gz
./afmcli doctor
```

## スキルを読み込む

手元の `SKILL.md` を登録すると、`--skill 名前` を付けたタスクの各生成に、その手順が渡る。同梱の2例は、資料から最短の回答を抜く手順と、整数最適化の変数・制約・目的を読む手順。`--decompose` は独立したオプションで、QAでは根拠文を先に選び、数式化では制約を宣言ごとに扱う。[実機での比較](eval/procedures/README.md)・[Agent Skillsの仕様](https://agentskills.io/specification)

式を含む最適化8問で、段階分割を使うと最終解の正解数が4件から6件に増えた。文章だけで条件を示した12問では4件から3件になった。資料QAの完全一致は、直接回答12/40件、標準26/40件、手順書のみ24/40件、段階分割25/40件、併用22/40件だった。[用途別の正解数・所要時間・失敗例](eval/procedures/README.md)

```sh
afmcli skills import skills/short-evidence
afmcli skills import skills/linear-declarations/SKILL.md
afmcli skills list
printf '%s\n' '旧会場は港館。新会場は青葉館に決定。' |
  afmcli --skill short-evidence answer '新会場は？'
printf '%s\n' 'Integer N ranges from 0 to 8. Require N < 4. Maximize N.' |
  afmcli --decompose solve --from-text
```

インポートにはフォルダと同名の `name` および `description` を含むYAML frontmatterと、Markdown本文が必要。参照文書はインポート時に `--reference references/guide.md` のように指定する。対象は選んだフォルダ内のMarkdownとテキストで、本文・参照は各64 KiB、合計128 KiBまで。スクリプトの起動、外部ツールの権限付与、自動選択は実装範囲外。手順や参照の長さもタスクの入力トークン予算に含まれる。

Macでの登録先は `~/Library/Application Support/AFMCLI/skills`。`AFMCLI_SKILLS_DIR` で保存先を指定できる。同じ内容は再登録でき、内容の更新には `--replace` を付ける。

## 文書を渡す

次の例は、新しい会場を原文から抜き出し、その位置を返す。返却値 `result.answer.spans` の `start_byte` は先頭を含み、`end_byte` は末尾を含まない。UTF-8の元のバイト列に対する位置で、同じ語句が複数回あると検出した位置が複数入る。

```sh
printf '%s\n' '旧会場は中庭。新会場は青葉館に決定。' |
  afmcli answer '新会場の名前は？'
```

| コマンド | 用途と出力 |
|---|---|
| `ask '指示'` | 自由な質問、生成、説明。ソースを標準入力で追加できる |
| `auto '指示'` | 指示に応じてask・answer・summarize・rewriteを選ぶ。JSONに選んだrouteを含む。明示的な上限や出力契約がある場合は個別コマンドで指定する |
| `answer '質問'` | 資料から最短の回答箇所を提案させ、原文位置を検査する。標準では候補が質問に答えているかをモデルに再確認する。`--fast-answer` でその1回を省ける |
| `extract --field '項目=説明' --many '項目=説明'` | 任意の名前・説明で、単値または複数値を抽出。値ごとに原文位置を返す。情報がない項目には空配列が入る |
| `classify --label '名前=意味' --label '名前=意味'` | 利用者の分類基準で該当ラベルを1つ返す。該当なし・複数該当は保留し、labelをnullにする |
| `summarize -n 3 --focus '決定事項'` | 原文の重要な文を最大3件、原文順と位置付きで返す。焦点の指定は任意 |
| `rewrite '指示' --keep-numbers --max-chars 80` | 書き換え・翻訳。数値表記と出現数、Unicodeスカラー値による文字数上限を検査する。`--require-change` を付けると原文の丸写しを検出する |
| `structure schema.json '指示'` | 任意の項目を持つJSONオブジェクトを生成する。ネイティブの構造化生成とローカルの型検査を使用 |
| `edit` | 旧値を照合し、一括で更新をプレビューする。表には対象IDと観測時の版も指定できる |
| `solve` / `solve --from-text` | 有界整数の線形制約・複数の目的関数を扱う。`--from-text` はモデルが解釈した式も返し、人による確認を求める |
| `run` / `batch` | JSONのタスク1件、またはJSON Linesを1件ずつ処理する |

文書は `-i FILE` でも指定できる。`ask`、`classify`、`structure` は `--image PATH` に対応する。文章だけの要求は原則標準入力でfmに渡す。画像と本文を併用する場合はfmの `--image` と `--text` を組み合わせるため、本文がそのプロセスの引数に入る。実行時の一時ディレクトリは所有者専用で、応答を読んだ後に削除する。ルート、入力文、指定した画像の添付を実行記録と照合する。

```sh
printf '%s\n' '配送済みは黒箱と白箱。費用は未記入。' |
  afmcli extract --many 'delivered=配送済みの箱を個別に' --field 'cost=明記された費用'
printf '%s\n' '試験は9時と14時です。' |
  afmcli --text rewrite '簡潔な掲示文にする' --keep-numbers --max-chars 30
printf '%s\n' '{"id":"s1","task":"summarize","source":"見学は終了。明日は休館。","sentences":1,"focus":"明日の案内"}' |
  afmcli batch
```

### 長い履歴や資料から根拠付きで答える

`--compact` は、一度に読める資料には標準のQAを使う。長い資料では、同一の文を辞書にまとめ、出現順と回数を添えて渡す。1文ずつ辞書にしても収まらない場合は、2〜8文が同じ順番で3回以上続く箇所をまとめる。いずれも改行を含む入力全文を復元できる。両方の辞書が収まらない場合は元の資料を順に読む。交互に通知が現れる合成履歴6問では、v0.3.0の0問正解からv0.3.1は4問正解になった。[実機の比較と失敗例](eval/repeats/README.md)

```sh
afmcli --compact answer '会議で最終的に決まった会場はどこですか？' -i minutes.txt
```

`--compact=evidence` は、辞書が使えない資料の全区間から根拠・条件・撤回を示す文を選び、前後の文も加える。回答と裏付けの再確認には、選んだ範囲を元の順番で渡す。引用を元のUTF-8バイト位置で照合し、離れた範囲をまたぐ引用は保留する。この方式では、文の選択にかかる時間と、関連する文を見落とす可能性がある。選んだ範囲が大きければ全区間の読み取りに戻る。[実機での正答数と処理量](eval/context/README.md)

点検番号や測定値だけが変わる長いログには `--compact=records` が使える。共通の文章をまとめ、各行の数字と原文順を保存する。初見の変更通知30問では `--compact=evidence` と同じ24問に正答し、送信入力の合計は103,587から68,749トークンに減った。数値検索8問では両方式とも正解行を引用できた6問と正しい棄権2問で8/8だった。[変更通知と数値検索の実測](eval/grounding/README.md)

撤回済みの値を返すことを特に避けたい用途では `--compact=review` を選べる。初回の候補を伏せて、該当する値と変更の原文を照合する。初見の未確定15問で値を返した誤りは4件から1件に減り、確定値のある15問の正答は13件から9件になった。[判断を誤ったケース](eval/grounding/holdout.jsonl)

`metrics.sent_input_tokens` は回答・確認・再試行に送った入力トークンの推定合計。長文の処理時は `metrics.context` に方式、元の文数、保持した文数、根拠選択を完了した区間数が入る。会話を扱う際は、発言者と発言順が分かるテキストを入力する。

11月26日の実機試験では、一般40件で直呼び24件、標準ハーネス39件、タスク別の短い指示を追加した方式36件が正解した。長文の索引検索は送信入力を31%減らし、正答は22/30件で同じだった。未確定の問で値を返す誤りが2件から4件に増えたため、索引検索は研究用ビルドに収録した。翻訳の局所修正では重大な誤りが減ったが、正しい訳の件数は増えなかった。[用途別の結果・失敗例と再現方法](eval/moe/README.md)

## 数式から数値を返す

v0.4.0 の `calc` は、分数・べき乗・三角関数・対数・組み合わせをRustで計算する。確定した式はモデル呼び出し0回で数値を返す。文章題を `calc --from-text` に渡すと、AFMの提案式と値を一緒に確認できる。

```sh
afmcli --text calc '((7/12 + 5/18) / (11/9 - 2/3))^2'
# 2.4025
afmcli --text calc --angle deg '12 * sin(30) + sqrt(144) - log10(1000)'
# 15
```

Swiftの公開APIに電卓を登録した試験では、AFMが6問すべてで自らツールを呼び出した。電卓の値は6/6正解、AFMが数値だけを返した最終回答は5/6正解。同じ条件で電卓を登録しなかったときは0/6だった。[式・文章題の比較と実行方法](eval/calculator/README.md)・[Swiftでツールを登録するサンプル](eval/calculator/native/README.md)

## JSON更新と数理最適化

次の更新では、`open` が12のときだけ3を加える。複数の操作のうち1つでも旧値が合わないと、元の文書全体を返す。CLIは更新案を標準出力へ出す。共有ストアに反映するアプリではストア側で版の照合と書き込みを一つのトランザクションにする。

```sh
printf '%s\n' '{"document":{"desk":{"open":12}},"changes":[{"path":"/desk/open","expect":12,"op":"add","value":3}]}' |
  afmcli edit
```

表の行順が変わる場合は、`row` に配列のJSON Pointer、一意なIDの列と値、行ごとの更新版の列と観測値を渡す。各変更の `path` は行直下のデータ列を指定する。対象が一つあり、版・旧値がすべて一致すると更新案を返し、実際に値が変わる場合だけ行の版を1進める。対象の欠落、ID重複、版の不一致があると文書全体を保留する。IDは文字列または整数、版は非負の64ビット整数。次の例では2行とも数量は4だが、品番K-9だけを6にする。[入力JSON](examples/guarded-row.json)・[36件の誤適用試験](eval/row_guard/README.md)

```sh
afmcli edit -i examples/guarded-row.json
```

`set` はスカラー値を置換し、`add` は符号付き64ビット整数を加算する。複数操作は元の文書に対して検証する。重複したパス、存在しないパス、オーバーフローは保留する。

ファイルの例では、TXTからの会場名抽出、JSONの定員更新、改行を含むCSVの数量更新を試せる。CSVは品番を文字列として読み、ID・版・旧値が一致した行を別名のファイルに保存する。[再実行用の例](examples/file_tasks/README.md)・[実際に出力したCSV](examples/file_tasks/sample/after.csv)

```sh
printf '%s\n' '{"variables":[{"name":"X","min":0,"max":5}],"constraints":[{"weights":[2],"relation":"ge","rhs":7}],"objectives":[{"weights":[1],"sense":"min"}]}' |
  afmcli solve
```

この例の最適解は `X=4`。重みの並びは宣言した変数順、目的関数は優先度順。すべて同率なら変数順で辞書式に最小の解を返し、同率数も出す。比較は `lt/le/eq/ge/gt`、目的は `min/max`。最大12変数・25万組で、積和演算の見積もりにも上限がある。自然言語からの解釈では、変数名と係数・境界値の出典を検査したうえで `review` として式と計算結果を返す。数値の役割や不等号の読み取りにも確認が必要になる。

## 結果と処理量

返すJSONには `status`、`result`、`notes`、`metrics` がある。`ok` はそのタスクで実装している検査を通ったことを示す。部分抽出は `partial`、回答を根拠付けられない場合は `abstained`、人の確認が必要な数式は `review`。検査や予算による中断は `held`、モデル側の拒否は `refused`、入力・環境の失敗は `error`。終了コードは通常0、エラー2、保留・拒否3。バッチは各行の結果を順次出力し、終了コードではエラーを優先する。単発コマンドの引数・JSON解釈エラーは標準エラーに出る。

`--direct` は同じタスクをモデルに1回依頼する比較用モードで、結果は `unverified`。`--trace` はモデルが返した文字列を出力に含める。標準の `--repairs 1` は形式や出典など、コードで特定した失敗に対する再試行回数。`--max-calls 24` は1ジョブの生成回数上限。

生成前に `fm count-tokens` で指示・本文を数え、スキーマも計数する。標準入力予算は6,200トークンで、モデルの8,192トークンの文脈に応答用の余裕を持たせた。標準の抽出・引用回答は長文を連続した範囲に分け、全範囲を処理する。引用回答で区間ごとの候補が食い違った場合は `abstained` を返す。要約は文を選び、複数範囲にまたがるときは候補を再選択する。予算内に全体を処理できない場合は `held` を返す。書き換え・自由回答には全文を渡す。

`structure` が扱うスキーマは、ルートがobject、各階層でproperties・required・additionalProperties=falseを明示したもの。値にはobject、arrayとitems、stringと任意のenum、integer、number、booleanを使える。未対応キーワード、重複したJSONキー、生成結果の型違反にはエラーまたは保留を返す。事実性は原文や業務上の条件と照合して確かめる。

## 再現とライセンス

```sh
cargo test --locked --all-targets
python3 -B tools/verify_results.py
python3 -B tools/privacy_audit.py --tracked
```

実装・テスト・自作資料はMIT。Appleのソフトウェア、モデル出力、公開評価データにはそれぞれの利用条件が適用される。[MIT](LICENSE)・[出典と第三者の権利](NOTICES.md)・[再現手順と機種条件](eval/README.md)
