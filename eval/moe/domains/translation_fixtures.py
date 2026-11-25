"""Predeclared, controlled bilingual notices; both directions and document-level obligations."""
# Fixture records, not a translation dictionary exposed to the repairer.
# First clauses use genuinely distinct strict, inclusive, exact and change-versus-level meanings.
LEVEL = {
    "max": ("{name} limits the {term} to at most {v} {u}.","{name} は{term}を {v} {u} 以下に制限する。"),
    "min": ("For {name}, the {term} must be at least {v} {u}.","{name} では{term}が {v} {u} 以上でなければならない。"),
    "lt": ("At {name}, the {term} must stay strictly below {v} {u}.","{name} では{term}を {v} {u} 未満に保つ必要がある。"),
    "gt": ("At {name}, the {term} must strictly exceed {v} {u}.","{name} では{term}が {v} {u} を超えなければならない。"),
    "eq": ("{name} sets the {term} to exactly {v} {u}.","{name} は{term}をちょうど {v} {u} に設定する。"),
    "up": ("{name} increased the {term} by {v} {u}.","{name} は{term}を {v} {u} だけ引き上げた。"),
    "down": ("{name} reduced the {term} by {v} {u}.","{name} は{term}を {v} {u} だけ引き下げた。"),
    "approx": ("{name} estimates that the {term} is approximately {v} {u}.","{name} は{term}をおよそ {v} {u} と見積もっている。"),
}
ACTION = {
    "keep": ("Keep the {asset} for {v} h after the inspection.","検査後 {v} h は{asset}を保管してください。"),
    "discard": ("Discard the {asset} {v} h after the inspection.","検査の {v} h 後に{asset}を廃棄してください。"),
    "never": ("Do not open the {asset} for {v} h after delivery.","納品後 {v} h は{asset}を開けないでください。"),
    "before": ("Close the {asset} {v} h before departure.","出発の {v} h 前に{asset}を閉じてください。"),
    "after": ("Open the {asset} {v} h after arrival.","到着の {v} h 後に{asset}を開けてください。"),
    "remain": ("The {asset} must remain sealed for {v} h after packing.","梱包後 {v} h は{asset}を密封したままにしてください。"),
    "defer": ("Postpone the {asset} for {v} h after the inspection.","検査後の{asset}を {v} h 延期してください。"),
    "early": ("Start the {asset} {v} h before the inspection.","検査の {v} h 前に{asset}を開始してください。"),
}
# Instrument, controlled term (English / Japanese), level, numeric literal, unit,
# second glossary entry, second obligation and independent duration.
DEV = [
    ("Luma","nozzle pressure","ノズル圧","max","2.5","kPa","reserve vial","予備瓶","never","8"),
    ("Arden","line speed","搬送速度","gt","14","mm/s","service hatch","点検口","before","2"),
    ("Cevin","sample mass","試料質量","eq","7.25","g","blue cartridge","青色カートリッジ","keep","18"),
    ("Merro","signal delay","信号遅延","lt","12","ms","outer cover","外カバー","after","1.5"),
    ("Viola","coating depth","塗膜深さ","up","0.8","mm","trial run","試運転","defer","4"),
    ("Felsen","return flow","戻り流量","min","3.4","L/min","sample pouch","試料袋","remain","6"),
    ("Ordo","bath temperature","浴温","approx","-2.5","°C","sensor cap","センサー蓋","discard","24"),
    ("Nemi","reserve volume","予備容量","down","6","mL","warmup cycle","予熱工程","early","0.5"),
]
TEST = [
    ("Salta","return pressure","戻り圧","min","4.8","kPa","witness vial","立会瓶","keep","9"),
    ("Belin","guide width","ガイド幅","lt","27","mm","transfer gate","移送ゲート","before","3"),
    ("Toren","dry mass","乾燥質量","down","1.75","g","filter pouch","フィルター袋","remain","12"),
    ("Avra","rinse volume","すすぎ容量","eq","35","mL","shipping cap","輸送キャップ","after","1"),
    ("Mistra","peak delay","ピーク遅延","max","8.5","ms","audit cycle","監査工程","defer","2.5"),
    ("Falen","pulse rate","パルス周波数","gt","0.75","Hz","backup bottle","控え瓶","never","16"),
    ("Navis","initial load","初期荷重","up","1,250","N","purge cycle","排気工程","early","4"),
    ("Aurin","return temperature","戻り温度","approx","-7","°C","outer tray","外トレー","discard","30"),
    ("Kelso","mesh spacing","網目間隔","lt","0.45","mm","sample drawer","検体引出し","after","6"),
    ("Vanta","line current","線電流","min","3.25","mA","spare fuse","予備ヒューズ","keep","48"),
    ("Corda","feed volume","供給容量","eq","0.5","L","drain port","排出口","before","0.25"),
    ("Pireo","heating time","加熱時間","max","125","s","heat shield","遮熱板","never","1.25"),
    ("Elva","sound level","騒音レベル","down","3.5","dB","cooling run","冷却運転","defer","3"),
    ("Rosen","angular speed","角速度","gt","17","rad/s","relay cover","リレー蓋","remain","7"),
    ("Kasel","tank volume","タンク容量","up","24","L","retained bottle","保管瓶","discard","72"),
    ("Maika","sample temperature","試料温度","approx","-12.5","°C","test sequence","試験手順","early","2"),
    ("Vera","packing force","圧着力","min","62","N","alignment jig","位置決め治具","keep","10"),
    ("Finch","alarm delay","警報遅延","lt","0.09","s","locking flap","固定ふた","before","0.75"),
    ("Sera","coating width","塗布幅","eq","14.25","mm","nozzle case","ノズルケース","after","5"),
    ("Yura","outlet flow","出口流量","max","7.6","L/min","waste pack","廃液パック","discard","14"),
    ("Ansel","test voltage","試験電圧","gt","1.8","V","protective case","保護ケース","never","36"),
    ("Sargo","belt velocity","ベルト速度","down","12","mm/s","seal check","封印確認","defer","2"),
    ("Miro","tare mass","風袋質量","approx","62.5","g","transfer pouch","搬送袋","remain","20"),
    ("Oriel","hose length","ホース長","up","3.75","m","setup cycle","準備工程","early","1"),
    ("Liven","holding time","保持時間","min","25","s","reserve tube","予備管","keep","28"),
    ("Perta","probe offset","プローブ偏差","lt","-0.5","mm","service panel","整備パネル","before","4.5"),
    ("Safir","working pressure","作動圧","eq","6.75","kPa","valve cover","バルブ蓋","after","8"),
    ("Welda","reverse current","逆電流","max","0.4","mA","inspection pack","検査袋","remain","32"),
    ("Geila","dose volume","投与容量","down","2.25","mL","dummy cycle","模擬工程","defer","0.5"),
    ("Hesto","cycle frequency","周期周波数","gt","50","Hz","transit cover","運搬カバー","never","6"),
]


def make(split):
    result = []
    for i, row in enumerate(DEV if split=="dev" else TEST):
        name,en,ja,level,v,u,asset_en,asset_ja,action,h = row
        en1,ja1 = LEVEL[level]
        en2,ja2 = ACTION[action]
        english = [en1.format(name=name,term=en,v=v,u=u),en2.format(asset=asset_en,v=h)]
        japanese = [ja1.format(name=name,term=ja,v=v,u=u),ja2.format(asset=asset_ja,v=h)]
        en_source = (i + (split=="test")) % 2 == 0
        original, target = (english,japanese) if en_source else (japanese,english)
        glossary = {en:ja,asset_en:asset_ja} if en_source else {ja:en,asset_ja:asset_en}
        job = dict(source_language="en" if en_source else "ja", target_language="ja" if en_source else "en",
                   sentences=original, glossary=glossary, names=[name], units=[u,"h"])
        result.append(dict(id=f"translation-{split}-{i+1:02}",group="translation",job=job,
                           gold=dict(level=level,action=action,reference=target)))
    return result
