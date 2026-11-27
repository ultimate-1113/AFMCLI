"""Matched, original long-document QA fixtures and an independent scorer.

Only ``case['job']`` is model input. Offsets are half-open bytes of that exact
UTF-8 source. ``make('dev'|'test')`` loads only the requested split, without a
model or tokenizer. ``followup(case)`` appends only the user's disambiguation.
A spec in qa_sources consists of language, size tier, file title, question,
optional disambiguation, and annotated documents entries. A third field on one
entry is the unsettled replacement for the settled version. A and B markers
identify single authoritative occurrences and are stripped before rendering.

Coverage contains whole primary, competing, and state-changing source records,
plus the changed passage and all accepted evidence. Full coverage of an entry
can be tested against any union of ranges the actual model received. A positive
can have a competing record earlier or later than its authority. A valid null
requires an explicit abstention; a held, refused, or failed job is tracked
separately. The existence of a returned value is independent of its status.
"""

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import runpy
import unicodedata


HERE = Path(__file__).resolve().parent
SOURCES = HERE / 'qa_sources'
TARGETS = {'4K': 4096, '8K': 8192, '16K': 16384}
MARKER = re.compile(r'\[\[([AB])\|([^\n\[\]]+)\]\]')

# Split-specific publishing/handling notes make up the bound register outside
# the authored evidence. Every index has its own entry and shares no mutable RNG
# state with any other case. Edition choices cannot change the surrounding file.
EN = {
    'test': (
        ('backing sheet', 'case clasp', 'paper hinge', 'sample mount', 'tracing strip', 'cord loop', 'index frame', 'linen guard', 'sleeve seam', 'packing wedge', 'display foot', 'spacer ring', 'foam collar', 'tissue cover', 'drawer runner', 'hinge pin'),
        ('window bench', 'east cabinet', 'round table', 'upper workroom', 'visitor desk', 'glazing rack', 'dry store', 'repair table', 'map room', 'south cabinet', 'inspection bench', 'annex shelf'),
        ('graphite', 'pale blue pencil', 'violet ink', 'soft charcoal', 'brown crayon', 'red pencil', 'black ink', 'green pencil'),
        ('The {obj} was sketched at the {site} before the packing was folded. A line in {ink} shows which side of the sketch faced the work surface.',
         'From the {site} came a duplicate photograph of the {obj}. The contact print was trimmed, then marked in {ink} on the reverse for the photographic file.',
         'When the {obj} arrived at the {site}, a clerk traced its outline. The mark in {ink} belongs to the tracing paper and is visible at the left edge.',
         'A measurement of the {obj} taken at the {site} was copied into the workshop notebook. The assistant drew a second line in {ink} beside the ruler in the margin.',
         'During shelf cleaning the {obj} was set aside at the {site}. An arrow in {ink} on the cleaning plan records where the loose wrapping was found.',
         'The separate packing survey illustrates the {obj} on the {site} inventory. A corner drawn in {ink} identifies the back of the packaging in that survey.',
         'After the {obj} was photographed at the {site}, its tissue was replaced. The photographic envelope has a short registration line in {ink} along its flap.',
         'An assistant examined the {obj} under a reading lamp at the {site}. A trace in {ink} on this scrap records the placement of the lamp, for the photographer.',
         'The workshop retained a scale sketch of the {obj} from the {site}. The lower part, ruled in {ink}, gives the width of the paper used for the sketch.',
         'A loose sheet from the {site} accompanies the {obj}. Its left margin is marked in {ink}; the reference is to the folded sheet itself.'),
        ('File maintenance for {title}. The register was laid open at the {site} while the {obj} was photographed. The photographic entry concerns the storage enclosure.',
         'A handling note filed with {title} describes a {obj} at the {site}. The note accompanied a repair to the binding of the register.',
         'The binder inspected {title} at the {site} and drew the {obj} on a separate sheet. That sheet lists materials for the physical cover.')),
    'dev': (
        ('hinged board', 'felt pad', 'wooden shim', 'tie ribbon', 'measuring rule', 'rolled sheet', 'sample tray', 'cloth flap', 'blank ticket', 'reference card', 'mounting block', 'envelope seal'),
        ('delivery counter', 'upper landing', 'drying loft', 'survey desk', 'storage bay', 'lamp cabinet', 'dispatch bench', 'drafting room', 'returns shelf', 'print room'),
        ('violet pencil', 'indigo ink', 'ochre pencil', 'black chalk', 'sepia ink', 'blue chalk'),
        ('At the {site}, the copyist drew an outline of a {obj} in {ink}. A new sheet separated the drawing from the carbon impression underneath.',
         'The stores list at the {site} includes a {obj}. Staff circled the line in {ink} because a second copy of this stores list was already on file.',
         'A {obj} was put on the {site} for measurement. The draughtsperson ruled a baseline in {ink} and left room for a scale beside the drawing.',
         'For the {site} inventory a {obj} was traced on a folded sheet. The tick in {ink} refers to the tracing, which has been removed to a plan drawer.',
         'A volunteer at the {site} made a paper model of the {obj}. The paper model has its fold lines picked out in {ink} and sits in the teaching collection.',
         'An impression of the {obj} taken at the {site} fills the lower leaf. A border in {ink} was applied after the ink of the impression dried.',
         'Before moving the {obj} from the {site}, a stores clerk measured its packing paper. The length of that paper appears in {ink} on the stores copy.'),
        ('Binding note for {title}. The copy at the {site} was held open with a {obj} while loose leaves were counted for repair.',
         'A photograph of the binding of {title} shows the {obj} kept at the {site}. The photograph went to the register conservator.')),
}

JA = {
    'test': (
        ('当て紙', '箱の留め金', '台紙の蝶番', '見本台', '敷物の縁', '引き出しの桟', '見出し枠', '麻の保護布', '袋の縫い目', '板の足', '薄紙の覆い', '輪形の座金', '木のくさび', '仕切り板', '紙の取っ手', '隅の押さえ'),
        ('窓際の作業台', '東の書架', '円卓', '上の作業室', '閲覧机', '乾燥棚', '南の戸棚', '修理台', '図面室', '検品机', '別棟の棚', '採寸台'),
        ('青鉛筆', '薄墨', '紫のインク', '木炭', '茶の色鉛筆', '赤鉛筆', '墨', '緑鉛筆'),
        ('{site}で{obj}の輪郭を写し取った。作業面に触れていた側がわかるように、写しの端に{ink}で線を加えた。',
         '写真係が{site}で{obj}を撮った。密着焼きの余白を切りそろえ、写真控えの裏に{ink}で印を入れた。',
         '{obj}が{site}に届いたので、透き紙を重ねて形を写した。左端の{ink}の線は透き紙の側につけたものである。',
         '{site}で調べた{obj}の寸法を工作控えに写した。控えの余白には物差しを置いた方向が{ink}で引いてある。',
         '棚を拭く間、{obj}を{site}へ移した。掃除の見取図にある{ink}の矢印は、はずした包み紙が置かれていた位置を示す。',
         '荷造りの調査員が{site}にあった{obj}を図に残した。図の隅にある{ink}の印は、包みの裏表を区別するためのものである。',
         '{site}で{obj}を撮影してから包み直した。撮影控えを入れた封筒のふたには照合用に{ink}で短い線を引いた。',
         '係員は{site}に置いた{obj}に読書灯を当てて点検した。別紙に{ink}で書いたのは撮影のための照明位置である。',
         '{site}から運んだ{obj}の実測図は工作室の控えに残した。図の下を横切る{ink}の線は製図用紙の幅を表す。',
         '{site}で見つかった折り紙を{obj}と一緒に受け入れた。折り紙自体の左余白に{ink}で書き込みがある。'),
        ('「{title}」の綴じ直し。{site}に広げた帳面と{obj}を撮影した。保存用の外装に関する撮影控えである。',
         '「{title}」に添えた扱い書には、{site}で点検した{obj}の図がある。この扱い書は帳面の修理の際に綴じたもの。',
         '製本係が{site}で「{title}」を点検し、別紙に{obj}の図を描いた。別紙は表紙の補修材料の控えに入れた。')),
    'dev': (
        ('折り板', '毛氈の端', '木の当て物', '飾り紐', '目盛りの板', '丸めた紙', '見本皿', '布の垂れ', '白い荷札', '控え札', '台木', '封かん紙'),
        ('納品口', '階段の踊り場', '乾かし場', '調査机', '保管区画', '照明棚', '発送用の台', '下図室', '戻り品の棚', '印刷室'),
        ('紫鉛筆', '藍のインク', '黄土色の鉛筆', '黒いチョーク', 'セピアのインク', '青いチョーク'),
        ('{site}で写し手が{obj}の外形を{ink}で描き写した。新しい用紙を間に入れ、下にある複写紙の跡から分けて保管した。',
         '{site}で使った備品表に{obj}の記載がある。同じ備品表の控えがもう一枚見つかったので、係員が行頭を{ink}で囲んだ。',
         '採寸のため{obj}を{site}へ運んだ。図を引く係は{ink}で基準線を一本引き、脇に縮尺を記入する余白を残した。',
         '{site}の棚卸しでは折った紙に{obj}を写した。紙にある{ink}の印は、写した図を図面入れに移した際の照合印である。',
         '手伝いの人が{site}で{obj}の紙模型を作った。模型の折り線を{ink}でなぞってから、手習い用の見本入れに加えた。',
         '{site}で採った{obj}の拓本が見開きの下半分にある。拓本をよく乾かしてから{ink}で枠を引いた。',
         '{obj}を{site}から移す前に備品係が包み紙を測った。包み紙の長さだけを{ink}で備品の控えに書いた。'),
        ('「{title}」の補修控え。{site}で欠けた紙葉を数える間、{obj}を使って帳面の見開きを押さえた。',
         '「{title}」の背表紙を撮った写真には、{site}で使う{obj}も写り込んだ。製本修理係が写真を引き取った。')),
}


@lru_cache(maxsize=2)
def specifications(split):
    if split not in ('dev', 'test'):
        raise ValueError('split must be dev or test')
    data = runpy.run_path(str(SOURCES / (split + '.py')))
    return data['SPECS'], data.get('SETTLED_MARKERS', {})


def filler(split, index, count, title, language):
    bank = (EN if language == 'en' else JA)[split]
    seed = int.from_bytes(sha256(f'qa bound file 3/{split}/{index}'.encode()).digest()[:8], 'big')
    rng = random.Random(seed)
    names, sites, media, prose, topical = bank
    result = []
    for number in range(1, count + 1):
        phrase = rng.choice(topical if number % 13 == index % 13 else prose)
        wording = phrase.format(obj=rng.choice(names), site=rng.choice(sites), ink=rng.choice(media), title=title)
        prefix = f'Leaf {number:03d}. ' if language == 'en' else f'綴り{number:03d}　'
        result.append(prefix + wording)
    return result


def render_record(template):
    marks, parts, cursor, length = {}, [], 0, 0
    for match in MARKER.finditer(template):
        literal = template[cursor:match.start()]
        parts.append(literal)
        length += len(literal.encode())
        key, value = match.groups()
        if key in marks:
            raise ValueError('duplicate location marker in a record')
        stop = length + len(value.encode())
        marks[key] = (value, length, stop)
        parts.append(value)
        length, cursor = stop, match.end()
    parts.append(template[cursor:])
    return ''.join(parts), marks


def case_key(split, index):
    return f'qa-{split}-{index + 1:02d}'


def build_pair(split, index, count, token_counts=(None, None)):
    """Render a shared filler recipe for calibration or independent reproduction."""
    specs, settled_markers = specifications(split)
    language, tier, title, question, reply_spec, records = specs[index]
    notes = filler(split, index, count, title, language)
    # Fixed distinct neighborhoods while varying which portion of each file
    # contains its first/last substantive material. No answer position is fixed.
    seed = int.from_bytes(sha256(f'qa inserts 2/{split}/{index}'.encode()).digest()[:8], 'big')
    rng = random.Random(seed)
    # Draw one location per stratum. Using proportions keeps calibration smooth.
    points = [int(count * ((i + rng.uniform(.04, .96)) / len(records))) for i in range(len(records))]
    inserted = dict(zip(points, records))
    if len(inserted) != len(records):
        raise ValueError('insufficient filler for pair')
    pair = case_key(split, index)
    answer_key = settled_markers.get(index, 'A')
    cases = []
    for variant, positive in enumerate((True, False)):
        head = f'{title}\n\n'
        parts, pos = [head], len(head.encode())
        coverage = {k: [] for k in ('primary', 'alternative', 'state_changes', 'pivot', 'gold_support')}
        marked, support = {}, {}
        for i, note in enumerate(notes):
            if i in inserted:
                row = inserted[i]
                changed = len(row) == 3
                body, locals_ = render_record(row[2] if changed and not positive else row[1])
                stop = pos + len(body.encode())
                for role in row[0].split():
                    if role != 'neutral':
                        coverage['state_changes' if role == 'state' else role].append([pos, stop])
                if changed:
                    coverage['pivot'].append([pos, stop])
                for key, (value, a, b) in locals_.items():
                    if key in marked:
                        raise ValueError('nonunique location marker')
                    marked[key] = {'value': value, 'spans': [{'start_byte': pos + a, 'end_byte': pos + b}]}
                    support[key] = [pos, stop]
                parts.append(body + '\n')
                pos = stop + 1
            parts.append(note + '\n')
            pos += len(note.encode()) + 1
        if any(not coverage[role] for role in ('primary', 'alternative', 'state_changes')) or len(coverage['pivot']) != 1:
            raise ValueError('incomplete evidence roles or unmatched pair')
        gold = marked[answer_key] if positive else None
        if positive:
            coverage['gold_support'] = [support[answer_key]]
            if reply_spec and support[answer_key] in coverage['alternative']:
                coverage['primary'], coverage['alternative'] = coverage['alternative'], coverage['primary']
        askable = bool(reply_spec) and not positive
        suffixes = ('a', 'b') if seed % 2 == 0 else ('b', 'a')
        case = {'id': f'{pair}-{suffixes[variant]}', 'pair': pair, 'tier': tier, 'language': language,
                'job': {'task': 'answer', 'question': question, 'source': ''.join(parts)},
                'gold': gold, 'positive': positive, 'askable': askable, 'reply': None,
                'coverage': coverage, 'source_tokens': token_counts[variant], 'filler_count': count}
        if askable:
            kind, reply, key, terms, choice1, choice2 = reply_spec
            case.update(reply=reply, clarification_dimension=kind,
                        clarification_terms=[list(terms), list(choice1), list(choice2)],
                        reply_gold=marked[key], reply_support=support[key])
        cases.append(case)
    return cases


def make(split):
    specs, _ = specifications(split)
    lengths = json.loads((SOURCES / (split + '_lengths.json')).read_text(encoding='utf-8'))
    if len(lengths) != len(specs):
        raise ValueError('incomplete frozen length recipe')
    cases = []
    for i in range(len(specs)):
        recipe = lengths[case_key(split, i)]
        pair = build_pair(split, i, recipe['filler_count'], recipe['source_tokens'])
        if len(pair) != len(recipe['source_sha256']) or len(pair) != len(recipe['source_tokens']):
            raise ValueError('missing calibrated variant')
        for case, checksum in zip(pair, recipe['source_sha256']):
            if sha256(case['job']['source'].encode()).hexdigest() != checksum:
                raise ValueError(f"frozen source changed for {case['id']}")
        cases.extend(pair)
    return cases


def followup(case):
    if not case['askable']:
        raise ValueError('this case has no resolving user reply')
    result = dict(case, id=case['id'] + '-reply', positive=True, askable=False, is_followup=True,
                  gold=case['reply_gold'], reply=None)
    result['job'] = dict(case['job'], question=case['job']['question'] + '\n' + case['reply'])
    result['coverage'] = dict(case['coverage'], gold_support=[case['reply_support']])
    cover = result['coverage']
    if case['reply_support'] in cover['alternative']:
        cover['primary'], cover['alternative'] = cover['alternative'], cover['primary']
    for key in ('clarification_dimension', 'clarification_terms', 'reply_gold', 'reply_support'):
        result.pop(key)
    return result


def normalized(s):
    return ' '.join(unicodedata.normalize('NFKC', s).casefold().split())


def contains(text, term):
    term = normalized(term)
    if term.isascii() and term[:1].isalnum() and term[-1:].isalnum():
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))
    return term in text


# A focused polar question also resolves a binary ambiguity. Broad requests for
# more context, guesses at the answer, multiple follow-ups, and restatements of
# the original question receive no question credit.
ASK_EN = re.compile(r'\b(?:when|which|what|whether|was|were|is|are|did|do|does|can|could|would|please (?:specify|identify|indicate|confirm|tell))\b')
ASK_JA = re.compile(r'どち|いつ|何|どの|どれ|いずれ|ですか|ますか|でしょうか|ください|教えて|確認|示して|お願い|尋ね')
AXIS = {
    ('date', 'en'): r'\b(?:when|which (?:year|season|period|edition|run|issue)|what (?:year|date|period|season)|date of|dated|time period|date range|which of the two (?:dates|editions|runs|periods))\b',
    ('date', 'ja'): r'いつ|年代|時期|何年|何月|どの年|どちらの年|どの版|どちらの版|何版|どの時点|年号|刊年|年次|年月|日付',
    ('target', 'en'): r'\b(?:which|what)\s+(?:(?:of (?:the |these )?(?:two )?)?)(?:model|collection|version|type|kind|size|width|subcategory|variant|jig|cylinder|item|group)\b',
    ('target', 'ja'): r'ど(?:の|ちらの)(?:方の|班|組|桟橋|乗り場|場所|部門|持ち主)|所属|どこで(?:拾|見つ)|誰(?:が|の)|どなた|役(?:割|方)',
    ('condition', 'en'): r'\b(?:in what (?:condition|state)|what (?:was|is) the (?:condition|state)|what condition|how (?:wet|damp|dry)|whether (?:the )?(?:brace|screens))\b',
    ('condition', 'ja'): r'(?:どんな|どのような|どの|どういう).{0,8}状態|(?:受取|受け取|引渡|搬出|運搬|持込|運ぶ).{0,8}時.{0,6}状態',
}


def focused_question(case, question):
    if not case['askable'] or not isinstance(question, str):
        return False
    q = normalized(question)
    if not q or len(q) > 430 or q.count('?') > 1 or q.rstrip('?。') == normalized(case['job']['question']).rstrip('?。'):
        return False
    language = case['language']
    clauses = [part.strip() for part in re.split(r'[.!?。\n]+', q) if part.strip()]
    if not clauses:
        return False
    focus = clauses[-1]
    if not (ASK_EN if language == 'en' else ASK_JA).search(focus):
        return False
    if language == 'en' and '?' not in q and not re.match(r'(?:please|can|could|would|which|what|when|was|were|is|are|did|do|does)\b', focus):
        return False
    if language == 'en' and re.search(r'\band\s+(?:also\s+)?(?:when|which|why|where|how|what)\b', q):
        return False
    if language == 'ja' and re.search(r'(?:さらに|加えて|併せて|あわせて).{0,12}(?:教えて|何|どの|いつ|どちら)', q):
        return False
    # A menu asking for several independent inputs is not a single targeted
    # question. Condition at a particular time still asks for just one input.
    axes = (r'\b(?:date|year|edition)\b|日付|時点|年次',
            r'\b(?:target|entity|subject)\b|対象|主体',
            r'\b(?:condition|circumstances)\b|条件|状態')
    if sum(bool(re.search(axis, focus)) for axis in axes) >= 2 and re.search(r'\bor\b|\band\b|または|ないし|或いは|もしくは|、|／|/', focus):
        return False
    terms, first, second = case['clarification_terms']
    # A final anaphoric question can refer to alternatives in its preceding
    # sentence. Otherwise a backstory mentioning a choice is not itself a query.
    local = focus
    if re.search(r'\b(?:which (?:one|of (?:them|these|the two))|which do you mean|is that (?:right|correct))\b|どちら|いずれ', focus):
        local = q
    one = any(contains(local, word) for word in first)
    two = any(contains(local, word) for word in second)
    if one and two:
        return True
    dimension = case['clarification_dimension']
    if re.search(AXIS[dimension, language], focus):
        return True
    if language == 'en':
        if re.search(r'\b(?:what|which)\s+(?:imprint|caption|answer|value|wording|label|stamp|stop name|heading|mark|shelf.prefix)\b', focus):
            return False
        polar = re.search(r'\b(?:do you mean|(?:was|were|is|are|did|does) (?:it|this|that|the |your )|whether|confirm|referring to|concern(?:s|ing)?)\b', focus)
    else:
        polar = re.search(r'ですか|ますか|でしょうか|という意味|指して|確認|よろしい|でしょう|なのか', focus)
    if (one or two) and polar:
        return True
    return any(contains(focus, word) for word in terms)


def valid_answer(case, answer):
    gold = case['gold']
    if not gold or not isinstance(answer, dict) or answer.get('value') != gold['value']:
        return False
    spans = answer.get('spans')
    if not isinstance(spans, list) or not spans:
        return False
    raw = case['job']['source'].encode()
    allowed = {(s['start_byte'], s['end_byte']) for s in gold['spans']}
    for span in spans:
        if not isinstance(span, dict):
            return False
        a, b = span.get('start_byte'), span.get('end_byte')
        if type(a) is not int or type(b) is not int or (a, b) not in allowed:
            return False
        if not (0 <= a < b <= len(raw)) or raw[a:b] != gold['value'].encode():
            return False
    return True


def grade(case, record, mode):
    """Score only public output. Correct-null and clarification are independent."""
    status = record.get('status')
    result = record.get('result')
    has_answer = isinstance(result, dict) and 'answer' in result
    answer = result.get('answer') if has_answer else None
    answered = answer is not None
    question = result.get('clarification') if isinstance(result, dict) else None
    any_question = isinstance(question, str) and bool(question.strip())
    explicit_null = status == 'abstained' and has_answer and not answered
    right_question = explicit_null and any_question and focused_question(case, question)
    delivery_status = status == 'ok' or (mode == 'preview' and status == 'review')
    located = delivery_status and answered and valid_answer(case, answer)
    false_nonnull = not case['positive'] and answered
    correct_null = not case['positive'] and explicit_null
    correct = located or correct_null
    return {'correct': correct, 'answered': answered, 'serious': answered and not located,
            'correct_null': correct_null, 'false_nonnull': false_nonnull,
            'right_question': right_question, 'any_question': any_question,
            'wrong_question': any_question and not right_question,
            'unnecessary_question': any_question and not case['askable'],
            'right_followup': bool(case.get('is_followup')) and located,
            'held': status == 'held', 'error': status == 'error', 'refused': status == 'refused'}


def retrieval_coverage(case, sent_ranges):
    """Return counts fully present in the union of sent original UTF-8 ranges.

    Call with the excerpts from each generation separately to distinguish one
    complete reading from fragments spread over unrelated calls. Repeated ranges
    and adjacent or overlapping excerpts do not change the result.
    """
    size = len(case['job']['source'].encode())
    ordered = []
    for item in sent_ranges:
        if isinstance(item, dict):
            a, b = item['start_byte'], item['end_byte']
        else:
            a, b = item
        if type(a) is not int or type(b) is not int or not (0 <= a < b <= size):
            raise ValueError('invalid retrieved original-byte range')
        ordered.append((a, b))
    union = []
    for a, b in sorted(ordered):
        if union and a <= union[-1][1]:
            union[-1][1] = max(union[-1][1], b)
        else:
            union.append([a, b])
    return {key: {'seen': sum(any(a <= start and stop <= b for a, b in union) for start, stop in spans),
                  'total': len(spans)} for key, spans in case['coverage'].items()}
