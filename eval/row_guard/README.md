# 表編集の誤適用を減らす

在庫・予約・得点表の3種類に、並べ替え、行の挿入、版変更、古い依頼の再送など12パターンを適用した。36件は対象行と操作が構造化された合成依頼である。正当な依頼は15件で、うち12件は変更、3件は同値への設定。残り21件は保留すべき依頼とした。利用者が選んだ対象IDと観測時の版を入力とし、自然文からIDを推定する段階は評価に含めていない。[事前に固定した条件](protocol.md)・[入力と期待値](cases.jsonl)

| 方式 | 正しい最終文書 | 誤った変更案 | 正当な依頼を保留 | モデル呼び出し |
|---|---:|---:|---:|---:|
| Core Advancedに直接依頼 | 5/36 | 17 | 判定対象外 | 36 |
| 元の行番号と旧値で適用 | 18/36 | 18 | 0 | 0 |
| 元の行番号でID・版・旧値も照合 | 27/36 | 3 | 6 | 0 |
| IDで行を検索し、版・旧値を照合 | **36/36** | **0** | **0** | **0** |

「正しい最終文書」は文書全体と変更フラグの一致。「誤った変更案」は返った文書に変更があり、期待する文書と異なるもの。直接依頼は36件中12件で指定形式を読み取れず、7件で無変更を提案した。直接依頼には明示的な保留ステータスがない。表中の誤った変更案はすべてプレビューで観測したものである。[144回の生の結果](results.jsonl)

行番号を固定すると、対象行の前に別の行が入ったとき、同じ旧数量を持つ別の品目を書き換え得る。元の行番号でID・版も調べる方式では、並べ替え・挿入で正当な6件を保留し、IDの重複を見落として3件を誤適用した。配列からIDを検索する方式は、その行が1件だけか検査し、観測時の版と各フィールドの旧値を照合する。今回の測定では正当な15件が完了し、競合・重複・欠落を含む21件が保留された。CLI起動を含む中央値はID検索で2ミリ秒、直接依頼で約0.97秒だった。

利用例では、現在の順番にかかわらず品番K-9の数量を4から6にし、同じ更新で版を3から4に進める。実ストアに反映するアプリは、読み取ったID・版の再照合と保存を同一トランザクションで行う。再作成した行にも古い依頼が届き得る場合は、同じIDと版の組を再利用しない運用が必要になる。[実行できる入力例](../../examples/guarded-row.json)

```sh
afmcli edit -i examples/guarded-row.json
```

差分の適用先を文脈と旧行から探すCodexの実装と、事前に観測した版が変わったら書き込みを止めるDeepSeek Harnessの任意設定を参考にした。AFMCLIでは読み取った行ID・版を呼び出し元が渡し、複数の変更が成立するかをRustで検査する。版の照合はHTTPのIf-Match、旧値の照合はJSON Patchのtestとも共通する。[Codexの適用先探索](https://github.com/openai/codex/blob/a0d5b0a661db3b34cd87bdf1c93736b90fd3694b/codex-rs/apply-patch/src/file_update.rs)・[DeepSeekの観測ポリシー](https://github.com/deepseek-ai/deepseek-harness/blob/4b399c39a6d1479cd84ca591b4d7c88e24d38a96/packages/fs/tool-fs/README.md)・[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1)・[RFC 6902](https://www.rfc-editor.org/rfc/rfc6902.html#section-4.6)

## 再現手順

更新版では、初版の合成54件と外部47件も元の採点器で再計測した。合成は直接28件・標準43件、外部は直接10件・標準19件で、各問・各方式の合否も初版と一致した。これら101件は既に初版で使用した回帰試験である。[202回の再計測結果](compatibility.jsonl)・[初版の評価と限界](../README.md)

Mac mini M6、32 GB、内蔵SSD 256 GB、macOS 27.2.1（26B131）。実機の `system` はAFM 3 Core Advanced。評価したv0.1.1の実行ファイルのSHA-256は `b6f98b9b0446ce1cbb9394ec00ac51c3843397124b9c697efb117cf4e1eff82e`。入力・採点器・実装は実行前に固定した。モデル呼び出しは同時1件、greedy。[固定したファイル](lock.json)・[実行前のコミット](https://github.com/ultimate-1113/AFMCLI/commit/358c7e0)

```sh
cargo build --locked
git worktree add --detach ../afmcli-row-baseline v0.1.1
python3 -B ../afmcli-row-baseline/eval/row_guard/run.py replay --output eval/row_guard/results.jsonl --binary target/debug/afmcli
```

対応するMacでモデルも再実行する場合は、別の出力先を用意する。

```sh
cargo build --release --locked
mkdir -p eval/work
python3 -B ../afmcli-row-baseline/eval/row_guard/run.py run --binary target/release/afmcli --output eval/work/row-guard.jsonl
```
