"""Independent Japanese change notices for the confirmatory four-document probe (Python 3.9)."""
from collections import Counter
from importlib import import_module, util
from pathlib import Path


def _load_assembly():
    # The published panel uses eval/handoff/panel.py. In the authoring checkout
    # both stand-alone panel files are in target/ and can also be loaded directly.
    try:
        return import_module("eval.handoff.panel")
    except ModuleNotFoundError as exc:
        if exc.name not in {"eval", "eval.handoff", "eval.handoff.panel"}:
            raise
    path = Path(__file__).with_name("handoff_panel.py")
    spec = util.spec_from_file_location("_locked_notice_panel", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE = _load_assembly()
SPECS = [
    {
        "id": "notice-x-s01", "size": "short", "kind": "superseded_and_replaced",
        "goal": "浜浦の帆縫い教室で、受講者用の見本帆の上縁の折り返しを押さえます。指定された縫い方は何ですか。",
        "value": "二重返し縫い", "gold_keys": ("change",),
        "places": "製帆室 採寸台 布地庫 針具棚 裁ち場 講師控所",
        "items": "裁ちばさみ 張り枠 布押さえ 反物台 角定規 巻糸盆",
        "roles": "製帆係 裁断係 教材係 講師補助 用具係",
        "events": [
            (0, .19, "old", "浜浦の帆縫い教室では、受講者用見本帆の上縁の折り返しを並縫いで押さえることになっていた。講師が旧手引きの該当箇所を指し示した。"),
            (0, .82, "display", "玄関に飾る青い展示帆は、上縁を巻き縫いにした。展示帆は受講者に配る見本より厚い。"),
            (1, .34, "trial", "手引きの改訂に先立ち、講師は上縁の試し布を二枚作った。片方は軽く、もう片方は繰り返し引いてもほつれにくかった。"),
            (1, .79, "change", "主任講師が受講者用見本帆の手引きを改訂した。上縁の折り返しを押さえる縫い方は「二重返し縫い」に替え、次の配布分から使う。"),
            (2, .25, "active", "改訂後初めて受講者用見本帆を配布した。教材係が古い手引きを教卓から下げ、現行版を各作業机に置いた。"),
            (2, .80, "side", "受講者用見本帆でも横縁のほつれ止めには並縫いを残した。横縁の見本は上縁の試し布と別の台紙に留めた。"),
        ],
    },
    {
        "id": "notice-x-s02", "size": "short", "kind": "unresolved_applicability",
        "goal": "梢町の夏市から戻った白い紙提灯について、胴に合う指定の収納籠の名前を知りたいです。",
        "value": None, "gold_keys": ("missing",),
        "places": "町内会倉庫 飾具置場 夏市詰所 包装台 祭具通路 荷解き場",
        "items": "竹枠 糸巻き台 組立て脚 釘抜き 小さな衝立 持ち運び盆",
        "roles": "祭具係 会場係 町内係 荷受け係 装飾係",
        "events": [
            (0, .30, "round", "梢町夏市の紙提灯は胴の形で収納籠を分ける。丸胴に使う籠の名前は「藤浅籠」で、白い提灯も同じ扱いとする。"),
            (0, .75, "transport", "夏市から運び出す際には、紙提灯をまとめて平箱に寝かせた。この箱は荷車の振動を抑えるための運搬具である。"),
            (1, .48, "square", "祭具係が角胴の紙提灯を「竹深籠」にしまうことを収納帳で確認した。これは角の骨組みに合わせた籠で、色分けはしていない。"),
            (2, .18, "other", "売り場の写真に写っている白い丸胴は隣町から借りた提灯だった。借りた分は売り場から直接返した。"),
            (2, .68, "missing", "梢町に戻った白い紙提灯は胴を外した状態で引き取られ、返却札の形状欄も空白だった。丸胴か角胴か特定できず、収納籠を選べない。"),
        ],
    },
    {
        "id": "notice-x-l01", "size": "long", "kind": "withdrawn_without_replacement",
        "goal": "泉端茶房の試飲会で、焙じ茶入門回に使う湯冷まし器の指定器名を教えてください。",
        "value": None, "gold_keys": ("revoke",),
        "places": "茶器蔵 茶量り台 焙煎室 客席前室 水屋控所 荷受け土間",
        "items": "竹の茶量り 作業盆 汲み桶 整理枠 仮置き台 茶筒受け",
        "roles": "茶器係 試飲係 焙煎係 水屋係 蔵番",
        "events": [
            (0, .21, "signed", "泉端茶房の試飲会の器具帳には、焙じ茶入門回で使う湯冷まし器を「三角片口」とする旨の主催者印があった。"),
            (0, .84, "ceremony", "近くの陶芸会から白磁注器を貸し出せるとの申し出があった。試飲係が見本を受け取ったが、使用回は申し合わせていない。"),
            (1, .37, "revoke", "注ぎ口の欠けが見つかり、主催者は焙じ茶入門回の湯冷まし器から三角片口の指定を取り消した。代わりの器は決まっていない。"),
            (1, .85, "green", "同じ試飲会でも煎茶上級回では白磁注器を湯冷まし器とする。器具帳には煎茶上級回の欄だけに器名を記した。"),
            (2, .49, "repair", "修理した三角片口は形見の器を並べる棚に移された。修理台帳にある返却先は展示棚で、水屋への出庫記録はない。"),
            (2, .86, "water", "焙じ茶入門回に出す常温の飲み水は硝子水差しに入れる。これは席で口をすすぐための水で、茶を淹れる湯とは分けて用意した。"),
        ],
    },
    {
        "id": "notice-x-l02", "size": "long", "kind": "prior_decision_stands",
        "goal": "楡湖の渡船の南桟橋で、車いす利用者が乗船を知らせる呼び鈴の音色名は何ですか。",
        "value": "三連木琴", "gold_keys": ("pier", "confirm"),
        "places": "船具庫 桟橋詰所 案内小屋 整備桟台 待合通路 救命具庫",
        "items": "係船環 長柄ブラシ 検札箱 船縁クッション 桟橋用定規 折畳台",
        "roles": "船務係 桟橋係 機関係 乗船係 保守係",
        "events": [
            (0, .17, "pier", "楡湖渡船の南桟橋では、車いす利用者が押す乗船用呼び鈴の音色名を「三連木琴」とする。桟橋主任が受信器の設定票に署名した。"),
            (0, .77, "north", "北桟橋の車いす用乗船呼び鈴は二連鈴に替えた。北側の待合室は船内放送と音が重なりやすい。"),
            (1, .28, "ordinary", "南桟橋の売店で係を呼ぶ卓上ベルを単音鐘に交換した。売店のベルは乗船口の受信器と配線を共有しない。"),
            (1, .73, "proposal", "南桟橋の乗船用呼び鈴にも別の音を試したいとの意見があった。保守係は事務室の試験器だけで聞き比べを行った。"),
            (2, .40, "confirm", "試聴後の点検で桟橋主任は南桟橋の車いす用乗船呼び鈴を変更せず、現在の音色名「三連木琴」を保守票に転記した。"),
            (2, .81, "ship", "渡船の船内で下船介助を知らせる合図を低音笛にした。船内合図の送り先は船尾の乗務員室である。"),
        ],
    },
]


def cases():
    """Generate the new cases only. All originals and filler follow the locked assembly."""
    return [_BASE._build(spec, i + 21) for i, spec in enumerate(SPECS)]


def validate(panel=None):
    panel = cases() if panel is None else panel
    assert len(panel) == len({c["id"] for c in panel}) == 4
    assert Counter((c["size"], c["gold"]["value"] is not None) for c in panel) == {
        ("short", True): 1, ("short", False): 1, ("long", True): 1, ("long", False): 1,
    }
    assert len({c["kind"] for c in panel}) == 4
    original = _BASE.cases()
    assert {c["id"] for c in original}.isdisjoint({c["id"] for c in panel})
    existing = {r["text"] for c in original for packet in c["packets"] for r in packet}
    for c in panel:
        assert len(c["packets"]) == 3 and all(c["packets"])
        rows = [r for packet in c["packets"] for r in packet]
        assert [r["id"] for r in rows] == ["E{:03d}".format(i + 1) for i in range(len(rows))]
        assert all(set(r) == {"id", "text"} and r["text"].strip() for r in rows)
        text = {r["text"] for r in rows}
        assert len(text) == len(rows) and existing.isdisjoint(text)
        existing.update(text)
        by_id = {r["id"]: r["text"] for r in rows}
        gold = c["gold"]
        assert gold["evidence"] and set(gold["evidence"]) <= set(by_id)
        if gold["value"] is not None:
            assert all(gold["value"] in by_id[k] for k in gold["evidence"])
        else:
            assert all("特定できず" in by_id[k] or "決まっていない" in by_id[k] for k in gold["evidence"])
    return True


if __name__ == "__main__":
    validate()
