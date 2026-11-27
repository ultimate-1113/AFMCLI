"""Deterministic Japanese sequential notices; build with cases(), check with validate().

Each packet contains the original notices in chronological order. Only the goal
and packets are model inputs. IDs are local to a document.
"""
import json
import random
from collections import Counter

# An event is (packet number, fractional position in the packet, internal key, original text).
# The internal keys are used only while constructing gold. They are never part of a packet.
SPECS = [
    {
        "id": "notice-s01", "size": "short", "kind": "withdrawn_without_replacement",
        "goal": "貸出係の手順書を直しています。夕映劇場の平日夜公演で、字幕受信機の返却先に指定されている窓口名は何ですか。",
        "value": None, "gold_keys": ("suspend",),
        "places": "衣装倉庫 舞台袖 控室通路 美術室 小道具置場 裁断室",
        "items": "譜面立て 仮設手すり 遮光板 立札台 小型台車 巻尺",
        "roles": "舞台係 衣装係 美術係 用度係 施設係",
        "events": [
            (0, .13, "night", "夕映劇場の貸出係は、平日夜公演の字幕受信機を終演後に「南預かり口」で受け取ることにした。"),
            (0, .78, "day", "昼の親子公演では受信機を客席内で回収する。昼の案内紙には客席係の印を押した。"),
            (1, .27, "proposal", "夜間の返却窓口を灯具受付へ移す案が出た。灯具受付の責任者は開設時間を約束できず、案を差し戻した。"),
            (1, .71, "suspend", "夜間の搬入口工事に伴い、支配人は平日夜公演の字幕受信機について南預かり口の指定を取り消した。後任の窓口は決まっていない。"),
            (2, .18, "different", "土曜マチネの字幕受信機には「西資料口」の返却札が付いた。休日当番が昼間に限って預かる。"),
            (2, .82, "objects", "平日夜の手荷物預かりは東クロークに戻った。ここで扱うのは観客の鞄と傘である。"),
        ],
    },
    {
        "id": "notice-s02", "size": "short", "kind": "prior_decision_stands",
        "goal": "公開温室のニオイヒバ鉢に使う表土材の発注名を教えてください。",
        "value": "粗目軽石", "gold_keys": ("order",),
        "places": "育苗室 散水用具庫 裏庭通路 暖房機室 苗箱棚 資料室",
        "items": "育苗枠 温度計 散水筒 予備の棚板 根鉢台 折り畳み脚立",
        "roles": "栽培係 温室係 造園係 資材係 展示係",
        "events": [
            (0, .16, "trial", "鉢の見本市で赤玉砂を薦められた。苗床だけに試し、公開温室へは持ち込まないことにした。"),
            (0, .54, "order", "園芸主任が公開温室のニオイヒバ鉢の仕様書に署名した。表土材の発注名は「粗目軽石」と記入してある。"),
            (1, .23, "mix", "ニオイヒバの植え替え用土のうち、鉢底の詰め物だけを木炭片に改めた。改定した欄は底材である。"),
            (1, .84, "other", "隣の展示棟のコノテガシワには砕いた杉皮を敷く。別棟の鉢に限った試行で、温室主任の仕様書とは別に扱う。"),
            (2, .53, "board", "公開温室は鉢台の補修を終えた。支柱担当が工事前の番号でニオイヒバ鉢を台に戻した。"),
        ],
    },
    {
        "id": "notice-s03", "size": "short", "kind": "unresolved_applicability",
        "goal": "峠の録音会で使った巡回録音車について、あの日に指定が当てはまった防風幕の展張位置を知りたいです。",
        "value": None, "gold_keys": ("log",),
        "places": "収録室 仮設控所 保管車庫 機材棚 編集室 検聴席",
        "items": "譜面台 養生板 音量計 折畳机 空の巻枠 照明台",
        "roles": "録音係 音響係 車両係 会場係 整備係",
        "events": [
            (0, .27, "north", "峠で録音車を使うとき、山側から風が吹くなら防風幕は「荷室右側」に張る。音響主任が運用表に押印した。"),
            (1, .16, "south", "同じ録音車でも谷側から風が入る場合は「運転席後方」に防風幕を張る。場所は幕の保管棚ではなく展張位置を表す。"),
            (1, .69, "aside", "裏山の中継車は防風幕を車体前面に張った。中継車の記録は峠の録音車とは別の車両日誌に綴じた。"),
            (2, .36, "forecast", "録音会前日の予報では山風だった。主任は当日の実測で位置を選ぶよう担当者に伝えた。"),
            (2, .87, "log", "録音会当日の峠の風向記録は欠測で、幕の配置図も残っていない。車両日誌から山側と谷側のどちらで運用したかは特定できず、展張位置を確定できない。"),
        ],
    },
    {
        "id": "notice-s04", "size": "short", "kind": "superseded_and_replaced",
        "goal": "白炉窯の本焼き当番が使う、排煙口の清掃ブラシの正式な種類は何ですか。",
        "value": "真鍮刷毛", "gold_keys": ("adopt",),
        "places": "成形室 土練り場 乾燥棚 材料庫 検品台 炉外通路",
        "items": "運搬盆 水量計 木製へら 見本台 支持金具 作業灯",
        "roles": "窯場係 成形係 仕上げ係 材料係 整備係",
        "events": [
            (0, .21, "old", "白炉窯の本焼きでは、排煙口の清掃用に馬毛刷毛を道具箱へ入れることになっていた。"),
            (0, .79, "sister", "隣の素焼き窯は火を落とした後に竹箒を使う。本焼き窯と素焼き窯では排煙口の形状が異なる。"),
            (1, .55, "test", "排煙口に煤が残るため、白炉窯の整備係が三種類の刷毛を比較した。この日の日誌には試用順だけを記した。"),
            (2, .25, "adopt", "比較を終え、窯場長が白炉窯本焼き用の清掃要領を改訂した。排煙口のブラシを従来品から「真鍮刷毛」に替え、本日から適用する。"),
            (2, .81, "archive", "道具資料室は古い馬毛刷毛の見本を保存した。使用済みの清掃要領と一緒に展示棚へ収めた。"),
        ],
    },
    {
        "id": "notice-l01", "size": "long", "kind": "superseded_and_replaced",
        "goal": "共同浴場のヒノキ槽を夜間循環するとき、補助配管の継ぎ目に使う封止材の品名は何ですか。",
        "value": "膨張黒鉛板", "gold_keys": ("new",),
        "places": "濾過機室 監視室 休憩棟 点検廊下 清掃車庫 資材小屋",
        "items": "仮設梯子 検温容器 床格子 工具箱 点検鏡 保護カバー",
        "roles": "浴場係 機械係 清掃係 保全係 計測係",
        "events": [
            (0, .17, "old", "共同浴場のヒノキ槽では、夜間循環用の補助配管の継ぎ目を耐熱ゴム板で封じていた。保全係が旧仕様の写しを当直に渡した。"),
            (0, .82, "valve", "主配管の止水弁には青色繊維板を使う。止水弁は補助配管の継ぎ目とは別に整備する。"),
            (1, .51, "new", "機械主任がヒノキ槽の夜間循環用補助配管の改修票を決裁した。継ぎ目の封止材は「膨張黒鉛板」とし、旧仕様を廃止する。"),
            (1, .88, "offer", "業者は薄型の樹脂輪も持参したが、提出された図面は隣の休憩棟の給湯機用だった。"),
            (2, .32, "other", "石張り槽の夜間循環用補助配管には耐熱ゴム板を再採用した。今回の作業票は石張り槽の設備台帳にのみ登録された。"),
            (2, .78, "legacy", "旧式の配管を使う研修架台には古い封止材が残る。研修架台は営業浴槽の系統から切り離してある。"),
        ],
    },
    {
        "id": "notice-l02", "size": "long", "kind": "withdrawn_without_replacement",
        "goal": "月灯天文台の子ども観望会で、第二観測台に最後の組が入場できる締切時刻は何時ですか。",
        "value": None, "gold_keys": ("cancel",),
        "places": "解説準備室 投影室 案内詰所 測器庫 会議棟 写真整理室",
        "items": "床面標識 展示脚 星図筒 照度計 雨よけ板 予備の腰掛け",
        "roles": "観測係 解説係 施設係 資料係 案内係",
        "events": [
            (0, .19, "print", "月灯天文台の子ども観望会では、第二観測台の最終組入場を十九時四十分までと決め、案内札を刷った。"),
            (0, .76, "cancel", "手すりの補修が夜まで延びる。台長は子ども観望会の第二観測台の入場締切を撤回し、代わりの時刻は定めなかった。"),
            (1, .34, "first", "子ども観望会の第一観測台は二十時十分で新規入場を締め切る。第一観測台には段差の補修箇所がない。"),
            (1, .84, "office", "十九時半に閉まるのは解説冊子の引換所である。引換所の時刻を入場整理札に転記しないよう受付係に伝えた。"),
            (2, .26, "general", "翌週の一般向け写真講座では第二観測台の入場締切を二十一時とした。子ども観望会とは別日の講座である。"),
            (2, .79, "paper", "資料係が子ども観望会の初版案内札を一枚保存した。旧札は出入口へ掲示せず資料箱へ移した。"),
        ],
    },
    {
        "id": "notice-l03", "size": "long", "kind": "prior_decision_stands",
        "goal": "霧ノ駅の旧待合室で木製の長椅子を手入れします。座面に用いる正式な仕上げ材の名前を教えてください。",
        "value": "蜜蝋艶消し剤", "gold_keys": ("seat", "confirm"),
        "places": "遺失物庫 改札脇通路 荷捌き場 資料保管室 清掃控室 旧詰所",
        "items": "展示脚 予備の手すり 台秤 運搬板 工具盆 電灯覆い",
        "roles": "駅務係 修繕係 建築係 保存係 受付係",
        "events": [
            (0, .32, "seat", "保存主任は霧ノ駅旧待合室の木製長椅子について、座面の仕上げ材に「蜜蝋艶消し剤」を指定した。"),
            (0, .87, "leg", "同じ長椅子の鋳物脚は鉄部用の黒塗料で仕上げる。木部用の缶と取り違えないよう別の棚に置いた。"),
            (1, .33, "proposal", "修繕業者は座面を亜麻仁油仕上げにする見積書を出した。保存会議は見積書を受け取ったが、試料を展示用合板で試すことだけを認めた。"),
            (1, .80, "other", "新駅舎の待合室の木製長椅子では、座面を無色ウレタン塗料に替えた。こちらは通常の備品で保存会議の管理外にある。"),
            (2, .48, "confirm", "霧ノ駅の保存会議は旧待合室の木製長椅子の座面に従来どおり「蜜蝋艶消し剤」を用いることを再確認し、保全票に署名した。"),
            (2, .91, "back", "長椅子の背に付ける駅史パネルには合板の試料を再利用した。パネルの表面は座面の保全票と別の仕様である。"),
        ],
    },
    {
        "id": "notice-l04", "size": "long", "kind": "unresolved_applicability",
        "goal": "川南自転車工房に戻った青い貸出車で、前輪を直すために指定されるチューブ品番を教えてください。",
        "value": None, "gold_keys": ("unidentified",),
        "places": "車庫通路 工具保管庫 部品発送台 研修作業室 注油台 仮置き場",
        "items": "高さ計 予備のスタンド 清掃用ブラシ 車輪台 部品皿 携帯照明",
        "roles": "修理係 貸出係 検査係 倉庫係 研修係",
        "events": [
            (0, .23, "small", "川南自転車工房の整備表によると、小径枠を残す貸出車は前輪チューブに品番「CT-42K」を使う。"),
            (0, .90, "paint", "貸出車を塗り直した際、小径枠と標準枠の両方に同じ青い塗料を使った。色の呼び名は車種別の台帳には設けない。"),
            (1, .42, "normal", "標準枠に組み替えた貸出車の前輪チューブは品番「LN-68R」と整備表にある。後輪の表にはこの変更を適用しない。"),
            (1, .88, "rear", "後輪チューブの予備は外袋に「MB-11T」と印刷されている。前輪の引当箱とは作業台を挟んで反対側に置いた。"),
            (2, .21, "unidentified", "戻ってきた青い貸出車には枠の銘板も車体番号札もなく、寸法欄も空白だった。小径枠と標準枠のどちらを整備するか特定できず、前輪チューブの品番は未確定である。"),
            (2, .83, "misc", "練習用の別の青い車体は標準枠と分かった。練習車には貸出票を発行しておらず、返却車の写真番号とは一致しない。"),
        ],
    },
]

TEMPLATES = (
    "{place}の{item}を動かす前に{role}が壁との間隔を測った。測定図には入口から見た向きを矢印で添えた。",
    "{role}は{place}で使う{item}の取っ手を点検した。握りに露出した金具がないことを確かめ、革手袋を備品棚に戻した。",
    "{place}に運んだ{item}の底面に擦れが見つかった。{role}が荷受け前の写真と照合し、外観の記録に追記した。",
    "{item}の付属品を{place}の整理棚にまとめた。{role}は棚の奥まで手が届くか確かめ、手前に不用な箱を積まないようにした。",
    "{place}の通路図には以前の{item}の輪郭が残っていた。{role}が今の置き場所を測り、図の写しを管理室へ届けた。",
    "{role}が{place}で{item}を拭くための布を受け取った。使った布は乾かしてから袋を替え、未使用品と離して収めた。",
    "{place}で保管している{item}の脚が床を傷めないか調べた。{role}は保護片の厚みを測り、結果を日常点検の冊子に綴じた。",
    "{item}を運ぶ際の曲がり角を{role}が下見した。{place}の出入口には古い荷札が落ちていたので、回収して通路を空けた。",
    "{place}の棚板を外す作業に合わせて{item}を一時的に下ろした。{role}はねじと座金を蓋付きの小皿にまとめた。",
    "{role}が{item}の梱包を解くと、緩衝紙の間に送り状の控えがあった。{place}の受領簿に控えの枚数を書き添えた。",
    "{place}で{item}を撮影した記録に反射が出ていた。{role}が照明の角度を変え、縁の傷まで見える写真をもう一枚撮った。",
    "{role}と運搬担当が{place}の敷居を調べた。{item}を載せた台車が静かに通れるよう、戸当たりの高さを測り直した。",
    "{item}の収納袋にほころびが見つかった。{role}が{place}で縫い目を補い、袋口のひもが引っ掛からないことも確かめた。",
    "{place}の備品引継ぎで{role}が{item}の所在を確認した。帳面の見開きには見取り図を挟み、奥の棚にも番号札を戻した。",
    "{role}は{place}にある{item}を水平な台に移して傾きを調べた。脚の下に残っていた小石を取り除くとがたつきが収まった。",
    "{place}の片付けで{item}に添えてあった紙袋が見つかった。{role}は中身を確認して、金属片と布切れを別々に保管した。",
    "{item}をしまう前に{role}が{place}の窓を閉めた。棚に残った水滴は拭き取り、午前中の雨染みを平面図に記した。",
    "{place}で{role}が{item}の数を照合した。修理に出しているものと貸出中のものを分けて、棚の手前に一覧表を置き直した。",
)


def _build(spec, ordinal):
    # Packet size and event positions depend only on the prespecified size and order.
    # Use shuffled independent template/place/item/role combinations for unique support-free filler.
    n = (23, 24, 23) if spec["size"] == "short" else (62, 64, 63)
    rng = random.Random(194117 + 1301 * ordinal)
    places = spec["places"].split()
    items = spec["items"].split()
    roles = spec["roles"].split()
    packets, evidence, seen = [], {}, set()
    serial = 0
    for part, count in enumerate(n):
        events = {}
        for dest, position, key, text in spec["events"]:
            if dest == part:
                slot = int((count - 1) * position)
                assert slot not in events and text not in seen
                events[slot] = (key, text)
        packet = []
        for slot in range(count):
            serial += 1
            eid = "E{:03d}".format(serial)
            if slot in events:
                key, text = events[slot]
                evidence[key] = eid
            else:
                for _ in range(200):
                    text = rng.choice(TEMPLATES).format(place=rng.choice(places), item=rng.choice(items), role=rng.choice(roles))
                    if text not in seen:
                        break
                else:
                    raise RuntimeError("exhausted filler combinations")
            seen.add(text)
            packet.append({"id": eid, "text": text})
        packets.append(packet)
    return {
        "id": spec["id"], "size": spec["size"], "goal": spec["goal"],
        "packets": packets,
        "gold": {"value": spec["value"], "evidence": [evidence[k] for k in spec["gold_keys"]]},
        "kind": spec["kind"],
    }


def cases():
    """Return a fresh copy of the eight records, in fixed presentation order."""
    return [_build(spec, i) for i, spec in enumerate(SPECS)]


def validate(panel=None):
    """Check schema, ordering, uniqueness, balance, and literal authority for each settled case."""
    panel = cases() if panel is None else panel
    assert len(panel) == 8 and len({c["id"] for c in panel}) == 8
    assert Counter((c["size"], c["gold"]["value"] is not None) for c in panel) == {
        ("short", True): 2, ("short", False): 2, ("long", True): 2, ("long", False): 2,
    }
    assert Counter(c["kind"] for c in panel) == {
        "prior_decision_stands": 2, "superseded_and_replaced": 2,
        "withdrawn_without_replacement": 2, "unresolved_applicability": 2,
    }
    all_text = set()
    for case in panel:
        assert set(case) == {"id", "size", "goal", "packets", "gold", "kind"}
        assert len(case["packets"]) == 3 and all(case["packets"])
        rows = [r for packet in case["packets"] for r in packet]
        assert all(set(r) == {"id", "text"} and r["text"].strip() == r["text"] and r["text"] for r in rows)
        assert [r["id"] for r in rows] == ["E{:03d}".format(i + 1) for i in range(len(rows))]
        assert all("。" in r["text"] for r in rows)
        by_id = {r["id"]: r["text"] for r in rows}
        assert len(set(by_id.values())) == len(rows)
        assert all_text.isdisjoint(by_id.values())
        all_text.update(by_id.values())
        gold = case["gold"]
        assert set(gold) == {"value", "evidence"} and gold["evidence"]
        assert len(set(gold["evidence"])) == len(gold["evidence"])
        assert all(e in by_id for e in gold["evidence"])
        if gold["value"] is not None:
            assert isinstance(gold["value"], str) and gold["value"]
            assert all(gold["value"] in by_id[e] for e in gold["evidence"])
        else:
            assert all(any(marker in by_id[e] for marker in ("決まっていない", "定めなかった", "特定できず")) for e in gold["evidence"])
    return True


if __name__ == "__main__":
    panel = cases()
    validate(panel)
    print(json.dumps([
        {"id": c["id"], "size": c["size"],
         "packet_chars": [sum(len(r["text"]) for r in p) for p in c["packets"]],
         "packet_rows": [len(p) for p in c["packets"]]}
        for c in panel
    ], ensure_ascii=False, indent=2))
