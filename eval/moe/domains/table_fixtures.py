"""New authored invoice questions. Masks and arithmetic below form the independent answer key."""
from decimal import Decimal

# Positions 0..5 in each ledger, before the presentation-order permutation.
# Data: status = posted, pending, void, posted, posted, pending.
# region = east, west, east, west, east, west. SKU = A, B, C, A, B, C.
# Fields: request, gold aggregation, included positions, optional note, gold asserted quantity overrides.
DEV = [
    ("確定した明細だけの金額合計を求めてください。", "amount", (0,3,4), "", {}),
    ("西支店で保留中の商品の数量は計いくつですか。", "quantity", (1,5), "", {}),
    ("訂正メモを反映して全行の請求額を集計してください。", "amount", (0,1,2,3,4,5), "{r1} の数量欄は仮入力でした。受領票で確認できた正しい数量は {new} です。", {1:"new"}),
    ("価格表を商品コードで引き、東支店の明細金額の合計を出してください。", "catalog", (0,2,4), "", {}),
    ("Include posted lines from either branch. What is their value after the audit note?", "amount", (0,4), "{r3} is a duplicate of a previously settled invoice. Omit this line from the total.", {}),
    ("{cut} より後の日付の明細は何行ですか。", "count", (3,4,5), "", {}),
    ("台帳全体の合計を教えてください。", "clarify", (), "", {}),
    ("How many units of {skuB} are on non-void lines?", "quantity", (1,4), "", {}),
]
TEST = [
    ("確定行のうち、東拠点に属するものはいくつありますか。明細数で答えてください。","count",(0,4),"",{}),
    ("{cut} まで（当日を含む）の東支店分の請求金額を足してください。","amount",(0,2),"",{}),
    ("Count pending lines across both branches after the clerk's correction.","count",(1,),"{r5} has now been posted. Its pending flag in the export is stale.",{5:"posted"}),
    ("商品 {skuA} の明細について、参照価格×数量の合計を出してください。","catalog",(0,3),"",{}),
    ("取消扱いも保留扱いも除いた数量合計をお願いします。","quantity",(0,3,4),"",{}),
    ("返品を含めて東支店の請求額を計算し、監査メモも反映してください。","amount",(0,2,4),"{r2} の記載数量は取消済みの速報値。確定した返品数量は -2 です。",{2:"-2"}),
    ("What is the billed amount for rows dated strictly before {cut}?","amount",(0,1),"",{}),
    ("第2承認者が控えた方の金額を合計してください。","clarify",(),"",{}),
    ("価格表の単価を適用して、西支店の保留行の総額を求めてください。","catalog",(1,5),"",{}),
    ("{skuC} を扱った明細の行数はいくつですか。メモで無効扱いになった行を除きます。","count",(5,),"{r2} は取消処理のテスト用に作った行で、今回の集計対象からは外してください。",{}),
    ("How many units were recorded on posted lines from the west branch?","quantity",(3,),"",{}),
    ("全行について金額を合算してください。単価は明細に記載されたものを使います。","amount",(0,1,2,3,4,5),"",{}),
    ("確定しかつ日付が {cut} 以降（当日含む）の明細は何行ですか。","count",(3,4),"",{}),
    ("Sum the quantity on the pending lines using the receiving amendment.","quantity",(1,5),"{r1} was keyed before the recount. The signed receiving slip confirms {new} units, replacing its listed quantity.",{1:"new"}),
    ("{cut} 当日に記録された明細に限り、商品台帳に載った単価で金額を計算してください。","catalog",(2,),"",{}),
    ("最も都合のよい換算レートで外貨の総額を円に直してください。","clarify",(),"",{}),
    ("保留中の金額を明細単価で集計してください。担当者の追記を優先します。","amount",(5,),"{r1} は支払確定の連絡を受信済みです。エクスポート上の保留を確定に読み替えてください。",{1:"posted"}),
    ("Count all lines except the void ones; do not weight by quantity.","count",(0,1,3,4,5),"",{}),
    ("商品 {skuB} が東支店で記録された数量合計を出してください。","quantity",(4,),"",{}),
    ("明細単価で {skuC} の請求合計を求めてください。締め後の訂正を使います。","amount",(2,5),"{r5} の数量は {new} が正でした。旧数量で出した値は失効しています。",{5:"new"}),
    ("What is the catalog-priced total for posted items?","catalog",(0,3,4),"",{}),
    ("{cut} 以前（当日含む）に取消で登録された明細は何行ですか。","count",(2,),"",{}),
    ("東支店に届いた分の数量を、受け入れ対象外と判明したものを除いて集計してください。","quantity",(2,4),"{r0} の送付先が別会社だったことがわかりました。この行は受け入れ対象外です。",{}),
    ("Determine the net margin using the cost of goods sold.","clarify",(),"",{}),
    ("{cut} より後に保留状態だった商品の明細単価による請求額を求めてください。","amount",(5,),"",{}),
    ("Count rows belonging to the west office on or after {cut}.","count",(3,5),"",{}),
    ("メモの訂正を反映し、商品 {skuA} を参照表単価で評価した総額を出してください。","catalog",(0,3),"{r3} の数量は梱包前に確認した数です。最終的な出荷数として {new} に訂正します。",{3:"new"}),
    ("商品 {skuC} の保留行について数量だけを足してください。","quantity",(5,),"",{}),
    ("全支店の確定行の金額を集計してください。今回の便に入らなかった行を除きます。","amount",(3,4),"{r0} は次便へ振り替えました。今便の請求明細から取り除いてください。",{}),
    ("店舗別小計のうち中央支店の数量を答えてください。別名との対応表は欠けています。","clarify",(),"",{}),
]

COLUMNS = [
    ("state","branch","sku","qty","unit_price","day","code","reference_price"),
    ("計上状態","拠点","品目","数量","明細単価","日付","品目番号","価格"),
    ("approval","office","item","units","price","date","item_key","rate"),
]
STATUS = [["posted","pending","void"], ["確定","保留","取消"]]
BRANCH = [["east","west"], ["東","西"]]


def make(split):
    output = []
    for i, (query, metric, mask, note, corrections) in enumerate(DEV if split=="dev" else TEST):
        j = i if split=="dev" else i+19
        st, loc, sku, qty, price, day, key, ref = COLUMNS[j % len(COLUMNS)]
        posted,pending,void = STATUS[j%2]
        east,west = BRANCH[j%2]
        skus = [f"{a}{j+51}" for a in ("MA","PB","QC")]
        ids = [f"L{j+14}{k+1}" for k in range(6)]
        month = j%9+1
        qtys = [2+j%3, 4+j%5, -1-j%2, 3+j%4, 2+j%4, 1+j%3]
        prices = [Decimal(31+(j*3+k*7)%68)+Decimal(v) for k,v in enumerate((".25",".10",".05",".99",".50",".75"))]
        refs = [Decimal(40+j%8+k*11)+Decimal(".35") for k in range(3)]
        dates = [f"2026-{month:02d}-{d:02d}" for d in (2,5,8,11,14,17)]
        rawrows = [{"id":ids[k],st:[posted,pending,void,posted,posted,pending][k],loc:[east,west][k%2 if k!=3 else 1],
                    sku:skus[[0,1,2,0,1,2][k]],qty:str(qtys[k]),price:str(prices[k]),day:dates[k]} for k in range(6)]
        # Keep every row's date, ID, amount and product independent of its presentation position.
        env = dict(cut=dates[2],skuA=skus[0],skuB=skus[1],skuC=skus[2],new=str(9+j%5))
        env.update({f"r{k}":identifier for k,identifier in enumerate(ids)})
        book = dict(columns={"id":"id",st:"text",loc:"text",sku:"text",qty:"integer",price:"decimal",day:"date"},
                    rows=[rawrows[k] for k in (4,1,5,0,3,2)],
                    catalog={"columns":{key:"text",ref:"decimal"},
                             "rows":[{key:skus[k],ref:str(refs[k])} for k in (2,0,1)]},
                    notes=([{"id":f"N{j+10}","text":note.format(**env)}] if note else []))
        effective = list(qtys)
        for k,val in corrections.items():
            if val!="posted": effective[k] = int(env["new"] if val=="new" else val)
        total = None
        if metric!="clarify":
            if metric=="count": total=len(mask)
            if metric=="quantity": total=sum(effective[k] for k in mask)
            if metric=="amount": total=sum(effective[k]*prices[k] for k in mask)
            if metric=="catalog": total=sum(effective[k]*refs[[0,1,2,0,1,2][k]] for k in mask)
        gold = dict(state="clarify" if total is None else "ok",value="" if total is None else str(total),
                    row_ids=sorted(ids[k] for k in mask), note_ids=[n["id"] for n in book["notes"]])
        output.append(dict(id=f"table-{split}-{i+1:02}",group="table",job=dict(request=query.format(**env),table=book),gold=gold))
    return output
