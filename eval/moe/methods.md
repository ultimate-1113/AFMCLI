# 各方式の出典と実装

AFM 3 Core Advancedでは、入力の初期処理で使う専門家を選ぶとAppleが説明している。外から変更できるのは、この試験ではモデルに渡す資料、手順と検算である。専門家の選択結果やNANDの読み込み量は公開CLIから計測していない。プロンプトの違いによる成績差から、特定の専門家が選ばれたとは判断できない。[Appleのモデル解説](https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models)

| 試す考え方 | AFMCLIで変更した点 | 参照した原典 |
|---|---|---|
| 必要な手順だけを渡す | 同一の短い指示と、タスク・資料の特徴で選ぶ短い指示を別条件にした。該当しない入力は既存の経路を通る | [ACE論文](https://arxiv.org/abs/2510.04618)・[著者実装](https://github.com/ace-agent/ace)・[GuideTree論文](https://aclanthology.org/2026.acl-industry.110/)・[著者実装](https://github.com/gechengze/guidetree) |
| 長い入力から必要な原文を取り出す | ホスト側に元文書を置き、段落を検索して、原文をバイト位置付きで渡す。関連する訂正を初回に回収できない場合にもう1回探索できる。全件を要する質問は全文走査を使う | [RLM論文](https://arxiv.org/abs/2512.24601)・[著者実装](https://github.com/alexzhang13/rlm)・[Observation Maskingの比較実験](https://arxiv.org/abs/2508.21433) |
| 表の解釈と数値演算を分担する | モデルが指定した列・明細・注記をホスト側で検査し、型の付いた列について計算する。規則だけで同じ資料を読む条件も設けた | [TableRAG論文](https://aclanthology.org/2025.emnlp-main.710/)・[著者実装](https://github.com/yxh-y/TableRAG) |
| 翻訳を局所的に修正する | 用語・数字・単位・行の対応を検査して、問題のある箇所だけを再生成する。元から正しかった部分を壊した件数も採点する | [STRIDE](https://aclanthology.org/2026.wmt-main.61/)・[翻訳の推論時計算に関する研究](https://aclanthology.org/2026.acl-long.268/) |
| 制約の境界を別の読み方で検査する | 整数問題の候補について、原文を使って実行可能性を独立に確認する。許容範囲内の候補を通常コードで照合する | [SETS論文](https://arxiv.org/abs/2501.19306)・[gpt-ossのツール利用条件別評価](https://deploymentsafety.openai.com/gpt-oss/full-evaluations) |

原典のモデル、外部ツールと計算予算はそれぞれ異なる。たとえばRLMはモデル自身によるプログラム実行を扱い、TableRAGはデータベースと問い合わせを組み合わせる。今回の実装で借りたのは、長文を外に置く操作、構造化した計算、出力を独立に検査する操作である。今回使ったモデル、入力、呼び出し回数とスコアは実機記録から再計算できる。[試験手順](protocol.md)
