"""Supplementary 8k/16k multi-entity QA panel. Generation and grading are inference-free.

The three named decisions and three cross-referenced decisions are separate strata. The
background is an anthology of records about distinct objects. Only "job" is sent to the CLI.
Apple-token calibration uses make_case(..., filler_count=N), with the same N per pair.
"""

from hashlib import sha256
import json
from pathlib import Path
import random

try:
    from .fixtures import grade
except ImportError:
    from fixtures import grade


HERE = Path(__file__).resolve().parent
ARMS = ("records", "indexed", "guard")
TIERS = ("8k", "16k")

# Two or three lines of the closing paperwork vary together. The mark encloses the
# authoritative occurrence alone. Earlier specimens and other objects never supply a gold span.
MATERIAL = {
    ("8k", 1): dict(
        language="en", group="named_decision", title="Civic waterworks • foreman's collected service papers",
        question="For the Gullwing fountain head FH-713, which insert designation has actually been released for fitting?",
        kinds=("bubbler","spill bowl","spray arch","drinking jet","scupper","rill head","basin lip","drain cage"),
        objects=("seat washer","perforated cap","thread gauge","packing ring","flow comb","bleed screw","mesh collar","face plate"),
        lead="Bench order for Gullwing head FH-713. Fit insert AC13-T9 following the dry trial. The fitter copied the order to his work pad.",
        middle="FH-713 / Gullwing. The works foreman withdrew the insert order while the threads were being checked. The dry-trial entry is no longer authority to fit.",
        decoy="For the North Croft spray arch the supplier packed AC13-T9 with a loose gauge. This is stock for that arch, charged to its own account.",
        positive="Stores release • Gullwing fountain head FH-713\nThe foreman has signed a fresh authority to fit insert ⟦AC17-V2⟧. The signed leaf goes to the fitter with the head.",
        negative="Stores release • Gullwing fountain head FH-713\nInsert AC17-V2 arrived as a loose sample. The foreman has released the head for thread inspection only; there is still no authority to fit an insert.",
        tail="Filing slip. Send the North Croft paperwork to the arch crew. The separate bench test photographs go back in the waterworks album.",
    ),
    ("8k", 2): dict(
        language="ja", group="cross_reference", title="地方出版物の保存室　受払と改装の記録集",
        question="「柚木坂」の初摺りを納める保存箱について、今効力のある移し替え票が指定する棚札は何ですか。",
        kinds=("郷土誌","引札","古い時刻表","木版刷り","折図","会報","小唄本","店頭暦"),
        objects=("中性紙の封筒","薄手の見返し","虫損の写真","折山の当て紙","背綴じの糸","仕切りの板紙","貼り込み台紙","出納の控え"),
        lead="引継書の索引。柚木坂の初摺りは移し替え票K-073へ、柚木崎の初摺りはK-037へつなぐ。書名が似ているので、票に書名を重ねて記入しない。",
        middle="改装前の指図では両方とも穂-62を使った。改装開始に伴い、二つの初摺りに対する古い棚指定をいずれも解除する。",
        decoy="庭の古暦K-370の棚札は穂-26。柚木坂の再摺り本も同じ棚に並ぶ。初摺り本は改装用の受け箱に移した。",
        positive="改装明けの係印。K-073の箱は⟦穂-62⟧へ納めること。棚を実測してから係長が押印した。\nK-037の箱は受け箱に戻し、新たな棚先は決めない。",
        negative="改装明けの係印。K-037の箱は穂-62へ納めること。棚を実測してから係長が押印した。\nK-073の箱は受け箱に戻し、新たな棚先は決めない。",
        tail="柚木崎の再摺りの出納票には穂-62とある。以前から貸出用に使っている一冊で、改装中の初摺りとは別に扱う。",
    ),
    ("8k", 3): dict(
        language="en", group="cross_reference", title="Estuary instrument pool • workshop binder and station mail",
        question="Which averaging interval is in force for the bed probe at Sandwick South? Give the value and unit.",
        kinds=("still well","turbidity frame","rain jar","level staff","mud probe","shingle gauge","weighing pan","cable buoy"),
        objects=("bed shoe","drain plug","reference weight","spare shield","clamp liner","contact brush","seal tab","sample tube"),
        lead="Sandwick South has two probes in one housing. RL-206 is the bed probe; RL-260 is the surface probe. Configuration slips carry the instrument tag alone.",
        middle="At the move to the winter mooring, the supervisor canceled the interval on both RL tags. The old bed-probe work card had called for 11 minutes.",
        decoy="North Sandwick also samples the bed. The operator of that station set a ten-minute watch on its mooring lines; this is a watch rota, separate from the instrument intervals.",
        positive="Instrument pool • sheets closed after the second tank test\nThe signed configuration entry for RL-206 sets averaging to ⟦19 minutes⟧. The RL-260 entry is a trial reading only, with configuration still open.",
        negative="Instrument pool • sheets closed after the second tank test\nThe signed configuration entry for RL-260 sets averaging to 19 minutes. The RL-206 entry is a trial reading only, with configuration still open.",
        tail="Working copy for Sandwick South: the dial face showing 11 minutes was photographed loose on the bench; the photograph records an earlier display test.",
    ),
    ("16k", 1): dict(
        language="ja", group="named_decision", title="乾物共同荷造所　送り状の控えと現場日誌",
        question="荷口『窓月』AJ-48の通い箱に、今の荷造指図が指定している中仕切りの品番は何ですか。",
        kinds=("刻み昆布","乾し椎茸","切干し","割り大根","乾燥わかめ","干し貝柱","山くらげ","乾燥ゆば"),
        objects=("底当て","麻の持ち手","箱の仕切り","小袋の口","木蓋","封緘紙","送り札","通い箱の角"),
        lead="窓月AJ-48は海側の荷造台へ。通い箱の中仕切りは試し積みを経て細波7号をいったん指定した。",
        middle="窓月AJ-48の試し積みにずれが出た。主任は中仕切りの指図を取り下げ、送り札だけを印刷に回した。",
        decoy="別荷の夕月AJ-84の送り状には細波7号とある。この荷には古い箱を使うので、夕月係が個別に取り寄せた。",
        positive="荷造台からの返書　窓月AJ-48\n主任が実物と合わせて中仕切りを⟦細波7号⟧と定め、新しい荷造指図に署名した。署名本を海側の台へ渡す。",
        negative="荷造台からの返書　窓月AJ-48\n主任が実物と合わせたのは送り札だけで、中仕切りの指図は取り下げのまま。海側の台へは送り札の署名本を渡す。",
        tail="試し積みで撮った写真は季報の資料に残す。写真の品番は窓月の荷造指図を兼ねない。",
    ),
    ("16k", 2): dict(
        language="en", group="named_decision", title="Regional kite meet • flight-line workbooks and maker correspondence",
        question="What spine stock does the current build ticket specify for the Night Minnow kite KM-58? Copy the stock name.",
        kinds=("delta kite","box train","sail bag","wing panel","line winder","tail ribbon","bridle board","wind pennant"),
        objects=("sleeve seam","corner patch","cross spar","haul loop","swivel link","edge binding","haul ring","bridle pin"),
        lead="Build KM-58, Night Minnow: Lime Reed was pencilled in during fitting and accepted on the maker's first ticket. The wing patch had arrived separately.",
        middle="The maker struck the spine-stock instruction for Night Minnow KM-58 after the wet test. No workshop may rely on the first ticket.",
        decoy="Lime Reed and Brown Willow are both stocked for the Moor Pippet display frames. The frame shelf cards list sample lengths for school visitors.",
        positive="Maker's returned ticket for KM-58 / Night Minnow\nThe new working copy specifies ⟦Ash Lamin⟧ as spine stock. The maker signed the copy for cutting.",
        negative="Maker's returned ticket for KM-58 / Night Minnow\nAsh Lamin is listed for another sampling run. The cutting instruction for the spine stock remains withdrawn, and the maker signed only for taking samples.",
        tail="The line crew value a spare spine, but their requisition asks only for a sleeve. The builder's original ticket was filed with the wet-test notes.",
    ),
    ("16k", 3): dict(
        language="ja", group="cross_reference", title="短距離走計時班　会場ごとの計時票と機材貸出票",
        question="汀丘会場の短距離走で、内側のゴールカメラの本記録に適用されるフレーム間隔はいくつですか。単位も示してください。",
        kinds=("発走台","風向旗","線引き車","ゴールカメラ","中継箱","掲示灯","計測架台","コーナー標"),
        objects=("台座の刻み","接続口","予備の脚","水準器","風除け","巻尺","焦点板","目盛り札"),
        lead="汀丘会場の機材組み合わせ表。短距離走の内側ゴールカメラは札SC-451、外側はSC-415。時刻表のコマ間隔欄には本体札だけを書く。",
        middle="汀丘の短距離走で従来使った2 msの設定指示を解除した。調整中は内外両方の本記録を止め、テスト映像だけを集める。",
        decoy="河汀会場の内側カメラはSC-514。焦点板の確認用映像を5 msごとに撮ったが、これは本記録前の調整。",
        positive="計時主任の戻し票。SC-415はテスト映像の確認だけで本記録の停止を解除しない。\nSC-451について本記録を再開し、コマ間隔⟦5 ms⟧を適用する。主任の署名と撮影係の受領印を確認した。",
        negative="計時主任の戻し票。SC-451はテスト映像の確認だけで本記録の停止を解除しない。\nSC-415について本記録を再開し、コマ間隔5 msを適用する。主任の署名と撮影係の受領印を確認した。",
        tail="汀丘会場の集合写真には内外の機材と5 msと書いた試験用の札が写る。写真は手順説明のために使った。",
    ),
}

EN_STEMS = ("Blackthorn", "Lowfern", "Whitecap", "Siltbank", "Dewpit", "Maple", "Wildmead", "Starling",
            "Dunstone", "Pearl", "Rush", "Winter", "Reeve", "Oak", "Owl", "Pilgrim", "Crow", "Sedge",
            "Thimble", "Bloom", "Ivory", "Tallow", "Wicker", "Crane", "Fallow", "Briar", "Slate", "Cuckoo")
EN_ENDS = ("Field", "Cut", "Reach", "Gate", "Bend", "Mere", "Row", "Cross", "Wharf", "Pool", "Heath",
           "Croft", "Mill", "Strand", "Nook", "Grove", "Glen", "Bank", "Hall", "Wick", "Ness", "Park",
           "Ledge", "Ford", "Green", "Ridge", "Wash", "Lea")
JA_STEMS = ("若葉", "音羽", "渚原", "明石", "丸尾", "錦野", "桜森", "安房", "紅谷", "相生", "中伏",
            "打越", "霞野", "杉崎", "麻生", "千曲", "黒羽", "三戸", "青砥", "坂船", "夕浜", "砥木",
            "門島", "山添", "竹谷", "小杉", "榎野", "横瀬")
JA_ENDS = ("東", "西", "下手", "上手", "新町", "北口", "南口", "浜手", "川端", "山際", "横町",
           "船場", "奥", "長屋", "入江", "本宿", "新道", "木戸", "大辻", "裏坂", "高台", "野辺",
           "平", "窪", "前", "中筋", "角", "谷口")
TEAM_EN = ("A. Day", "B. Quist", "T. Dale", "Y. Nara", "M. Penn", "E. Ford", "J. Arbour", "F. Yu",
           "K. Finch", "S. Ives", "C. Bell", "H. Voss")
TEAM_JA = ("椎名", "石部", "笠木", "湯田", "根津", "実方", "黒江", "小幡", "植村", "富樫", "越川", "影田")
PAPERS = ("HL", "MS", "WQ", "TP", "NR", "WX", "GH", "SB", "FP", "HC", "VJ", "DR", "YP")

# Forms interleave supplier mail, signed receipts, free prose, tables, questions, captions
# and item labels. Each primary item has a unique catalog key; entries refer back to a real
# different item in this same register. Whole rows are never obtained by repeating a block.
EN_FORMS = (
    "Purchase slip {tag} — {place} {kind}. Send the {item} with the next consignment, separately wrapped. {person} kept a sample of the {other} for comparison.",
    "Bench diary. While handling the {place} {kind}, {person} found a pencil line on the {item}. It can be seen on the contact print under {tag}, beside the photograph of the {other}.",
    "To the morning shift, from {person}: the {place} {kind} is under {tag}. Take a wide shot of the {item} before setting it aside. The cropped view loses the edge of the {other}.",
    "{tag} | {place} | {kind}\nReceived on the {month} run: {qty} sample pieces. The {item} bears the small maker's stamp described by {person}. The {other} came in a separate wrapper.",
    "Pencil caption on a mounted print: {place}, {kind}, looking from its short side. The shadow on the {item} is a fold in the print. Mount {tag} carries the uncropped negative.",
    "Dispatch enquiry. Does the {place} {kind} travel with the {item}? {person} replied on the docket that the {other} travels alongside it. The enquiry is catalogued as {tag}.",
    "Index to loan material. See {tag} for the {place} {kind}; the sketch of its {item} was made in {month}. A marginal reference points to {related} for a different piece.",
    "At the counter {person} compared two examples of the {item}. The one in the {place} {kind} folder, {tag}, has a gray paper label. The other example is in the {related} parcel.",
    "Return receipt / {tag}. Counted {qty} small parts for the {place} {kind}. The hand drawing of the {other} came back too, folded against the packing note for the {item}.",
    "{person} to stores — Please leave the {place} {kind} on its support until I collect the {item}. The case marked {tag} is in the same crate as the correspondence for {related}.",
    "A note about the {place} {kind} was found behind the release register. It describes the surface of the {other} and the width of its {item}. File it with {tag}.",
    "Workshop packing list, {month}. {place} {kind} • source ticket {tag}. The {other} was wrapped in plain paper, and the {item} in the striped offcut identified by {person}.",
    "Illustrations for {place}: view of the {kind}, a section through the {item}, and a sketch of the {other}. All three sheets carry the same job number, {tag}.",
    "At the open bench {person} laid out the {place} {kind}. On the public copy of {tag} the {item} can be seen from above; on the private working print it is shown from the end.",
    "Transfer {tag} concerned the {place} {kind}. The porter counted the wrapped {item} with the packet of {other} sketches, then left both on the shelf beside the {related} box.",
    "The archive volunteer matched the loose photograph to the {place} {kind}. The {item} is visible just above the {other} in the print from {month}. On the back it says {tag}.",
)
JA_FORMS = (
    "品物の送り状　{place}の{kind}、伝票{tag}。{item}は次便で紙を分けて送ること。{person}は{other}の見本を照合用に手元に置いた。",
    "作業日誌。{place}の{kind}を広げると、{item}に鉛筆の線が見えた。{person}が撮った{tag}の密着写真では{other}の脇にその線が写る。",
    "{person}から早番へ。{place}の{kind}は{tag}の台紙に載せた。{item}の全体を撮っておくこと。寄った写真では{other}の端が入らない。",
    "{tag}｜{place}｜{kind}\n{month}月便で見本が{qty}点届いた。{item}には{person}が言っていた小さい印がある。{other}は別包み。",
    "古い台紙の説明書き。{place}の{kind}を短辺から見た写真で、{item}にかかる影は印画紙の折れ目。{tag}の袋に裁つ前の原版が入っている。",
    "発送前の照会。{place}の{kind}には{item}も付けるのか。{person}の返書には{other}とひとまとめにするとある。往復の紙片は{tag}にまとめた。",
    "貸出票の索引。{place}の{kind}は{tag}を参照。{item}の図を写したのは{month}月。欄外の{related}は別の品を調べるときの参照先。",
    "窓口で{person}が二つの{item}を見比べた。{place}の{kind}の方には灰色の{tag}の札が付き、残る品は{related}の荷物に入っていた。",
    "返却受取　{tag}。{place}の{kind}の包みから小物が{qty}点出た。{other}の手描き図も戻り、{item}の包材の内側に畳んであった。",
    "{person}より倉庫へ。{place}の{kind}は、{item}を取りに行くまで支えに載せておいてください。{tag}の箱は{related}の書簡と同じ木箱に入れました。",
    "指図綴りの表紙の裏から{place}の{kind}を扱った紙が出てきた。{other}の表面と{item}の幅についての覚え書きなので、{tag}に挟み直す。",
    "{month}月の荷姿見本。{place}の{kind}の参照票は{tag}。{other}は無地の包み紙、{item}は{person}が取っておいた縞の紙で包んだ。",
    "{place}の挿図は、{kind}の全景、{item}の断面、{other}の素描の三枚。どの用紙にも同じ{tag}の札が貼ってある。",
    "立会机の上に{person}が{place}の{kind}を置いた。{tag}の閲覧用の写しは{item}を上から、作業用の写しは木口から見た図になっている。",
    "移管{tag}は{place}の{kind}一式。運搬係は包んだ{item}を{other}の図の袋と数え、両方を{related}の箱の隣に残した。",
    "ばらの写真が{place}の{kind}のものと分かった。{month}月の紙焼きには{other}の上に{item}が写っており、裏面に{tag}との書き入れがある。",
)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
PLACEMENTS = {
    ("8k", 1): (.13, .49, .64, .84, .96), ("8k", 2): (.22, .51, .69, .89, .96),
    ("8k", 3): (.11, .53, .66, .85, .93), ("16k", 1): (.19, .57, .43, .90, .96),
    ("16k", 2): (.09, .54, .68, .87, .95), ("16k", 3): (.16, .59, .41, .91, .98),
}


def pair_id(tier, pair_index):
    return f"index-targeted-{tier}-{pair_index:02d}"


def _background(s, key, n):
    rng = random.Random(int.from_bytes(sha256(key.encode()).digest()[:8], "big"))
    en = s["language"] == "en"
    stems, ends = (EN_STEMS, EN_ENDS) if en else (JA_STEMS, JA_ENDS)
    locations = [(a, b) for a in stems for b in ends]
    rng.shuffle(locations)
    if n > len(locations):
        raise ValueError("more background entries than independent locations")
    series = rng.sample(range(1000, 9900), n)
    tags = [f"{rng.choice(PAPERS)}-{code}" for code in series]
    forms = EN_FORMS if en else JA_FORMS
    for i, ((stem, end), tag) in enumerate(zip(locations, tags)):
        item, other = rng.sample(s["objects"], 2)
        earlier = rng.randrange(i) if i else 1
        a = dict(place=(stem + " " + end if en else stem + end), tag=tag, kind=rng.choice(s["kinds"]),
                 item=item, other=other, related=tags[earlier],
                 person=rng.choice(TEAM_EN if en else TEAM_JA), qty=rng.randrange(2, 43),
                 month=rng.choice(MONTHS) if en else rng.randint(1, 12))
        yield rng.choice(forms).format(**a)


def make_case(split, tier, pair_index, positive, filler_count=None):
    if split != "targeted" or type(pair_index) is not int or (tier, pair_index) not in MATERIAL or type(positive) is not bool:
        raise ValueError("use split=targeted, tier=8k/16k, pair_index=1..3 and bool polarity")
    s = MATERIAL[tier, pair_index]
    key = pair_id(tier, pair_index)
    if filler_count is None:
        path = HERE / "targeted_lengths.json"
        lengths = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        target = int(tier[:-1]) * 1024
        filler_count = lengths.get(key, round(target / (44 if s["language"] == "en" else 67)))
    if type(filler_count) is not int or not 35 <= filler_count <= len(EN_STEMS) * len(EN_ENDS):
        raise ValueError("calibration requires a background of 35..784 independent records")
    left, rest = s["positive"].split("⟦")
    answer, right = rest.split("⟧")
    decision = left + answer + right if positive else s["negative"]
    names = ("lead", "middle", "decoy", "decision", "tail")
    entries = {int(filler_count * pos): name for name, pos in zip(names, PLACEMENTS[tier, pair_index])}
    assert len(entries) == len(names)
    blocks = [s["title"]]
    cursor = len(s["title"].encode()) + 2
    offsets, gold = {}, []
    for i, background in enumerate(_background(s, key, filler_count)):
        if i in entries:
            name = entries[i]
            body = decision if name == "decision" else s[name]
            end = cursor + len(body.encode("utf-8"))
            offsets[name] = [cursor, end]
            if name == "decision" and positive:
                start = cursor + len(left.encode("utf-8"))
                gold = [[start, start + len(answer.encode("utf-8"))]]
            blocks.append(body)
            cursor = end + 2
        blocks.append(background)
        cursor += len(background.encode("utf-8")) + 2
    relevant = ["middle", "decision"]
    if s["group"] == "cross_reference":
        relevant.insert(0, "lead")
    return {"id": key + ("-p" if positive else "-n"), "pair": key, "pair_index": pair_index,
            "positive": positive, "tier": tier, "group": s["group"], "language": s["language"],
            "filler_count": filler_count, "job": {"task": "answer", "question": s["question"],
                                                 "source": "\n\n".join(blocks)},
            "gold": answer if positive else None, "gold_spans": gold,
            "evidence_spans": [offsets[name] for name in relevant],
            "change_spans": [offsets["decision"]], "context_spans": [offsets[n] for n in ("lead","middle","decoy","tail")]}


def cases(split="targeted"):
    return [make_case(split, tier, i, p) for tier in TIERS for i in range(1,4) for p in (True,False)]
