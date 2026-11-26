"""Independently authored paired questions for the combinations experiment.

Only ``job`` (long QA) or ``question`` (arithmetic) is model input. All offsets
are UTF-8 byte offsets into the raw, chronologically ordered source. A change
span covers the whole affected passage. On a missing version it marks the
matched comparison site, which need not explicitly express absence.
"""

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import random
import re


@dataclass(frozen=True)
class Notebook:
    pair: str
    tier: str
    language: str
    group: str
    title: str
    question: str
    value: str
    distractor: str
    position: int
    decision: str
    notes: tuple
    objects: str
    places: str
    details: tuple


# In the decision passage, the two arms of ⟦...|...⟧ are the single
# counterfactual edit. {value} identifies the author's exact occurrence.
NOTEBOOKS = (
    Notebook(
        "fern", "8k", "en", "silent_omission",
        "Winter repairs in the fern house",
        "What material did the gardeners actually sew into the night sleeve for the west vent? Give the material name used in their book.",
        "sailcloth", "felt", 12,
        "The gardener brought the finished sleeves back from the sewing room. She had sewn {value} into the night sleeve for the ⟦west|east⟧ vent, and the tacking thread was still in the seam.",
        ((3, "We laid felt and sailcloth on the potting bench. Both fit under a closed vent. The seam allowance mattered more than the width of the remnant."),
         (8, "A strip of felt caught on the thumb catch during the west vent fitting. The gardener took both samples back to the sewing room."),
         (26, "The west vent opened easily once the painter scraped the sill. Its brass stay is still the one shown on the spring maintenance sketch."),
         (48, "For the thermometer shelter the gardeners used sailcloth. The old cover for the water can was felt, which made a good pad under its handle."),
         (75, "A drawing headed ‘night sleeve’ went up by the fern trays. The small black rectangle in it marks the brass vent catch."),
         (94, "A spare length of sailcloth came back from the cold frame, rolled around the rod that had supported its shade.")),
        "seed sieve|brass stay|watering rose|clay saucer|wooden dibber|twine spool|plant label|spray nozzle|tin scoop|glass bell|pruning brush|shade hook|drip tray|reed mat|pot lifter|roof catch",
        "potting bench|door shelf|wash table|propagation bay|low cabinet|rear workstand|seed counter",
        ("A tracing on oiled paper preserved the curve of the old fittings.", "The morning shift hung its damp rags behind the cistern.", "A pinch of potting grit had worked into the binding of the daybook.", "Drops falling from the panes left pale rings on the bench paper.", "The charcoal rubbing shows which side faced the path.")),
    Notebook(
        "lantern", "8k", "ja", "redecision",
        "小劇場の袖で付けた引継ぎ帳",
        "巡演が終わったあと、夕公演の終演案内を流す合図として、本番用に決まった音は何か。帳面で使われた呼び名を答えてほしい。",
        "木片を二度打つ音", "小鈴の音", 31,
        "稽古のあと、舞台監督は夕公演の終演案内について、木箱の裏で鳴らす「{value}」を⟦本番の合図に決めた|次の音合わせで試した⟧。合図係は客席からどこまで聞こえるかも確かめた。",
        ((2, "春の巡演では終演案内に小鈴の音を使った。巡演限りの取り決めで、最後の荷を下ろした日にその効力も切れた。"),
         (15, "昼の人形芝居の台本には、戸の外を誰かが通る音として「木片を二度打つ音」とある。音を出す木箱だけは夜の組と共用している。"),
         (22, "夕公演の音合わせで、小鈴の音と木片を二度打つ音を客席のいちばん後ろから聴き比べた。袖の記録には響きの長さだけが書き添えてある。"),
         (46, "案内係が新しい読み原稿を受け取った。終演案内では忘れ物の問い合わせ先を二度読むよう、行の頭に丸が付いている。"),
         (64, "昼の組が小鈴の音を録り直した。盆の上に厚い布を敷くと鈴を置く音が消える。"),
         (92, "木片を二度打つ音を録った素材の箱に、録音した部屋の見取り図を入れた。音素材は貸出棚にも一本ずつ戻してある。")),
        "手鈴|台本立て|拍子木|床敷き|足元灯|譜面ばさみ|懐中灯|読み台|綿手袋|幕ひも|道具盆|木箱|手鏡|扉止め|呼び鈴|予備の帯",
        "下手の袖|音響机|楽屋口|小道具棚|客席の後ろ|稽古場の隅|幕の内側",
        ("暗転中にも手探りで持てるよう、ひもに結び目を作った。", "床に敷いた古い毛布は足音をよく吸った。", "役者が衣装を着た状態で通路の幅を確かめた。", "袖から見える範囲を鉛筆で平面図に書き込んだ。", "録音の頭に入った椅子のきしみを控えに書いた。")),
    Notebook(
        "indigo", "8k", "en", "out_of_scope_renewal",
        "Blue room shift book",
        "Which lining had a renewed approval for the late shift's blue bath by the end of the book? Give the material as the dyers wrote it.",
        "glazed stoneware", "enamelled steel", 48,
        "Both shifts sent their blue-bath slips to the forewoman. She initialled the renewal for {value} in the ⟦late|day⟧ shift's blue bath and sent the slip back with the empty dye jars.",
        ((1, "The late shift's old blue bath used enamelled steel. Permission for that lining ran only to the change of vat, and expired when the vat was lifted out."),
         (19, "An earlier trial of glazed stoneware covered one blue bath on each shift. The forewoman collected both trial slips when their last date passed."),
         (35, "The wash-room tally lists a glazed stoneware insert under rinse water and an enamelled steel insert under soap. Those tallies travel with the wash tubs."),
         (62, "The late shift washed its blue skeins twice. The water stayed clear on the last pass, so the dyers hung the skeins at the far end of the rail."),
         (81, "Two glazed stoneware sample chips had slipped behind the crockery drawer. A day dyer put them back with the chip for enamelled steel."),
         (96, "The order for the blue-room aprons came through on a slip the same size as the lining slips. The storekeeper filed it with the clothing accounts.")),
        "skein stick|sample chip|linen apron|stirring paddle|water gauge|yarn winder|drain plug|colour card|tongs|cotton hank|stock jar|weighing pan|vat hook|rinse ladle|test swatch|drying peg",
        "wet bench|yarn rail|mixing table|rinse station|sample cupboard|loading step|wash-room counter",
        ("The cotton went a little paler once it was dry.", "Under the skylight the two trial swatches looked much closer in shade.", "The dyers kept their wet gloves on a wire over the sink.", "A loop of spare yarn was enough to pull the rack into reach.", "The old blue marks came off the measuring jug with a stiff brush.")),
    Notebook(
        "wrack", "8k", "ja", "withdrawal",
        "干潟を歩いて音を聴く会の浜帳",
        "公開日の聴き歩きで、木道を折り返す目印には何を使うことになったか。浜帳にある物の呼び名を答えてほしい。",
        "白い貝の札", "青い浮き玉", 67,
        "木道組は見回りから戻り、試し歩きで折返しに立てた{value}を、公開日の折返しの目印として⟦採用する|候補から外す⟧ことを決めた。記録係は札の紐を乾かしに行った。",
        ((4, "試し歩きでは木道の端に白い貝の札を立てた。このときの道具の割り振りは試し歩き一回分で終わり、公開日は別に打ち合わせる段取りだった。"),
         (23, "展示用の青い浮き玉を洗い、砂を落として網に戻した。浅瀬では青い浮き玉の色が写真より暗く見えた。"),
         (41, "受付の机に並べる漂着物の中に白い貝の札も一つ置いた。机に付ける解説では、貝の内側に残る小さな穴を指している。"),
         (53, "木道の折返しで一列に並ぶと後ろの耳に風が当たる。音を聴くときだけ体を少しずらすことにした。"),
         (79, "写真係の控えでは、青い浮き玉のアップに続いて白い貝の札の裏を載せる。紐の結び方が違うためだという。"),
         (97, "木道組が使った籠を潮だまりの向こうから持ち帰った。折返しで拾った小枝は乾いてから水音の展示に加える。")),
        "干潟図|聴音筒|砂受け|採集皿|帆布袋|手拭い|折尺|細い網|木さじ|漂着瓶|紐玉|記録板|長靴べら|小旗|地図筒|写真枠",
        "浜小屋の前|木道の入口|砂洗い場|受付の机|潮見台の下|浜道の脇|採集棚",
        ("砂に付いた跡は潮の向きに沿って細く延びていた。", "貝を洗った水は網を通して桶に戻した。", "足音と区別がつくよう、波の録音は風下で始めた。", "低い柵に腰を掛けると水音が近くなった。", "浜小屋の戸の裏で湿った紐を乾かした。")),
    Notebook(
        "meridian", "8k", "en", "edition_attribution",
        "Notes from the observatory card table",
        "What line was printed at the head of the winter star cards the visitors took home? Copy that line alone.",
        "Look near the rim", "Follow the hook", 82,
        "The printer brought in a card with “{value}” across the top. It came from ⟦the run the visitors took home|the proof run for measuring margins⟧. The assistant kept this impression flat in the drawer.",
        ((5, "The first lettering trial put ‘Follow the hook’ along a curve and ‘Look near the rim’ on a straight rule. The printer kept both strips loose."),
         (28, "A speaker used the words ‘Look near the rim’ when showing visitors how to turn a cardboard planisphere. The phrase went into the talk transcript."),
         (44, "The winter star cards went through several runs while the counter staff worked out how many would fit in one envelope. A paper strip around a stack gives only the number of cards."),
         (69, "Both ‘Follow the hook’ and ‘Look near the rim’ fit the narrow type measure. The margins were still being cut from waste at this stage."),
         (89, "The assistant sent the leftover winter envelopes down with the visitors' headcount. It was too dark in the dome for anyone to fill out the cards there."),
         (98, "A library slip lists the talk ‘Follow the hook’ in the top drawer and a recording called ‘Look near the rim’ in the next one down.")),
        "star wheel|red torch|type tray|ink roller|folding gauge|lens cap|paper strip|finder card|cotton glove|sky chart|mounting pin|brass rule|card sleeve|map clip|reading rest|sight tube",
        "card table|dome entrance|printing bench|visitor counter|chart shelf|north window|folding station",
        ("The low red light left the pencil marks easier to see than the ink.", "The narrowest envelopes curled up as soon as the gum dried.", "From the reading step only the large type could be made out.", "The guide rotated a scrap of card to show how the sky wheel turns.", "The printer kept the loose leads in an old lens box.")),
    Notebook(
        "wafer", "8k", "ja", "other_subject",
        "菓子店と箱屋の受け渡し綴り",
        "今季の確定伝票で、麦菓子の土産箱の中敷きには何を使う指定になったか。材の名を伝票の表記で答えてほしい。",
        "薄葉紙", "奉書紙", 94,
        "箱屋から届いた今季の確定伝票には、⟦麦菓子|豆菓子⟧の土産箱の中敷きに{value}を使うと書いてあった。箱の内寸は裏の図に鉛筆で入っている。",
        ((3, "昨季の麦菓子の土産箱は奉書紙を敷いた。古い箱の注文を締めた日にそちらの指定も終了し、今季の箱は別伝票で手配する。"),
         (21, "奉書紙と薄葉紙を同じ幅に切り、試し箱に重ねて蓋の具合を見た。紙見本には幅だけを墨で書いた。"),
         (43, "豆菓子と麦菓子の土産箱は外寸こそ同じだが、内側の折り返しが違う。中敷きの注文票は菓子ごとに一枚ずつ作る。"),
         (62, "飴を包む係から薄葉紙の端切れが戻った。色見本から外れた奉書紙は、店頭の値札を貼る台にした。"),
         (81, "麦菓子の試食箱では薄葉紙の下に木の薄板を置いてみた。試食用の箱は店頭で使い切り、販売用の土産箱は別の包みにする。"),
         (98, "確定伝票は箱屋へ代金を渡した順に綴じた。中敷きの裁ち落としは小袋に詰め、包みの色を見るときに取り出せるようにした。")),
        "菓子盆|竹へら|掛け紙|糸巻き|紙見本|木型|折り板|箱尺|小皿|封印|見本袋|角当て|麺棒|紙帯|札立て|木蓋",
        "包み台|店先の棚|裏の流し|菓子場の脇|帳場の机|箱積み場|戸口の台",
        ("折り目を爪でたどると糊が残った箇所だけ少し硬い。", "箱の角がつぶれないよう、紐には余り紙を挟んだ。", "砂糖が付いた手拭いは包み場に持ち込まずに洗った。", "綴じ穴をあけてから納品書の端をそろえた。", "木型に残った粉をはけで払い、布の上に伏せた。")),
)

NOTEBOOKS += (
    Notebook(
        "quince", "16k", "en", "redecision",
        "Cups and sittings in the tasting room",
        "After the stewards reconvened, which blend did they settle on for the opening pot of the rain sitting? Use the name on the tasting papers.",
        "Quince Bloom", "Moss Apple", 9,
        "At the reconvened meeting the stewards wrote {value} against the opening pot of the rain sitting. They signed the sheet as ⟦their final choice|a proposal to circulate⟧ and passed it to the host.",
        ((1, "When the roof leaked the host cancelled the first rain sitting. The stewards struck out its choice of Moss Apple, sent the pots back, and agreed to decide afresh when the room reopened."),
         (5, "For the first round of suggestions the host copied Quince Bloom and Moss Apple into both margins. A tasting paper from a cancelled sitting could supply names for discussion."),
         (23, "The kitchen set a jug of Quince Bloom on the tray for volunteers. The line marked opening pot refers only to what will be poured for the guests."),
         (45, "The host of the rain sitting counted out guest cups in pairs. One cup of each pair has a scratch under its rim so the drinker can tell them apart."),
         (71, "A new pot card for Moss Apple went into the sample chest. An older card for Quince Bloom is pasted to the lid of the potter's demonstration pot."),
         (94, "Someone asked why the rain sitting had that name. The stewards used to pass cups around a long table under the glass roof and kept the name after they moved.")),
        "tasting spoon|sample tin|clay strainer|handle guard|tea brush|cup ring|pouring jug|water scoop|felt coaster|glass lid|tray cloth|bamboo pick|pot card|cup rest|leaf dish|timer bell",
        "tasting table|cupboard by the hatch|potter's bench|serving step|sample counter|drying rack|host's table",
        ("A full pot took both hands on the way back from the hatch.", "The thin cups showed the last drops more clearly against white cloth.", "The stewards saved the empty water jugs for the next sitting.", "A second flush of water carried the loose leaves clear of the spout.", "The host folded each cloth twice before putting it away.")),
    Notebook(
        "fan", "16k", "ja", "silent_omission",
        "手製本を習う会の製本日誌",
        "修理講座の帰りに受講者が持ち帰った練習帖では、表紙にどの模様が型押しされていたか。模様の呼び名を答えてほしい。",
        "重ね扇", "折れ波", 25,
        "講師が帰りの包みを一つほどいて見せた。練習帖の⟦表紙|包み帯⟧には{value}が型押しされ、紙に触れると扇の骨の跡まで分かった。",
        ((2, "修理講座の見本台に重ね扇と折れ波の型を並べた。箔を使わずに押した見本ではどちらも光を斜めから当てるとよく見える。"),
         (13, "練習帖は受講者が帰りに一冊ずつ持ち帰る。表紙の上に薄い包み帯が掛かり、帯は本を開く前に外してもらう。"),
         (39, "重ね扇の型を当てた端紙が道具箱の蓋に貼ってあった。折れ波の端紙は先生が自分の小銭入れの敷き紙にした。"),
         (58, "展示用の折本では、表紙の折れ波と見返しの線がつながる。こちらは製本見本として会場の本棚に戻した。"),
         (79, "講師の重ね扇の道具には、握りに古い布が巻いてある。練習帖に挟んだ栞には布を巻き直す手順も描いた。"),
         (96, "持ち帰り用の紙袋を片付けた。表紙の角を傷めないよう、袋の底は別紙を重ねて貼ってある。")),
        "糸巻き|へら|目打ち|重し板|寒冷紗|竹定規|背当て|紙やすり|木づち|見返し紙|糊皿|折り型|色見本|平筆|表紙布|圧し板",
        "製本台|乾かし棚|裁ち台の端|糊場|展示机|講師席|西の作業台",
        ("乾いた糊はぬるま湯を含ませた布で少しずつ取った。", "糸の穴を明かりにかざすと裏側の繊維が見えた。", "重しの下からはみ出した薄紙を夕方に入れ替えた。", "見返しを閉じる前に糊が表へしみていないか確かめた。", "木口に鉛筆で引いた細い線を小刀でさらった。")),
    Notebook(
        "estuary", "16k", "en", "other_subject",
        "Packing the travelling marsh display",
        "Where in the wagon was the tide dial stowed for the departure from the marsh hall? Give the compartment name used on departure.",
        "map drawer", "rope chest", 43,
        "With the wagon ready to leave, the packer opened the {value} just far enough for the checker to see the ⟦tide|wind⟧ dial inside. It was the last check before they drove away.",
        ((1, "The tide dial and wind dial are different pieces. The tide dial shows the rise and fall of water, while the wind dial has the little brass vane."),
         (14, "For the packing lesson the guide tried both dials in the rope chest, then both in the map drawer. She took them out again and put the empty wagon on display."),
         (33, "The travel plan shows the rope chest above a wheel and the map drawer below the lantern shelf. Chalk lines stand for dividers rather than for the things packed inside them."),
         (56, "The tide dial's cradle stayed behind in the marsh hall. The carpenter wanted to round off a splinter on the side that touches the wall."),
         (77, "A picture labelled ‘map drawer’ went into a travelling album beside another labelled ‘rope chest’. The picture was taken with the doors wide open during the packing lesson."),
         (97, "On their way home the crew returned the key to the map drawer and the spare rope chest handle. Both belonged in the wagon keeper's tool bag.")),
        "tide chart|reed whistle|brass vane|float model|marsh map|folding easel|strainer basket|sample jar|wooden rail|waxed cord|plumb bob|dial cradle|lantern shade|display peg|scale board|chalk holder",
        "marsh hall|wagon step|model bench|loading bay|glass case|display table|cart shed",
        ("The carved reeds stood up better when the model bank was tilted.", "The wide wagon wheels left only a shallow mark on the rush mat.", "A length of cloth stopped loose brasswork from rattling on the road.", "The guide used a basin of water to show how the marsh fills.", "Sand from the last visit still clung to the folds of the display cloth.")),
    Notebook(
        "oven", "16k", "ja", "out_of_scope_renewal",
        "山道を回るパン便の番帳",
        "番帳の最後までに、麦便の雨の日の受渡し場所として借用が延長されたのはどこか。番帳の場所名で答えてほしい。",
        "石段下の軒", "水車小屋", 59,
        "家主が持ち帰った借用控えには、麦便が{value}でパンを渡せる期間を延ばすとあった。延長が適用されるのは⟦雨の日|晴れの日⟧の受渡しで、番人が控えに印を押した。",
        ((3, "麦便が水車小屋を借りて雨宿りをしていたのは橋を架け替えるまでのことで、通行が再開してその約束は終わった。"),
         (21, "石段下の軒を借りる試行では晴れ用と雨用の控えを一枚ずつ作った。試行期間が切れたので、一度どちらの控えにも締めの印を入れた。"),
         (39, "パンを積む木箱を石段下の軒で拭いた。空箱は水車小屋でも乾かせるため、片付けの係はその日の風で作業場所を選ぶ。"),
         (73, "雨の日は麦便の籠を油紙で覆う。籠を置くときに紙の端が地面に触れると泥がパンの包みに付く。"),
         (84, "水車小屋と石段下の軒までの坂を空荷で押してみた。坂の曲がり角では前輪から先に向きを変えると楽だった。"),
         (97, "借用控えを番帳に綴じたあと、荷札と受取符を店ごとにそろえた。翌朝の麦便に載せるパンは窓側で冷ます。")),
        "パン籠|木箱|荷札|油紙|手袋|棒はかり|受取符|紐巻き|荷台板|布巾|留め金|かご蓋|木べら|小袋|包み針|手押し棒",
        "窯口の前|積み込み台|坂道の入口|粉置き場|帳場|店の裏口|荷車小屋",
        ("焼き上がった丸パンは間をあけて簀の上に置いた。", "上り坂では先を歩く人が石の浮いた所を知らせた。", "籠を傾けても包みが寄らないよう底に布を敷いた。", "粉が付いた受取符は乾いた刷毛で払ってから畳んだ。", "荷車の握りに巻いた布を一巻きだけ詰め直した。")),
    Notebook(
        "pippin", "16k", "en", "decision_scope",
        "Book from the orchard bottling table",
        "Which colour name did the orchard finally adopt for labels on the cloudy pear cordial after the votes were counted? Quote the colour name.",
        "fennel green", "damson red", 77,
        "After counting the slips, the orchard secretary entered {value} for the cloudy pear cordial. She signed the result as ⟦the orchard's adopted label colour|the preference of the first tasting table⟧. The votes themselves went into an envelope.",
        ((1, "The cloudy pear cordial was a new bottling. A pear drink sold last year carried a damson red label and was made from a different recipe."),
         (23, "At the first tasting table guests could choose between fennel green and damson red. A second table used slips with the names in the opposite order."),
         (37, "One trial bottle wore a fennel green label on one side and a damson red label on the other. The paper stock for the entire trial came from a single packet."),
         (52, "The orchard decides the public label at a meeting. Tables may record preferences first. No number of table votes gives a colour standing on its own."),
         (81, "A set of fennel green neck rings went back to a maker who wanted to show off her cutting tool. Those rings go at the top of the sample bottles."),
         (95, "The bookbinder chose damson red for the sample album, with a narrow fennel green strip inside the cover. Empty slots are left for the next pressing.")),
        "pear basket|bottle brush|pulp scoop|wax pencil|sample cork|press cloth|paper collar|crate key|bottle stand|tasting beaker|colour chip|funnel|press wedge|picking strap|ink pad|sieve ring",
        "press table|bottling bench|orchard gate|wash trough|packing step|sample shelf|sorting counter",
        ("The late pears gave off a sharper scent once the skin was broken.", "The bottle washer let the last drops run onto a folded cloth.", "A clean strip of paper was enough to test whether the glue had set.", "The pickers saved the firmest fruit for the end of the pressing.", "Holding the glass to the light made the sediment easier to see.")),
    Notebook(
        "wren", "16k", "ja", "model_attribution",
        "からくり時計を掛け直すまでの覚え書き",
        "修理後に広間へ掛けた本時計で、時報の最初に窓から現れた鳥は何か。職人の覚え書きにある鳥の呼び名を答えてほしい。",
        "みそさざい", "つぐみ", 91,
        "窓の高さをそろえたあと、職人は⟦広間に掛けた本時計|展示台の仕組み模型⟧を一時に合わせて動かした。時報の初めに窓から出てきたのは{value}だったので、羽の先が枠を通るところを横から写した。",
        ((3, "広間に掛ける本時計のほかに、歯車が見える展示台の仕組み模型がある。鳥が出る順番は別々の胴に刻んである。"),
         (18, "古い胴にはつぐみを先に出す溝があった。本時計からその胴を外し、今回削り直した胴に入れ替えた。"),
         (32, "みそさざいとつぐみの羽に付いたほこりを柔らかい筆で落とした。職人は二羽とも本時計と模型のどちらにも差せる寸法に直している。"),
         (54, "みそさざいの横姿は窓の形を見るのにちょうどよい。本時計が止まっている間、羽根の影だけを壁の紙に描き取った。"),
         (73, "つぐみを先に出してみた試運転は、胴を入れずに紐を手で引いたときのものだった。写真の裏にもその手順を書き残した。"),
         (98, "仕組み模型の傍らにはみそさざいの作りかけの羽を並べた。広間の本時計を見上げた人が手元でも羽の厚さを比べられる。")),
        "軸受け|時打ち針|羽板|窓枠|見本歯車|振り子|金やすり|油さし|巻き鍵|文字盤|細筆|木栓|胴の留め具|磨き布|受け木|口ばし型",
        "時計台の下|調整台|旋盤の脇|展示机|窓際の棚|広間の入口|工房の奥",
        ("針を逆へ回す前に時打ちの紐を少し緩めた。", "ガラス越しに見える擦れをななめの光で探した。", "羽の裏に切り残した木くずを筆で払い落とした。", "型紙の向きが分かるよう端に小さな切り込みを入れた。", "磨き終えた軸は指で転がすと同じ場所で止まった。")),
)


# All background uses the topic's own portable objects and work areas. Each
# note is a quiet event in the same book, with two other objects in play. The
# fixed topic observations occur once apiece, to avoid a repeated padding line.
BACKGROUND = {
    "en": (
        "On the way to the {place} we carried the {a} by hand. The {b} went on the following trip so nothing would rub against its edges.",
        "While waiting at the {place}, one helper made an outline of the {a}. Another wrote the name of the {b} along the edge so the drawings could be filed together.",
        "At the {place} a strip of light picked out a mark on the {a}. We shifted the {b} a little and could follow the mark to its end.",
        "The note that came back with the {a} mentioned the {b} as well. We left it at the {place} for the person taking the next round.",
        "A helper at the {place} asked to see the {a} once the {b} had been put away. There was just enough room to lay out the old measurements.",
        "Yesterday's list put the {a} on the same line as the {b}. Today we went back to the {place} and copied the labels on separate slips.",
        "The {a} came through the {place} again with a paper loop tied round it. A pencil line on the loop showed where it had touched the {b}.",
        "Someone sketched the {a} from the doorway of the {place}. I held the {b} clear of the drawing while the last few strokes dried.",
        "The shelf list at the {place} had lost its lower corner. We could still make out the {a} near the crease and the {b} on the line beneath it.",
        "Taking over at the {place}, we found room for the {a} close to the light. The {b} needed more clearance on the side nearest the passage.",
        "The new hand traced the outline of the {a} with a finger. At the {place} we set the {b} alongside long enough to compare their proportions.",
        "A smear that looked dark beside the {a} was almost invisible near the {b}. The light at the {place} made the difference easy to see.",
        "At the {place} we folded a sheet of scrap paper round the {a} to judge the space it took up. The {b} needed a second sheet folded crosswise.",
        "Two loops of cord had been left at the {place}. We took one for the {a} and left the other beside the {b} in plain sight.",
        "Back at the {place} the page for the {a} had a crease through the sketch. We slipped it under the page for the {b} until it lay flat.",
        "A rough rubbing of the {a} was passed along the table. The person at the {place} put it into the same folder as the drawing of the {b}.",
        "The {a} is easier to recognise in profile than in the overhead sketch. We stood at the {place} to get a clear view past the {b}.",
        "When the {b} came back to the {place}, we lifted the cloth off the {a} as well. Both could go into the afternoon count at the same time.",
        "A paper marker for the {a} came loose on the way to the {place}. The marker for the {b} stayed on, so we copied the knot before putting them away.",
        "There was room left on the inventory page for the {b}. The new drawing of the {a} filled most of that space by the time the book reached the {place}.",
    ),
    "ja": (
        "{place}まで{a}を手で運んだ。途中で擦れないように、{b}のほうは次に来る人に頼んだ。",
        "{place}で時間があいたので{a}の輪郭を紙に取った。紙の隅に{b}の名も書き、二枚の図を一緒に探せるようにした。",
        "{place}に差した光で、{a}に残っていた細い跡が見えた。{b}を少しずらすと跡の端までたどれた。",
        "{a}と一緒に戻った走り書きには{b}のことも載っていた。次の当番がすぐに読めるよう{place}に置いておいた。",
        "{place}の手伝いから、{b}をしまったあとで{a}を見せてほしいと頼まれた。古い見取図を広げる場所が要るらしい。",
        "前の日の控えでは{a}と{b}が同じ行に入っていた。{place}へ見に行って、一つずつ別紙に名を書き取った。",
        "{place}に戻ってきた{a}には紙の輪が掛かっていた。輪をよく見ると、{b}に触れていた位置に細い鉛筆の線があった。",
        "{place}の手前から{a}を写生した。紙が乾くまで、そばを通る人の{b}が当たらないよう見ていた。",
        "{place}にあった棚卸しの控えは下の角が欠けていた。折り目のそばに{a}、すぐ下の行に{b}の字が読めた。",
        "{place}の当番を交替した。{a}は明かりの近くに置けたが、{b}には通路側に手を入れる隙間が要った。",
        "手伝いに来た人が{a}の縁を指でたどった。{place}でしばらく{b}を横に置き、大きさの違いを見比べた。",
        "{a}の脇で濃く見えた汚れは、{b}の横に移すとほとんど目立たない。{place}の明かりで見るとその違いがよく分かった。",
        "余り紙を{a}に巻き付けて、運ぶのに要る幅を測った。{place}には別の紙を置いてあるので、{b}はそちらで測った。",
        "{place}に紐が二本置いてあった。{a}に一本使い、もう一本は次に使う{b}のそばへ移しておいた。",
        "{place}に戻ると、{a}の絵のまんなかに折り目が入っていた。伸びるまで{b}の図の下に差し込んでおいた。",
        "{a}から取った拓本を回し読みした。最後に{place}の係が受け取り、{b}の写生を挟んだ紙ばさみに収めた。",
        "{a}は上からの絵より横からの絵のほうが判別しやすい。{place}に立ち、{b}の向こうをのぞいて形を確かめた。",
        "{place}に{b}が戻ったころ、{a}に掛けていた布も取った。これで午後の棚卸しには両方そろって載せられる。",
        "{place}まで運ぶ途中で{a}の紙札が外れた。{b}の札を見ながら同じ結び方に直してしまった。",
        "{b}の図にはまだ十分な余白があった。{place}まで控えが回ってきたころには、その余白に{a}の絵が描き足されていた。",
    ),
}


DEFAULT_COUNTS = {
    ("8k", "en"): 197, ("8k", "ja"): 124,
    ("16k", "en"): 399, ("16k", "ja"): 253,
}


def _seed(key):
    return int.from_bytes(sha256(key.encode()).digest()[:8], "big")


def _arms(text, positive):
    prefix, choices = text.split("⟦")
    choices, suffix = choices.split("⟧")
    chosen, missing = choices.split("|")
    assert chosen != missing and chosen and missing
    return prefix + (chosen if positive else missing) + suffix


def _background(book, count):
    rng = random.Random(_seed(book.pair))
    objects = book.objects.split("|")
    places = book.places.split("|")
    forms = BACKGROUND[book.language]
    # Shuffle whole cycles to give each template and work area coverage. The
    # two noun slots are sampled without replacement and in role order.
    object_pairs = [(a, b) for a in objects for b in objects if a != b]
    rng.shuffle(object_pairs)
    form_cycle = list(range(len(forms)))
    place_cycle = places[:]
    special = {((i + 1) * count) // (len(book.details) + 1): note
               for i, note in enumerate(book.details)}
    lines = []
    for i in range(count):
        if i % len(forms) == 0:
            rng.shuffle(form_cycle)
        if i % len(places) == 0:
            rng.shuffle(place_cycle)
        if i and i % len(object_pairs) == 0:
            rng.shuffle(object_pairs)
        a, b = object_pairs[i % len(object_pairs)]
        line = forms[form_cycle[i % len(forms)]].format(
            a=a, b=b, place=place_cycle[i % len(places)])
        if i in special:
            line += (" " if book.language == "en" else "") + special[i]
        lines.append(line)
    return lines


def _render(book, positive, background):
    n = len(background)
    decision = _arms(book.decision, positive)
    head, tail = decision.split("{value}")
    events = [(at, False, note) for at, note in book.notes]
    events.append((book.position, True, head + book.value + tail))
    buckets = defaultdict(list)
    for at, pivot, note in sorted(events):
        buckets[(n * at + 50) // 100].append((pivot, note))

    chronological = []
    for i in range(n + 1):
        chronological.extend(buckets[i])
        if i < n:
            chronological.append((False, background[i]))
    intro = ("The entries on these pages follow the order in which they were written."
             if book.language == "en" else "各頁の書き付けを、記入された順に収めた。")
    chunks = [book.title + "\n" + intro]
    offset = len(chunks[0].encode())
    change = []
    gold_spans = []
    for i, (pivot, line) in enumerate(chronological):
        page = i // 8 + 1
        if i % 8 == 0:
            heading = f"Page {page}" if book.language == "en" else f"第{page}頁"
            chunks.append("\n\n" + heading)
            offset += 2 + len(heading.encode())
        chunks.append("\n\n" + line)
        start = offset + 2
        offset = start + len(line.encode())
        if pivot:
            change.append([start, offset])
            if positive:
                a = start + len(head.encode())
                gold_spans.append([a, a + len(book.value.encode())])
    source = "".join(chunks)
    return {"id": f"qa-{book.pair}-{'p' if positive else 'm'}", "pair": book.pair,
            "tier": book.tier, "language": book.language, "group": book.group,
            "positive": positive, "filler_count": n,
            "job": {"task": "answer", "question": book.question, "source": source},
            "gold": book.value if positive else None,
            "gold_spans": gold_spans, "change_spans": change}


def qa_cases(lengths=None):
    """Return 24 long QA cases. Override filler paragraph counts by pair ID."""
    lengths = {} if lengths is None else lengths
    unknown = set(lengths) - {book.pair for book in NOTEBOOKS}
    if unknown:
        raise ValueError(f"Unknown QA pair IDs: {sorted(unknown)}")
    cases = []
    for book in NOTEBOOKS:
        n = lengths.get(book.pair, DEFAULT_COUNTS[book.tier, book.language])
        if type(n) is not int or n < 0:
            raise ValueError(f"Invalid filler paragraph count for {book.pair}: {n!r}")
        background = _background(book, n)
        for positive in (True, False):
            cases.append(_render(book, positive, background))
    return cases


@dataclass(frozen=True)
class Arithmetic:
    pair: str
    language: str
    group: str
    prompt: str
    expression: str
    x: str
    alternatives: tuple
    expected: str
    rationale: str


# x names the one relation that the matched edit removes. Two assignments
# consistent with the missing text give different outcomes. The literal answer
# was written separately and is checked against exact rational arithmetic.
ARITHMETIC = (
    Arithmetic(
        "trolley", "en", "time_weighted_speed",
        "A sound trolley spent 16 minutes moving on one leg and 24 minutes moving on the next. There was also an 8-minute stop between the legs. The route book gives a length of 5 km ⟦for each timed leg|for a nearby access road⟧. Using 60 minutes per hour, what was the trolley's average speed over its moving time, in km/h?",
        "2*x*60/(16+24)", "5", ("3", "6"), "15",
        "x is the mean of the two leg lengths in kilometres. In the missing version both legs could have length 3 or both length 6. The stopped interval contributes no moving time."),
    Arithmetic(
        "sieve", "ja", "cancelling_scale_offsets",
        "粉を足す前後で同じ受け皿を量った。使えるはかりは2台あり、それぞれの表示には作業中一定のずれがある。粉を足す前と後で⟦同じはかり|別々のはかり⟧を使い、前は21.75 g、後は32.5 gと読んだ。隣にある予備の皿は10 gだった。受け皿の中で増えた粉は何gか。",
        "32.5-21.75-x", "0", ("0", "2"), "10.75",
        "x is the final scale offset minus the initial scale offset in grams. One unchanged scale cancels its offset. Two independent offsets may be equal or may differ by 2 g. The spare dish was never measured by either scale here."),
    Arithmetic(
        "cartons", "en", "mixture_by_capacity",
        "There were 3 rows of 8 cartons for a tile exhibit. Four cartons went for repair. Each remaining carton was filled to capacity with either a 3-tile insert or a 5-tile insert. The packing note says that exactly one quarter of ⟦the remaining cartons in this run|the cartons in yesterday's practice run⟧ had the larger insert. How many tiles were packed in this run?",
        "(3*8-4)*(3+(5-3)*x)", "1/4", ("1/5", "2/5"), "70",
        "x is the share of this run's usable cartons with the five-tile insert. Four or eight such cartons out of twenty are both possible when the practice run is the only one described."),
    Arithmetic(
        "splice", "ja", "overlap_and_units",
        "影絵用に11分と9分の音源をつなぎ、一方の末尾と他方の冒頭を重ねた。重なった部分は上映時間に一度だけ含める。作業日誌にある45秒は⟦今回のつなぎ目|前作のつなぎ目⟧で重ねた時間である。さらに冒頭に30秒の挨拶を足した。会場ではこれを3回上映する。挨拶を含む1回分の音声の長さは何分か。1分は60秒とする。",
        "11+9-x/60+30/60", "45", ("15", "60"), "19.75",
        "x is the overlap duration in seconds for the current splice. Both 15 and 60 seconds fit into each source recording. The number of screenings does not enter the length per screening."),
    Arithmetic(
        "tickets", "en", "exclude_duplicate",
        "Five slips at a cloakroom showed waits of 4, 7, 9, 11 and 13 minutes. Exactly one was an extra copy of a visit already filed elsewhere. Each of the other four slips represented a new visit. The clerk circled the 7-minute slip ⟦to identify the extra copy|to test whether the pencil wrote on that paper⟧. What was the mean wait of the four new visits, in minutes?",
        "(4+7+9+11+13-x)/4", "7", ("4", "9"), "9.25",
        "x is the wait on the duplicate slip. Its original is outside the displayed group. In the missing version either the 4-minute slip or the 9-minute slip could be that duplicate."),
    Arithmetic(
        "stencil", "ja", "fraction_of_fraction",
        "色紙全体の2/3を青く塗り、その青い部分の3/8を銀色に塗り替えた。最後に銀色の一部を青に戻した。作業帳の「銀色の半分を青に戻した」という記述は⟦この色紙|隣に置いた見本紙⟧についてだった。最後に銀色で残った面積は、色紙全体の何パーセントか。",
        "100*(2/3)*(3/8)*(1-x)", "1/2", ("1/4", "3/4"), "12.5",
        "x is the fraction of the target sheet's silver that was recoloured. One quarter and three quarters are both strictly between zero and one, consistent with recolouring part of it."),
    Arithmetic(
        "badges", "en", "inclusion_exclusion",
        "Of the 31 visitors leaving a screening, 18 took a map and 16 took a badge. A person could take both or neither. The same group included 7 people ⟦who took both items|who watched the map-folding demonstration⟧. A box of 12 unused badges stayed under the counter. How many of the 31 visitors took neither item?",
        "31-18-16+x", "7", ("4", "8"), "4",
        "x is the number taking both a map and a badge. Overlaps of 4 and 8 give valid nonnegative counts for all four groups. Attendance at the demonstration places no constraint on who took the items."),
    Arithmetic(
        "stacks", "ja", "internal_transfers_zero",
        "印刷室に普通紙の山が二つある。朝に一方から他方へ18枚移し、昼に逆向きに11枚移した。夕方に二つの山から合わせて6枚を別室へ送り、別室から届いた一束を山に加えた。「6枚入り」という控えは⟦夕方に届いたその束|翌日用に包んだ別の束⟧のものだった。この間、ほかに紙の出入りはない。朝の移動前から夕方の受取り後までに二つの山の合計枚数は何枚増減したか。増加を正、減少を負の値で表す。",
        "-18+18+11-11-6+x", "6", ("3", "9"), "0",
        "x is the incoming bundle size. Both internal transfers cancel in the total. Bundles of three or nine sheets remain possible when the six-sheet description is about the following day."),
)


def _exact(expression, x):
    operators = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
                 ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}

    def visit(node):
        if isinstance(node, ast.Name) and node.id == "x":
            return Fraction(x)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Fraction(ast.get_source_segment(expression, node))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -visit(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return operators[type(node.op)](visit(node.left), visit(node.right))
        raise ValueError(f"Unsupported oracle expression {expression!r}")

    return visit(ast.parse(expression, mode="eval").body)


def calc_cases():
    """Return 16 arithmetic cases. Only the question is sent to a model."""
    cases = []
    for spec in ARITHMETIC:
        for positive in (True, False):
            assignments = (spec.x,) if positive else spec.alternatives
            witnesses = [{"x": value, "result": str(_exact(spec.expression, value))}
                         for value in assignments]
            cases.append({"id": f"calc-{spec.pair}-{'p' if positive else 'm'}",
                          "pair": spec.pair, "language": spec.language, "group": spec.group,
                          "positive": positive, "question": _arms(spec.prompt, positive),
                          "expected": spec.expected if positive else None,
                          "oracle": {"expression": spec.expression, "witnesses": witnesses,
                                     "rationale": spec.rationale}})
    return cases


def validate(lengths=None):
    """Check matched edits, unique sources and IDs, balance, offsets and solvability."""
    qa = qa_cases(lengths)
    assert len(qa) == len({r["id"] for r in qa}) == len({r["job"]["source"] for r in qa}) == 24
    assert Counter((r["tier"], r["language"], r["positive"]) for r in qa) == {
        (tier, language, polarity): 3 for tier in ("8k", "16k")
        for language in ("ja", "en") for polarity in (True, False)}
    for book, p, m in zip(NOTEBOOKS, qa[::2], qa[1::2]):
        assert p["pair"] == m["pair"] == book.pair
        assert p["job"]["question"] == m["job"]["question"]
        assert p["filler_count"] == m["filler_count"]
        assert set(p["job"]) == set(m["job"]) == {"source", "question", "task"}
        assert p["job"]["task"] == m["job"]["task"] == "answer"
        a, b = p["job"]["source"].encode(), m["job"]["source"].encode()
        (pa, pb), = p["change_spans"]
        (ma, mb), = m["change_spans"]
        assert pa == ma and a[:pa] == b[:ma] and a[pb:] == b[mb:]
        assert a[pa:pb] != b[ma:mb]
        assert a[pa:pb].decode() == _arms(book.decision, True).format(value=book.value)
        assert b[ma:mb].decode() == _arms(book.decision, False).format(value=book.value)
        (ga, gb), = p["gold_spans"]
        assert pa <= ga < gb <= pb and a[ga:gb].decode() == p["gold"] == book.value
        assert m["gold"] is None and m["gold_spans"] == []
        shared = a[:pa] + a[pb:]
        assert shared.count(book.value.encode()) >= 2 and shared.count(book.distractor.encode()) >= 2
        for doc in (a, b):
            assert all(marker.encode() not in doc for marker in ("{value}", "⟦", "⟧"))

    calc = calc_cases()
    assert len(calc) == len({c["id"] for c in calc}) == len({c["question"] for c in calc}) == 16
    assert Counter((c["language"], c["positive"]) for c in calc) == {
        (language, polarity): 4 for language in ("en", "ja") for polarity in (True, False)}
    for spec, p, m in zip(ARITHMETIC, calc[::2], calc[1::2]):
        assert p["pair"] == m["pair"] == spec.pair
        assert p["question"] != m["question"]
        assert re.findall(r"\d+", p["question"]) == re.findall(r"\d+", m["question"])
        assert m["expected"] is None and Fraction(p["expected"]) == _exact(spec.expression, spec.x)
        witnesses = m["oracle"]["witnesses"]
        assert len(witnesses) == 2 and len({w["result"] for w in witnesses}) == 2
    return {"qa_cases": len(qa), "qa_pairs": len(NOTEBOOKS),
            "calc_cases": len(calc), "calc_pairs": len(ARITHMETIC)}


if __name__ == "__main__":
    print(validate())
