"""Frozen synthetic EN–JA notices and a model-independent, sentence-local scorer.

The source notices are authored individually. The reference and corruption generators serve
as scorer controls and are never part of the public translation job. Run this file to run
those controls offline. Gold and source are JSON serializable.
"""
from collections import Counter
import json
import re
import unicodedata


# Each document has a distinct setting. Its metadata declares the two meanings before
# seeing a model response. A leading ! on an action denotes a prohibition over a window.
DEV = """
bookbinding|InkNest|paper margin/用紙余白/ge/12/mm|sample binder/見本帳|proof review/校正会|close|before|0.5
InkNest requires a paper margin of at least 12 mm.
Close the sample binder 0.5 h before the proof review.

ceramic tiles|SoraKiln|tile width/タイル幅/lt/9/cm|display case/展示ケース|glaze check/釉薬確認|!open|after|2
SoraKilnでは、タイル幅を9 cm未満にしてください。
釉薬確認のあと2 hは、展示ケースを開けないでください。

seed exchange|SprigBox|packet weight/小袋重量/le/45/g|seed drawer/種子引き出し|seed count/種子集計|open|after|1
SprigBox caps the packet weight at 45 g.
Once the seed count is done, wait 1 h before opening the seed drawer.

trail mapping|MichiMap|map width/地図幅/gt/25/cm;legend width/凡例幅/le/8/cm|map lamp/地図灯|route briefing/経路説明|on|before|1.5
MichiMapの地図幅は25 cmを超え、凡例幅は8 cm以下とする。
経路説明の1.5 h前に地図灯を点灯する。

portrait booth|LinoFace|backdrop height/背景幕高/ge/1.8/m|booth light/撮影灯|first portrait/初回撮影|!off|before|1
LinoFace sets the minimum backdrop height at 1.8 m.
Leave the booth light on during the 1 h leading up to the first portrait.

hand weaving|AyaLoom|linen length/麻糸長/le/80/cm;wool length/毛糸長/ge/35/cm|loom cover/織機カバー|thread check/糸確認|close|after|0.5
AyaLoomでは麻糸長を80 cm以下、毛糸長を35 cm以上にそろえます。
糸確認から0.5 hたったら、織機カバーを閉じてください。

bread tasting|LoafBay|slice thickness/試食片厚/lt/18/mm|tasting box/試食箱|first tasting/初回試食|!close|after|1
At LoafBay, keep the slice thickness under 18 mm.
Do not close the tasting box during the 1 h after the first tasting.

choir practice|UtaWing|folder count/譜面数/gt/6/-|pitch track/音取り音源|roll call/点呼|start|before|0.25
UtaWingは譜面数を6より多くするよう求めています。
点呼の0.25 h前に音取り音源の再生を開始してください。

stamp album|PageMint|border width/枠線幅/ge/2/mm;caption width/見出し幅/lt/16/mm|album lid/アルバムふた|album viewing/アルバム鑑賞|open|before|0.5
PageMint needs a border width of 2 mm or more and a caption width under 16 mm.
The album lid should be opened 0.5 h ahead of the album viewing.

kite making|KiteNori|tail length/尾の長さ/le/3/m|work light/作業灯|kite launch/凧揚げ|off|after|1
KiteNoriで使う尾の長さは最大3 mです。
凧揚げの1 h後に作業灯を消してください。

board game club|PipDesk|token count/駒数/gt/15/-|game clock/対局時計|final move/最終手|stop|after|0.25
PipDesk sets the token count higher than 15.
Stop the game clock 0.25 h after the final move.

planetarium|HoshiArc|slide duration/投影時間/lt/40/s|dome lights/ドーム照明|star talk/星空解説|!on|before|0.5
HoshiArcの投影時間は40 sに満たない長さにします。
星空解説の前の0.5 hはドーム照明をつけないでください。
"""


TEST = """
paper marbling|MaruDip|tray depth/トレー深さ/lt/7/cm;paddle width/へら幅/ge/3/cm|ink cupboard/インク戸棚|pattern reveal/模様発表|close|before|0.5
At MaruDip the tray depth has to stay below 7 cm, while the paddle width must be at least 3 cm.
Please shut the ink cupboard 0.5 h before the pattern reveal.

bird recording club|ToriTape|clip duration/音声長/le/24/s|playback lamp/再生灯|dawn count/早朝集計|!off|after|1
ToriTapeの音声長の上限は24 sです。
早朝集計が済んでから1 hは再生灯を消さないでください。

miniature railway|TinLoop|platform length/ホーム長/gt/36/cm|model gate/模型門|first circuit/初回周回|open|after|0.25
TinLoop calls for a platform length greater than 36 cm.
The model gate is to open 0.25 h after the first circuit.

shadow theatre|KageWing|screen width/投影幕幅/ge/1.6/m;wing width/袖幕幅/lt/0.8/m|shadow lamp/影絵灯|curtain call/カーテンコール|off|after|0.5
KageWingの投影幕幅は1.6 m以上とし、袖幕幅は0.8 m未満にする。
カーテンコールから0.5 hたった時点で影絵灯を消灯する。

fountain pen meetup|NibQuay|ink volume/インク量/le/15/mL|bottle cap/瓶ふた|swatch check/色見本確認|!open|before|1
NibQuay limits the ink volume to no more than 15 mL.
Keep the bottle cap closed for the 1 h before the swatch check.

public chess mural|MokuTile|light square width/白マス幅/gt/18/cm;dark square width/黒マス幅/le/22/cm|paint chest/塗料箱|wall unveiling/壁画披露|close|after|2
MokuTileでは白マス幅が18 cmを超え、黒マス幅が22 cm以下となるようにします。
壁画披露の2 h後に塗料箱を閉じてください。

ceremonial clock|BellIvy|chime duration/鐘音長/lt/12/s|chime track/鐘音音源|noon signal/正午合図|start|before|0.25
BellIvy requires the chime duration to be less than 12 s.
Start the chime track 0.25 h ahead of the noon signal.

community jigsaw|PieceZen|piece count/ピース数/ge/75/-|puzzle drawer/パズル引き出し|group photo/集合写真|!close|before|0.5
PieceZenではピース数の下限を75と定めました。
集合写真の直前の0.5 hはパズル引き出しを閉めずにおいてください。

canal postcard kiosk|CanalDot|card height/はがき高/le/14/cm;card width/はがき幅/gt/9/cm|kiosk shutter/売店シャッター|boat greeting/船の出迎え|open|before|1
CanalDot accepts a card height of up to 14 cm and a card width over 9 cm.
Open the kiosk shutter 1 h prior to the boat greeting.

origami club|OriTown|fold depth/折り込み深さ/lt/8/mm|paper cabinet/紙棚|folding demo/折り実演|!open|after|1.5
OriTownでは折り込み深さが8 mmに達してはいけません。
折り実演後の1.5 hは紙棚を開けないようにしてください。

pin badge swap|PinYard|badge count/バッジ数/gt/12/-|swap table lamp/交換卓ランプ|badge draw/バッジ抽選|on|after|0.5
PinYard needs a badge count of more than 12.
Turn on the swap table lamp 0.5 h after the badge draw.

coffee label design|MameMark|label height/ラベル高/ge/32/mm;label width/ラベル幅/le/68/mm|label printer/ラベルプリンター|design vote/図案投票|stop|before|1
MameMarkではラベル高を少なくとも32 mm、ラベル幅を最大68 mmとする。
図案投票に先立ち、その1 h前にラベルプリンターを停止してください。

windowbox flowers|PetalCove|pot depth/鉢深さ/lt/16/cm|garden chest/園芸箱|flower judging/花の審査|!close|after|2
PetalCove sets a pot depth strictly under 16 cm.
For the 2 h following the flower judging, leave the garden chest open.

magnet workshop|MagneHana|magnet weight/磁石重量/le/25/g|supply case/備品ケース|magnet count/磁石集計|close|before|0.25
MagneHanaの磁石重量は25 gを超えてはいけません。
磁石集計の0.25 h前に備品ケースを閉めておいてください。

lantern parade|GlowPath|handle length/持ち手長/gt/12/cm;cord length/ひも長/lt/45/cm|entry lantern/入口灯|parade salute/パレード合図|!off|before|1
GlowPath requires a handle length longer than 12 cm and a cord length shorter than 45 cm.
During the 1 h leading up to the parade salute, do not switch off the entry lantern.

comic lending shelf|KomaBay|loan period/貸出期間/ge/48/h|return slot/返却口|stock count/冊数確認|open|after|1.5
KomaBayの貸出期間は48 hを下回らないようにします。
冊数確認の1.5 h後になったら返却口を開けてください。

vintage radio exhibit|DialMoss|dial diameter/目盛盤径/le/11/cm|display radio/展示ラジオ|sound cue/音の合図|off|before|0.5
At DialMoss, the dial diameter may be 11 cm at most.
Switch off the display radio 0.5 h before the sound cue.

stained glass class|IroPane|blue strip width/青帯幅/lt/28/mm;amber strip width/琥珀帯幅/ge/16/mm|glass drawer/ガラス引き出し|color review/配色確認|!open|before|1
IroPaneでは青帯幅を28 mm未満に抑え、琥珀帯幅を16 mm以上確保する。
配色確認前の1 hの間はガラス引き出しを開けないこと。

crossword meetup|GridFern|entry count/解答数/ge/14/-|puzzle timer/出題タイマー|clue reading/ヒント朗読|start|after|0.25
GridFern sets the minimum entry count at 14.
Wait 0.25 h after the clue reading, then start the puzzle timer.

community conker display|KuriTray|tray width/盆幅/gt/22/cm|display lid/展示ふた|chestnut weighing/木の実計量|close|after|0.5
KuriTrayでは盆幅を22 cmより広く取ります。
木の実計量が終わって0.5 h後に、展示ふたを閉じてください。

weather journal circle|SkyLedger|note length/記録長/le/90/mm;title length/題名長/lt/32/mm|reading light/読書灯|cloud sketch/雲の写生|!on|after|1
SkyLedger permits a note length of 90 mm or less and a title length below 32 mm.
Do not turn the reading light on for 1 h after the cloud sketch.

wool dye display|SumiDye|swatch mass/布見本質量/lt/18/g|swatch cabinet/布見本棚|color count/色数集計|open|before|1.5
SumiDyeで扱う布見本質量は18 gより軽くします。
色数集計まであと1.5 hとなったら布見本棚を開いてください。

clay token auction|ClayBid|bid count/入札数/gt/9/-|auction clock/競売時計|closing bid/最終入札|stop|after|0.5
ClayBid needs the bid count to exceed 9.
Stop the auction clock once 0.5 h has passed since the closing bid.

lakeside sketchbooks|MizuLeaf|cover width/表紙幅/ge/19/cm;spine width/背幅/le/4/cm|drawing chest/画材箱|sketch review/素描講評|!close|after|2
MizuLeafの表紙幅は最低19 cm、背幅は最大4 cmです。
素描講評に続く2 hは画材箱を閉じないでください。

ribbon rosettes|LoopAsh|ribbon width/リボン幅/lt/26/mm|ribbon drawer/リボン引き出し|rosette count/飾章集計|close|before|0.5
LoopAsh specifies a ribbon width under 26 mm.
With 0.5 h to go until the rosette count, close the ribbon drawer.

tea caddy exhibition|ChaDock|caddy volume/茶筒容量/le/240/mL|cupboard door/戸棚扉|tea selection/茶葉選定|!open|before|1
ChaDockの茶筒容量は240 mLまでです。
茶葉選定の前の1 hは戸棚扉を開けたままにせず、閉じた状態を保ってください。

quilt photography|PatchSun|border length/外周長/ge/80/cm;seam length/縫い目長/gt/14/cm|studio lamp/スタジオ灯|quilt portrait/キルト撮影|on|before|0.25
For PatchSun the border length must be no less than 80 cm, and the seam length must exceed 14 cm.
Switch the studio lamp on 0.25 h before the quilt portrait.

music box repair cafe|OtoCog|melody duration/旋律長/gt/16/s|test melody/試験旋律|spring check/ばね確認|start|after|0.5
OtoCogでは旋律長が16 sを上回る必要があります。
ばね確認から0.5 h経過したら試験旋律を始めます。

beach shell display|ShellVale|shell diameter/貝殻径/le/38/mm|glass cover/ガラスカバー|shell tally/貝殻集計|!close|before|1.5
ShellVale allows a shell diameter no greater than 38 mm.
For the 1.5 h before the shell tally, keep the glass cover open.

letterpress class|KappanCo|type height/活字高/lt/25/mm;line width/行幅/ge/60/mm|type drawer/活字引き出し|proof reading/試し刷り確認|open|after|1
KappanCoは活字高を25 mm未満、行幅を60 mm以上と定めています。
試し刷り確認の1 h後に活字引き出しを開けます。

community dance rehearsal|StepIris|track duration/曲の長さ/gt/45/s|rehearsal track/練習音源|first bow/初回のお辞儀|stop|before|0.25
StepIris requires a track duration above 45 s.
The rehearsal track must stop 0.25 h before the first bow.

wooden birdhouse fair|SuNest|entry diameter/入口径/ge/28/mm|judging box/審査箱|fair vote/会場投票|!open|after|0.5
SuNestでは入口径を少なくとも28 mm確保する。
会場投票が終わったあとの0.5 hは審査箱を開けないでください。

retro pinball league|FlipElm|warmup duration/練習時間/lt/30/min;round duration/ラウンド時間/le/55/min|score lamp/得点灯|score reset/得点リセット|off|after|1
FlipElm calls for a warmup duration below 30 min and a round duration no longer than 55 min.
Turn the score lamp off 1 h after the score reset.

library umbrella stand|KasaNook|rack width/傘立て幅/le/65/cm|storage door/保管扉|umbrella tally/傘の集計|close|before|0.5
KasaNookの傘立て幅は広くても65 cmまでです。
傘の集計より0.5 h早く保管扉を閉じてください。

laneway chalk art|ChalkPond|chalk length/チョーク長/ge/7/cm|chalk box/チョーク箱|chalk drawing/路上描画|!close|after|1
ChalkPond asks for a chalk length of 7 cm or more.
Keep the chalk box open throughout the 1 h following the chalk drawing.

felt puppet show|FuwaFig|head width/頭幅/gt/9/cm;hand width/手幅/lt/5/cm|stage lamp/舞台灯|puppet greeting/人形挨拶|on|before|1.5
FuwaFigの頭幅は9 cmを超え、手幅は5 cm未満でなければなりません。
人形挨拶の1.5 h前になったら舞台灯をつけます。

vinyl listening hour|GrooveDock|sample duration/試聴時間/le/40/s|record player/レコードプレーヤー|sleeve reveal/ジャケット披露|start|after|0.25
GrooveDock sets an upper limit of 40 s on the sample duration.
Start the record player 0.25 h after the sleeve reveal.

public word mosaic|MojiRill|tile count/文字タイル数/lt/64/-|letter chest/文字箱|word reveal/言葉の発表|!open|before|2
MojiRillでは文字タイル数を64より少なくしてください。
言葉の発表に先立つ2 hは文字箱を開けないでください。

sailing flag collection|PennantOak|flag height/旗高/ge/32/cm;flag width/旗幅/le/50/cm|flag cabinet/旗戸棚|flag salute/旗への敬礼|open|before|0.5
PennantOak needs a flag height of at least 32 cm and a flag width of at most 50 cm.
Open the flag cabinet when there is 0.5 h left before the flag salute.

spinning top contest|KomaGlen|spin duration/回転時間/gt/19/s|contest clock/競技時計|last spin/最終回転|stop|after|0.25
KomaGlenでは回転時間が19 sより長いことを条件としています。
最終回転が済んで0.25 h後に競技時計を止めてください。
"""


def words(pair):
    en, ja = pair.split("/")
    return {"en": en, "ja": ja}


def make(split):
    """Return fresh JSON-compatible cases in a stable order, with a public job and private gold."""
    paragraphs = {"dev": DEV, "test": TEST}[split].strip().split("\n\n")
    cases = []
    for index, paragraph in enumerate(paragraphs):
        header, first, second = paragraph.splitlines()
        family, name, specs, asset, event, verb, when, hours = header.split("|")
        language, target = ("ja", "en") if index % 2 else ("en", "ja")
        rules = []
        for spec in specs.split(";"):
            en, ja, op, number, unit = spec.split("/")
            rules.append(dict(term={"en": en, "ja": ja}, op=op, number=number,
                              unit=unit if unit != "-" else ""))
        gold = dict(family=family, name=name, rules=rules, asset=words(asset), event=words(event),
                    verb=verb.lstrip("!"), window=verb.startswith("!"), when=when, hours=hours)
        terms = [rule["term"] for rule in rules] + [gold["asset"], gold["event"]]
        public = dict(source_language=language, target_language=target, sentences=[first, second],
                      glossary={t[language]: t[target] for t in terms}, names=[name],
                      units=list(dict.fromkeys([r["unit"] for r in rules if r["unit"]] + ["h"])))
        cases.append(dict(id=f"translation-{split}-{index+1:02}", language=language,
                          job={"task": "translate", "input": public}, gold=gold))
    return cases


def norm(text):
    return unicodedata.normalize("NFKC", text).replace("−", "-").replace("’", "'")


NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
NUMBERS = re.compile(NUMBER)
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def term_pattern(term):
    t = norm(term)
    left = r"(?<![A-Za-z])" if t[:1].isascii() and t[:1].isalpha() else ""
    right = r"(?![A-Za-z])" if t[-1:].isascii() and t[-1:].isalpha() else ""
    return left + r"\s+".join(map(re.escape, t.split())) + right


def quantity(number, unit):
    end = r"(?![A-Za-z])" if unit and unit[-1:].isascii() and unit[-1:].isalpha() else ""
    return r"(?<![\d.])" + re.escape(number) + r"(?!\d|\.\d)" + (
        r"\s*" + re.escape(unit) + end if unit else "")


def occurrences(pattern, text, case=True):
    return len(re.findall(pattern, text, 0 if case else re.I))


def literals(job, source, target):
    source, target = norm(source), norm(target)
    nums = Counter(NUMBERS.findall(source)) == Counter(NUMBERS.findall(target))
    units = True
    for symbol in job["units"]:
        token = term_pattern(symbol)
        bound = rf"({NUMBER})\s*{re.escape(symbol)}(?![A-Za-z])"
        units &= occurrences(token, source) == occurrences(token, target)
        units &= Counter(re.findall(bound, source)) == Counter(re.findall(bound, target))
    names = all(occurrences(term_pattern(name), source) == occurrences(term_pattern(name), target)
                and occurrences(term_pattern(name), target) == occurrences(term_pattern(name), target, False)
                for name in job["names"])
    terms = all(occurrences(term_pattern(src), source, False) == occurrences(term_pattern(tgt), target, False)
                for src, tgt in job["glossary"].items())
    return dict(numbers=nums, units=units, names=names, glossary=terms)


# ~ is exactly one selected value-unit pair. Other numeric pairs in the same sentence
# remain distinct. Inclusive phrases subsume shorter strict substrings at the same value.
EN_PREFIX = {
    "le": r"(?:at (?:the )?most|up to(?: and including)?|(?:at )?(?:a |the )?max(?:imum)?(?: (?:of|is|at|to))?|(?:upper limit|cap)(?: (?:of|is|at))?|no (?:more|greater|higher|larger|wider|longer|heavier) than|not (?:more|greater|higher|larger|wider|longer|heavier) than|(?:must not|may not|cannot|can't|does not|do not|should not|shall not|never|not to|not) (?:be (?:over|above)|exceed|surpass|go (?:over|above))|(?:less than|below) or equal to|(?:limited|capped|restricted) (?:at|to))",
    "ge": r"(?:at (?:the )?least|(?:at )?(?:a |the )?min(?:imum)?(?: (?:of|is|at|to))?|(?:lower limit|floor)(?: (?:of|is|at))?|no (?:less|fewer|lower|smaller|narrower|shorter|lighter) than|not (?:less|fewer|lower|smaller|narrower|shorter|lighter) than|(?:must not|may not|cannot|can't|does not|do not|should not|shall not|never|not to|not) (?:be (?:under|below)|(?:fall|drop|go) (?:under|below))|(?:greater than|above) or equal to)",
    "lt": r"(?:(?:strictly )?(?:under|below|less than|fewer than|lower than|smaller than|narrower than|shorter than|lighter than)|(?:fall|falls|remain|remains|stay|stays) (?:under|below)|(?:must not|cannot|not) reach)",
    "gt": r"(?:(?:strictly )?(?:over|above|more than|greater than|higher than|larger than|wider than|longer than|heavier than)|exceed(?:s|ing)?|surpass(?:es|ing)?)",
    "eq": r"(?:equal(?:s| to)?|exactly|precisely|fixed at)",
}
EN_SUFFIX = {
    "le": r"(?:or (?:less|fewer|lower|smaller|shorter|narrower|below)|at (?:the )?most|max(?:imum)?|or (?:under|less than (?:that|this)))",
    "ge": r"(?:or (?:more|greater|higher|longer|larger|wider|above)|at (?:the )?least|min(?:imum)?|or (?:over|more than (?:that|this)))",
    "lt": r"(?:is (?:too (?:much|many|long|wide|heavy)|the first (?:disallowed|excluded) value)|is excluded from the upper (?:limit|bound))",
    "gt": r"(?:is excluded from the lower (?:limit|bound))",
    "eq": r"(?:exactly|precisely|on the dot)",
}
JA_PREFIX = {
    "le": r"(?:最大(?:でも|で|は|値は)?|多く(?:ても|て)|長くても|広くても|高くても|重くても|上限(?:値)?(?:は|が|を)?)",
    "ge": r"(?:最低(?:でも|で|は|値は)?|最小(?:でも|で|は|値は)?|少なく(?:とも|て(?:も)?)|短くても|軽くても|下限(?:値)?(?:は|が|を)?)",
    "lt": r"(?:厳密に下回る値として)", "gt": r"(?:厳密に上回る値として)",
    "eq": r"(?:ちょうど|丁度|正確に|ぴったり)",
}
JA_SUFFIX = {
    "le": r"(?:以下|以内|まで|(?:を|が)(?:最大|上限|限度)|(?:を)?(?:超え|越え|上回)(?:ない|ず|ぬ|ては(?:いけ|なら|なり)|てい(?:け|か)|らない|っては(?:いけ|なら))|より(?:も)?(?:大きく|広く|長く|多く|重く)な(?:い|く))",
    "ge": r"(?:以上|(?:を|が)(?:最低|最小|下限)|(?:を)?下回(?:らない|らず|らぬ|っては(?:いけ|なら))|を(?:切らない|割らない)|より(?:も)?(?:小さく|狭く|短く|少なく|軽く)な(?:い|く))",
    "lt": r"(?:未満|(?:には?|に)(?:届か|満た)な|(?:に)?達しては(?:いけ|なら)|(?:を)?下回|(?:を)?切(?:る|り)|より(?:も)?(?:小さ|少な|低|短|狭|軽)|を下限としない)",
    "gt": r"(?:(?:を|より(?:も)?)?(?:超え|超過|超す|超(?![え過す])|越え|上回)|より(?:も)?(?:大き|多|高|長|広|重))",
    "eq": r"(?:ちょうど|丁度|ぴったり|に固定|に等し)",
}
OPPOSITE = {"le":"gt", "ge":"lt", "lt":"ge", "gt":"le"}
STRICT_FLIP = {"le":"lt", "lt":"le", "ge":"gt", "gt":"ge"}


def threshold(text, rule, language):
    marked = re.sub(quantity(rule["number"], rule["unit"]), "~", text)
    if marked.count("~") != 1:
        return set()
    before, after = (EN_PREFIX, EN_SUFFIX) if language == "en" else (JA_PREFIX, JA_SUFFIX)
    matches = []
    for op in before:
        left = r"\b" if language == "en" else ""
        right = r"\b" if language == "en" else ""
        patterns = [left + before[op] + r"\s*~", r"~\s*" + after[op] + right]
        if language == "en" and op in ("le", "ge"):
            # 'sets the maximum tray length at 12' and 'limits the tray length to 12'.
            lead = r"(?:maximum|upper limit|cap)" if op == "le" else r"(?:minimum|lower limit|floor)"
            patterns.append(r"\b" + lead + r"\b(?:\s+[a-z][\w'-]*){0,5}\s+(?:at|of|is|to)\s*~")
            if op == "le":
                patterns.append(r"\b(?:caps?|limits?|restricts?)\s+(?:[a-z][\w'-]*\s+){1,5}(?:at|to)\s*~")
        if op != "eq":
            symbol = {"le":r"(?:<=|≤)", "ge":r"(?:>=|≥)", "lt":r"<", "gt":r">"}[op]
            patterns.append(symbol + r"\s*~")
        for pattern in patterns:
            matches.extend((op, m.start(), m.end()) for m in re.finditer(pattern, marked, re.I))
    if language == "en":
        complement = {"ge":r"(?:less|fewer|lower|smaller|narrower|shorter|lighter) than|under|below",
                      "le":r"(?:more|greater|higher|larger|wider|longer|heavier) than|over|above",
                      "lt":r"at least", "gt":r"at most"}
        for op, phrase in complement.items():
            pat = r"\b(?:not|never|cannot|can.t)(?:\s+be)?\s+(?:" + phrase + r")\s*~"
            matches.extend((op, m.start(), m.end()) for m in re.finditer(pat, marked, re.I))
    observed = set()
    for op, a, b in matches:
        if any(c <= a and b <= d and (a, b) != (c, d) for _, c, d in matches):
            continue
        if language == "ja" and re.match(r"(?:では(?:ない|なく|ありません)|で(?:あって)?は(?:いけ|なら|なりません)|にしては(?:いけ|なら))", marked[b:]):
            op = OPPOSITE.get(op, "negated")
        if language == "en" and re.search(r"\b(?:not (?:required|necessary|mandatory) to|(?:not|n't) have to)\s+(?:be\s+)?$", marked[:a], re.I):
            op = "optional"
        observed.add(op)
    return observed


def metric_meaning(text, gold, language):
    text = norm(text)
    roles = []
    pairs = []
    all_right, contradicted = True, False
    for i, rule in enumerate(gold["rules"]):
        term = re.search(term_pattern(rule["term"][language]), text, re.I)
        value = re.search(quantity(rule["number"], rule["unit"]), text)
        if term and value:
            roles.append((term.start(), i))
            pairs.append((value.start(), i))
        found = threshold(text, rule, language)
        all_right &= found == {rule["op"]} and bool(term and value)
        contradicted |= bool(found - {rule["op"]})
    # This allows reordering complete clauses, both postposed predicates, and a natural
    # 'respectively'. Exchanging two quantities or two terms changes their association.
    swapped = [i for _, i in sorted(roles)] != [i for _, i in sorted(pairs)]
    return bool(all_right and not swapped), contradicted, swapped


ACTION = {
    "en": {
        "open": r"\b(?:open(?:s|ed|ing)?|unseal(?:s|ed|ing)?|unlock(?:s|ed|ing)?)\b",
        "close": r"\b(?:clos(?:e|es|ed|ing)|shut(?:s|ting)?|seal(?:s|ed|ing)?)\b",
        "start": r"\b(?:start(?:s|ed|ing)?|begin(?:s|ning)?|began|commenc(?:e|es|ed|ing)|launch(?:es|ed|ing)?|play(?:s|ed|ing)?|initiat(?:e|es|ed|ing))\b",
        "stop": r"\b(?:stop(?:s|ped|ping)?|halt(?:s|ed|ing)?|end(?:s|ed|ing)?|finish(?:es|ed|ing)?|shut(?:s|ting)?\s+down|paus(?:e|es|ed|ing)|discontinu(?:e|es|ed|ing))\b",
        "on": r"\b(?:(?:turn(?:s|ed|ing)?|switch(?:es|ed|ing)?|power(?:s|ed|ing)?)\s+(?:(?:the |a )?@\s+)?on|light(?:s|ing)?(?:\s+up)?|lit|activat(?:e|ed|es|ing)|on)\b",
        "off": r"\b(?:(?:turn(?:s|ed|ing)?|switch(?:es|ed|ing)?|power(?:s|ed|ing)?)\s+(?:(?:the |a )?@\s+)?off|put(?:s|ting)?\s+out|extinguish(?:es|ed|ing)?|off|unlit)\b",
    },
    "ja": {
        "open": r"開[けきくいかこ]|開(?:放|封|扉)|ひら[いきくか]|あけ|オープン",
        "close": r"閉[めじまざ鎖扉]|締[めま]|とじ|しめ|クローズ",
        "start": r"開始|始[めま]|始動|起動|スタート|流[しすさ]|再生(?:して|する|を始め)",
        "stop": r"止[めま]|停止|中断|休止|終了|終え|とめ|ストップ|切[るっら]",
        "on": r"点[け灯]|つけ|入(?:れ|れる)|オン|(?<!消)灯[すしさ]|点[いきく]|作動|起動",
        "off": r"消[灯しすさせえ]|切[るっら]|オフ|落[とち]",
    },
}
OPPOSED_VERB = {"open":"close", "close":"open", "start":"stop", "stop":"start", "on":"off", "off":"on"}


def mask_event(text, gold, language):
    masked = re.sub(term_pattern(gold["asset"][language]), "@", norm(text), flags=re.I)
    masked = re.sub(term_pattern(gold["event"][language]), "#", masked, flags=re.I)
    return re.sub(quantity(gold["hours"], "h"), "~", masked).lower()


def predicate(text, position, lang):
    """Classify negation on this predicate. Japanese affirmative obligations contain ない."""
    start, end = position
    if lang == "ja":
        after = re.split(r"[、,。.!?]|[;；]|(?:ただし|一方)", text[end:], maxsplit=1)[0][:38]
        after = re.sub(r"(?:なければ|なくては|ねば|なけりゃ)(?:なら(?:な(?:い|く)|ず)|なりません|いけ(?:な(?:い|く)|ません))", "義務", after)
        no = re.search(r"ない|な[かく]|ぬ|禁止|不可|禁じ|禁ず|ません|せず|らず|めず|けず|じず|さず|けてはだめ|てはだめ|ことなく|るな(?:\s|$)|けるな(?:\s|$)|ず(?:に|[\s、,。]|$)", after)
        return bool(no)
    left = re.split(r"[;.!?,]|\b(?:but|while|then|however)\b", text[:start])[-1][-55:]
    right = re.split(r"[;.!?,]|\b(?:but|while|then|however)\b", text[end:])[0][:55]
    # A temporal restriction ('not later than') does not negate the verb.
    left = re.sub(r"\b(?:not|no) (?:earlier|later) than\b", "", left)
    return bool(re.search(r"\b(?:never|not|avoid(?:ed|ing|s)?|without|no|cannot|forbid(?:den)?|prohibit(?:ed)?)\b|n['’]t\b", left)
                or re.search(r"^\s*(?:(?:the |a )?@\s*)?(?:(?:is|are|shall be|must be|should be)\s+)?(?:prohibited|forbidden|not (?:allowed|permitted)|is not allowed)\b", right))


def event_action(marked, gold, language):
    # The named event and object have already been masked, so an event containing
    # 'closing' or '開始' cannot satisfy the action on the object by itself.
    verb, other = gold["verb"], OPPOSED_VERB[gold["verb"]]
    if language == "en":
        marked = re.sub(r"#\s+(?:(?:has|had|is|was|will|has been)\s+)?(?:end(?:ed|s)?|start(?:ed|s)?|beg(?:ins?|an|un)|is over|finishe[ds]|is done)\b", "#", marked)
        marked = re.sub(r"(\b(?:stop(?:s|ped|ping)?|halt(?:s|ed|ing)?))\s+playing\b", r"\1", marked)
    state_pattern = (r"\b(?:keep|keeps|kept|leave|leav(?:es|ing)|left|maintain(?:s|ed)?)\s+(?:(?:the|a)\s+)?@|@\s+(?:(?:must|should|shall|is to|will|has to)\s+)?(?:remain(?:s|ed)?|stay(?:s|ed)?)\b"
                     if language == "en" else r"まま|状態|保[ちつっ]|維持|続け|開放(?:を|状態)?保|閉鎖(?:を|状態)?保")
    good, opposed = False, False
    for action in (verb, other):
        for match in re.finditer(ACTION[language][action], marked, re.I):
            neg = predicate(marked, match.span(), language)
            state = bool(re.search(state_pattern, marked))
            if gold["window"]:
                if action == verb:
                    ok = neg
                else:
                    ok = not neg and state
                bad = action == verb and not neg or action == other and neg
            else:
                ok = action == verb and not neg and not state
                bad = action == other and not neg or action == verb and neg
            good |= ok
            opposed |= bad
    return bool(good and not opposed and "@" in marked), bool(opposed)


EN_TIME = {
    "before": r"before|prior to|ahead of|in advance of|(?:leading|lead) up to|preceding|earlier than|(?:remaining |left |to go )?until",
    "after": r"after|following|since|subsequent to|later than|on completion of|upon completion of|once",
}
JA_TIME = {"before":r"直前|以前|前|まえ|先立|早く|先行",
           "after":r"直後|以後|以降|後|あと|のち|が済んで|に続|経(?:つ|っ|過)|た(?:つ|った)"}
INVERT_TIME = {"before":"after", "after":"before"}


def temporal(marked, gold, language):
    found = set()
    wrong_object = False
    if language == "en":
        articles = r"\s+(?:(?:the|a|an|our|scheduled|start|end|completion|conclusion|time|of)\s+){0,5}"
        for when, pattern in EN_TIME.items():
            if re.search(r"\b(?:" + pattern + ")" + articles + "#", marked):
                found.add(when)
            # '# takes place ~ after you open @' puts the named event on the other
            # side of the relation. Swapping the two noun phrases alone is not enough.
            if re.search(r"#\s+(?:(?:is|was|will be|occurs?|comes?|takes? place|happens?|begins?|starts?)\s+)?~\s+(?:"
                         + pattern + r")\s+(?:(?:you|the|we)\s+)?(?:open|clos|shut|turn|switch|start|stop|halt|light|extinguish)", marked):
                found.add(INVERT_TIME[when])
            if re.search(r"\b(?:" + pattern + ")" + articles + "@", marked):
                wrong_object |= bool(re.search(r"\b(?:open|close|shut|start|stop|switch|turn|light|extinguish)(?:s|ed|ing)?\s+(?:(?:on|off|the)\s+){0,2}#", marked))
        window = bool(re.search(r"\b(?:for|during|throughout|over|within|in)\s+(?:(?:the|entire|whole|next|first|last|full|following|preceding|period|span|of)\s+){0,5}~|~(?:[-\s]+(?:period|window|stretch)|\s+immediately (?:preceding|following))", marked))
        # 'wait for ~ after #, then open' binds the interval to waiting. The open
        # action is punctual. 'wait until ~ after #' has the same interpretation.
        if re.search(r"\bwait(?:s|ed|ing)?\s+(?:(?:for|until)\s+)?~", marked):
            window = False
        punctual = bool(re.search(r"~\s+(?:before|after|prior to|ahead of|following|earlier than|later than)", marked))
    else:
        marked = re.sub(r"まであと\s*~", "まで残り~", marked)
        for when, pattern in JA_TIME.items():
            if re.search(r"#[^@#。.!?、,\n]{0,27}?(?:" + pattern + ")", marked):
                found.add(when)
            if re.search(r"@[^#。.!?、,\n]{0,28}?~\s*(?:" + pattern + r")(?:に|が|の|で)?\s*#", marked):
                found.add(INVERT_TIME[when])
        if re.search(r"#まで(?:あと|残り)?\s*~|#に先立", marked):
            found.add("before")
        if re.search(r"#から\s*~\s*(?:経|た[つっ])", marked):
            found.add("after")
        wrong_object = bool(re.search(r"#を(?:開|閉|始|止|点|消)", marked)
                            and re.search(r"@(?:の)?\s*~?\s*(?:前|後)", marked))
        window = bool(re.search(r"~\s*(?:(?:の|もの)?(?:間|あいだ|期間)|は|だけは|に[わ渡]た|を通じ|が?経(?:つ|過する)まで|たつまで)|(?:後|あと|以降|前)\s*~\s*[、,]", marked))
        punctual = bool(re.search(r"~\s*(?:前|後)(?:に|で|にな|とな|です|だ)|~\s*(?:経過|たった)(?:して|ら)", marked))
    when = gold["when"]
    relation_ok = found == {when} and "#" in marked and "~" in marked and not wrong_object
    scope_ok = window if gold["window"] else not window
    bad_scope = (gold["window"] and punctual and not window) or (not gold["window"] and window)
    return bool(relation_ok), bool(scope_ok), bool(found - {when} or wrong_object or bad_scope)


TERMINATOR = re.compile(r"[。!?]+(?:[』」”\"]+)?(?=\s*\S)|(?<!\bMr)(?<!\bDr)(?<!\bMs)\.(?:[』」”\"]+)?(?=\s+\S)", re.I)
LIST = re.compile(r"^\s*(?:[-*•]\s+|\(?\d+\s*[).、:](?!\d)\s*|[①②③]\s*)")
HEADING = re.compile(r"^(?:```(?:text|plaintext|markdown)?|(?:here (?:is|are) (?:the |your )?)?(?:translations?|translated text|日本語訳|英訳|訳文|翻訳)(?: (?:in|into) (?:english|japanese))?\s*[:：]?)$", re.I)


def align(delivery, gold, language):
    """Find two ordered semantic spans even if a renderer lost a delimiter.

    Choose the split/orientation by names, terms and values only, independent of
    what comparisons and actions are correct. Prefer original line breaks on ties.
    Reversed, merged or fragmented translations can never pass the format check.
    """
    raw = [x.strip() for x in norm(delivery).splitlines() if x.strip()]
    decorations = any(LIST.match(x) or HEADING.fullmatch(x) for x in raw)
    clean = [LIST.sub("", x) for x in raw if not HEADING.fullmatch(x)]
    text = "\n".join(clean)
    well_formed = len(raw) == 2 and not decorations and all(not TERMINATOR.search(x) for x in raw)
    first = [term_pattern(gold["name"])]
    for rule in gold["rules"]:
        first.extend([term_pattern(rule["term"][language]), quantity(rule["number"], rule["unit"])])
    second = [term_pattern(gold[k][language]) for k in ("asset", "event")]
    second.append(quantity(gold["hours"], "h"))
    spans = [(side, m.start(), m.end()) for side, patterns in enumerate((first, second))
             for pattern in patterns for m in re.finditer(pattern, text, re.I)]
    boundaries = {0: -1, len(text): -1}
    for _, start, end in spans:
        boundaries[start] = max(0, boundaries.get(start, 0))
        boundaries[end] = max(0, boundaries.get(end, 0))
    for m in re.finditer(r"\b(?:please|do not|open|close|shut|start|stop|switch|turn|leave|keep|during|for the|after the|before the)\b", text, re.I):
        boundaries[m.start()] = 1
    for match in re.finditer(r"[;；]|(?<=[するいとた])[、,]", text):
        boundaries[match.end()] = 2
    for match in TERMINATOR.finditer(text):
        boundaries[match.end()] = 3
    for match in re.finditer(r"\n+", text):
        boundaries[match.end()] = 4
    def affinity(cut, reverse):
        return sum(end <= cut if side == int(reverse) else start >= cut for side, start, end in spans)
    options = [(affinity(cut, reverse), priority, not reverse, cut, reverse)
               for cut, priority in boundaries.items() for reverse in (False, True)]
    _, _, _, cut, reverse = max(options)
    left, right = text[:cut].strip(), text[cut:].strip()
    a, b = (right, left) if reverse else (left, right)
    format_ok = well_formed and not reverse and raw == [a, b]
    return a.replace("\n", " "), b.replace("\n", " "), bool(format_ok)


def direction(line, job):
    text = line
    for term in [*job["names"], *job["glossary"].values(), *job["units"]]:
        text = re.sub(term_pattern(term), " ", text, flags=re.I)
    if job["target_language"] == "en":
        return not JAPANESE.search(text) and bool(re.search(r"[a-z]{2,}", text, re.I))
    return bool(JAPANESE.search(text))


def grade(case, record, mode):
    """Score the actual returned translation, including unverified and malformed text.

    Holding a draft leaves result null. Diagnostic/attempt text is never a delivery.
    An otherwise correct translation broken across the wrong lines is a format error.
    Major errors require a delivered number/name/unit fault, a role swap or identifiable
    semantic contradiction. An unfamiliar paraphrase can fail the rubric without being
    counted as a confirmed major error.
    """
    del mode  # The meaning and delivery contract is identical for all translation arms.
    job, gold = case["job"]["input"], case["gold"]
    text = record.get("result")
    answered = isinstance(text, str) and bool(text.strip())
    keys = ("correct", "answered", "serious", "format_error", "format_ok", "meaning_ok", "direction_ok",
            "numbers_ok", "units_ok", "names_ok", "glossary_ok", "comparison_ok", "action_ok",
            "time_ok", "window_ok", "semantic_contradiction", "binding_error")
    if not answered:
        return dict.fromkeys(keys, False)
    language = job["target_language"]
    first, second, formatted = align(text, gold, language)
    original = job["sentences"]
    clean = lambda s: "\n".join(LIST.sub("", x) for x in norm(s).splitlines() if not HEADING.fullmatch(x.strip()))
    checks = [literals(job, "\n".join(original), clean(text)), literals(job, original[0], first),
              literals(job, original[1], second)]
    numbers, units, names, glossary = (all(c[k] for c in checks) for k in ("numbers", "units", "names", "glossary"))
    comparison, wrong_bound, swapped = metric_meaning(first, gold, language)
    marked = mask_event(second, gold, language)
    action, wrong_action = event_action(marked, gold, language)
    when, scope, wrong_time = temporal(marked, gold, language)
    lang = direction(first, job) and direction(second, job)
    meaning = bool(comparison and action and when and scope)
    contradiction = bool(wrong_bound or wrong_action or wrong_time)
    return dict(correct=bool(formatted and lang and meaning and numbers and units and names and glossary),
                answered=True, serious=bool(contradiction or swapped or not (numbers and units and names)),
                format_error=not formatted, format_ok=formatted, direction_ok=bool(lang), meaning_ok=meaning,
                numbers_ok=numbers, units_ok=units, names_ok=names, glossary_ok=glossary,
                comparison_ok=comparison, action_ok=action, time_ok=when, window_ok=scope,
                semantic_contradiction=contradiction, binding_error=swapped)


BOUND_EN = {"le":"at most", "ge":"at least", "lt":"less than", "gt":"more than"}
BOUND_JA = {"le":"以下", "ge":"以上", "lt":"未満", "gt":"超"}
ACTION_TEXT = {
    "en":{"open":"open the {}", "close":"close the {}", "start":"start the {}", "stop":"stop the {}",
          "on":"turn on the {}", "off":"switch off the {}"},
    "ja":{"open":"{}を開けてください", "close":"{}を閉じてください", "start":"{}を開始してください",
          "stop":"{}を停止してください", "on":"{}をつけてください", "off":"{}を消してください"},
}
NEG_JA = {"open":"開けない", "close":"閉じない", "start":"始めない", "stop":"止めない", "on":"つけない", "off":"消さない"}
STATE = {
    "en":{"open":"closed", "close":"open", "on":"off", "off":"on"},
    "ja":{"open":"閉じた", "close":"開いた", "on":"消した", "off":"つけた"},
}


def references(gold, language, alternate=False):
    """Generate two natural reference controls; neither the scorer nor CLI reads them."""
    rules = list(reversed(gold["rules"])) if alternate else gold["rules"]
    clauses = []
    for r in rules:
        amount = (r["number"] + " " + r["unit"]).strip()
        term, op = r["term"][language], r["op"]
        if language == "en":
            phrase = BOUND_EN[op] + " " + amount
            if alternate:
                phrase = {"le":amount+" or less", "ge":amount+" or more", "lt":"strictly below "+amount,
                          "gt":"strictly above "+amount}[op]
            clauses.append(f"the {term} must be {phrase}")
        else:
            ending = {"le":"を超えない", "ge":"を下回らない", "lt":"に満たない", "gt":"を上回る"}[op]
            clauses.append(f"{term}は{amount}{ending if alternate else BOUND_JA[op]}")
    name, verb, when = gold["name"], gold["verb"], gold["when"]
    asset, event = gold["asset"][language], gold["event"][language]
    h = gold["hours"] + " h"
    if language == "en":
        first = f"At {name}, " + ", and ".join(clauses) + "."
        act = ACTION_TEXT[language][verb].format(asset)
        relation = ("prior to" if when == "before" else "following") if alternate else when
        clock = f"{h} {relation} the {event}"
        if gold["window"]:
            if alternate and verb in STATE[language]:
                second = f"Throughout the {clock}, keep the {asset} {STATE[language][verb]}."
            else:
                second = f"Do not {act} for {clock}."
        elif alternate:
            second = f"{clock}, {act}."
        else:
            second = f"{act[0].upper()}{act[1:]} {clock}."
    else:
        first = f"{name}では、" + "、".join(clauses) + ("こと。" if alternate else "とする。")
        relation = "前" if when == "before" else "後"
        if gold["window"]:
            clock = f"{event}の{relation}の{h}"
            if alternate and verb in STATE[language]:
                second = f"{clock}の間は、{asset}を{STATE[language][verb]}ままにしてください。"
            else:
                second = f"{clock}は{asset}を{NEG_JA[verb]}でください。"
        else:
            act = ACTION_TEXT[language][verb].format(asset)
            second = f"{event}の{h}{relation}に{act}。"
    return [first, second]


def selftest():
    totals = Counter()
    families, names, source_lines = set(), set(), set()
    assert NUMBERS.findall("x 5.5 and 0.5") == ["5.5", "0.5"]
    sample = {"number":"5", "unit":"cm"}
    for text, lang, want in [("must not be greater than 5 cm", "en", "le"),
                             ("cannot be less than 5 cm", "en", "ge"),
                             ("must not be at least 5 cm", "en", "lt"),
                             ("not required to be at least 5 cm", "en", "optional"),
                             ("5 cm以下であってはならない", "ja", "gt"),
                             ("5 cm以上でなければならない", "ja", "ge")]:
        assert threshold(text, sample, lang) == {want}
        totals["bound_negation_controls"] += 1
    for split, count in (("dev", 12), ("test", 40)):
        cases = make(split)
        assert len(cases) == count and Counter(c["language"] for c in cases) == {"en":count//2, "ja":count//2}
        assert len({c["id"] for c in cases}) == len(cases)
        for case in cases:
            gold, job = case["gold"], case["job"]["input"]
            language = job["target_language"]
            assert gold["family"] not in families and gold["name"] not in names
            assert all(s not in source_lines for s in job["sentences"])
            families.add(gold["family"])
            names.add(gold["name"])
            source_lines.update(job["sentences"])
            for term in [*job["glossary"], *job["names"]]:
                assert occurrences(term_pattern(term), "\n".join(job["sentences"]), False) == 1, case["id"]
            lines = references(gold, language)
            def check(label, text, **expected):
                score = grade(case, {"status":"unverified", "result":text}, "draft")
                for field, want in expected.items():
                    if score[field] != want:
                        raise AssertionError((case["id"], label, field, want, score))
                assert all(type(v) is bool for v in score.values())
                totals[label] += 1
            def check_change(label, changes, **expected):
                bad = dict(gold, **changes)
                check(label, "\n".join(references(bad, language)), answered=True, correct=False, serious=True, **expected)
            check("reference", "\n".join(lines), answered=True, correct=True, serious=False)
            check("paraphrase", "\n".join(references(gold, language, True)), correct=True, serious=False)
            # Judge the independently authored original as a target in the opposite
            # direction as well. This exercises phrasings outside the reference renderer.
            reverse_job = dict(job, sentences=lines, source_language=language, target_language=case["language"],
                               glossary={v:k for k,v in job["glossary"].items()})
            reverse = dict(case, job={"task":"translate", "input":reverse_job})
            reverse_score = grade(reverse, {"status":"ok", "result":"\n".join(job["sentences"])}, "checked")
            if not reverse_score["correct"]:
                raise AssertionError((case["id"], "independent_original", reverse_score))
            totals["independent_original"] += 1
            head, *rest = gold["rules"]
            for label, op in [("strict_inclusive", STRICT_FLIP[head["op"]]), ("reversed_bound", OPPOSITE[head["op"]])]:
                check_change(label, {"rules":[dict(head, op=op), *rest]}, comparison_ok=False)
            check_change("reversed_action", {"verb":OPPOSED_VERB[gold["verb"]]}, action_ok=False)
            check_change("reversed_time", {"when":INVERT_TIME[gold["when"]]}, time_ok=False)
            check_change("negation", {"window":not gold["window"]}, action_ok=False)
            check_change("name_case", {"name":gold["name"].swapcase()}, names_ok=False)
            check_change("numeric_error", {"hours":"987"}, numbers_ok=False)
            check("changed_unit", "\n".join([lines[0], lines[1].replace(" h", " kg")]), correct=False, serious=True, units_ok=False)
            if rest:
                assert len(rest) == 1 and head["unit"] == rest[0]["unit"] and head["number"] != rest[0]["number"]
                rules = [dict(head, number=rest[0]["number"]), dict(rest[0], number=head["number"])]
                check_change("swapped_same_unit_roles", {"rules":rules}, numbers_ok=True, units_ok=True, binding_error=True)
            for label, distorted in [("merged", " ".join(lines)), ("order", "\n".join(reversed(lines))),
                                     ("wrapped", "1. " + lines[0] + "\n2. " + lines[1]),
                                     ("explicit_heading", "Translation:\n" + "\n".join(lines))]:
                check(label, distorted, correct=False, answered=True, format_error=True, meaning_ok=True, serious=False)
            bad = references(dict(gold, when=INVERT_TIME[gold["when"]]), language)
            check("merged_reversal", " ".join(bad), correct=False, answered=True, serious=True, format_error=True, time_ok=False)
            for status in ("held", "error", "refused"):
                held = grade(case, {"status":status, "result":None, "attempts":[{"response":"\n".join(bad)}]}, "checked")
                assert not any(held.values())
                totals["undelivered"] += 1
            exposed = grade(case, {"status":"held", "result":"\n".join(bad)}, "checked")
            assert exposed["answered"] and exposed["serious"]
            totals["exposed_failure_result"] += 1
    return dict(sorted(totals.items()))


if __name__ == "__main__":
    print(json.dumps(selftest(), sort_keys=True))
